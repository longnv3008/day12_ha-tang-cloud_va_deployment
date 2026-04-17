"""
Production AI Agent — Kết hợp tất cả Day 12 concepts

Checklist:
  Config tu environment (12-factor)          app/config.py
  Structured JSON logging                    request_middleware
  API Key authentication                     app/auth.py -> verify_api_key
  Rate limiting (sliding window)             app/rate_limiter.py -> check_rate_limit
  Cost guard (daily budget)                  app/cost_guard.py -> check_budget
  Input validation (Pydantic)                AskRequest / AskResponse
  Health check + Readiness probe             GET /health, GET /ready
  Graceful shutdown (SIGTERM)                lifespan + signal.SIGTERM
  Stateless design (conversation in Redis)   _get_history / _save_history
  Security headers                           request_middleware
  CORS                                       CORSMiddleware
"""
import time
import json
import signal
import logging
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, record_cost, get_daily_cost
from utils.mock_llm import ask as llm_ask

# ─────────────────────────────────────────────────────────
# Logging — JSON structured
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0

# ─────────────────────────────────────────────────────────
# Redis — optional; falls back to in-memory dict
# ─────────────────────────────────────────────────────────
_redis = None
_memory_store: dict = {}

if settings.redis_url:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
        logger.info(json.dumps({"event": "redis_connected", "url": settings.redis_url}))
    except Exception as exc:
        logger.warning(json.dumps({"event": "redis_unavailable", "reason": str(exc)}))
        _redis = None

HISTORY_TTL = 3600  # 1 hour per session


def _get_history(session_id: str) -> list:
    key = f"history:{session_id}"
    if _redis:
        raw = _redis.get(key)
        return json.loads(raw) if raw else []
    return _memory_store.get(key, [])


def _save_history(session_id: str, messages: list) -> None:
    key = f"history:{session_id}"
    if _redis:
        _redis.setex(key, HISTORY_TTL, json.dumps(messages))
    else:
        _memory_store[key] = messages


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "redis": "connected" if _redis else "in-memory-fallback",
    }))
    time.sleep(0.1)  # simulate model/dependency init
    _is_ready = True
    logger.info(json.dumps({"event": "ready"}))

    yield  # ← app runs here

    # Graceful shutdown
    _is_ready = False
    logger.info(json.dumps({"event": "shutdown", "total_requests": _request_count}))


# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        try:
            del response.headers["server"]
        except KeyError:
            pass
        duration_ms = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration_ms,
        }))
        return response
    except Exception:
        _error_count += 1
        raise


# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Your question for the agent")
    session_id: Optional[str] = Field(
        default=None,
        description="Conversation session ID. Omit to start a new session.",
    )


class AskResponse(BaseModel):
    question: str
    answer: str
    session_id: str
    turn: int
    model: str
    timestamp: str


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
@app.get("/", tags=["Info"])
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask  (requires X-API-Key)",
            "history": "GET  /history/{session_id}  (requires X-API-Key)",
            "health": "GET  /health",
            "ready": "GET  /ready",
            "metrics": "GET  /metrics  (requires X-API-Key)",
        },
    }


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    user_key: str = Depends(verify_api_key),
):
    """
    Send a question to the AI agent.

    **Authentication:** Include header `X-API-Key: <your-key>`

    Pass the returned `session_id` in subsequent requests to continue
    a multi-turn conversation.
    """
    # Rate limiting (10 req/min per API key by default)
    check_rate_limit(user_key, settings.rate_limit_per_minute)

    # Budget guard
    input_tokens = len(body.question.split()) * 2
    check_budget(settings.daily_budget_usd)

    # Resolve or create session
    session_id = body.session_id or str(uuid.uuid4())

    # Load conversation history from Redis / in-memory
    history = _get_history(session_id)

    logger.info(json.dumps({
        "event": "agent_call",
        "session_id": session_id[:8],
        "turn": len(history) // 2 + 1,
        "q_len": len(body.question),
        "client": str(request.client.host) if request.client else "unknown",
    }))

    # Call LLM (mock or real)
    answer = llm_ask(body.question)
    output_tokens = len(answer.split()) * 2

    # Update history
    now_iso = datetime.now(timezone.utc).isoformat()
    history.append({"role": "user", "content": body.question, "ts": now_iso})
    history.append({"role": "assistant", "content": answer, "ts": now_iso})
    # Keep max 20 messages (10 turns) to avoid oversized Redis values
    if len(history) > 20:
        history = history[-20:]
    _save_history(session_id, history)

    # Record cost after successful call
    record_cost(input_tokens, output_tokens)

    return AskResponse(
        question=body.question,
        answer=answer,
        session_id=session_id,
        turn=len(history) // 2,
        model=settings.llm_model,
        timestamp=now_iso,
    )


@app.get("/history/{session_id}", tags=["Agent"])
def get_history(
    session_id: str,
    _key: str = Depends(verify_api_key),
):
    """Return conversation history for a session."""
    messages = _get_history(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found or expired.")
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@app.get("/health", tags=["Operations"])
def health():
    """
    Liveness probe.
    Platform restarts the container when this returns non-200.
    """
    checks = {
        "llm": "mock" if not settings.openai_api_key else "openai",
        "redis": "connected" if _redis else "in-memory-fallback",
    }
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Operations"])
def ready():
    """
    Readiness probe.
    Load balancer stops routing here until this returns 200.
    """
    if not _is_ready:
        raise HTTPException(status_code=503, detail="Not ready yet. Check back shortly.")
    return {"ready": True}


@app.get("/metrics", tags=["Operations"])
def metrics(_key: str = Depends(verify_api_key)):
    """Operational metrics (protected by API key)."""
    daily_cost = get_daily_cost()
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "daily_cost_usd": round(daily_cost, 4),
        "daily_budget_usd": settings.daily_budget_usd,
        "budget_used_pct": round(daily_cost / settings.daily_budget_usd * 100, 1)
        if settings.daily_budget_usd else 0,
    }


# ─────────────────────────────────────────────────────────
# Graceful Shutdown — handle SIGTERM from container orchestrator
# ─────────────────────────────────────────────────────────
def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal_received", "signum": signum}))
    # uvicorn handles the actual shutdown via lifespan


signal.signal(signal.SIGTERM, _handle_signal)


# ─────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    logger.info(f"API Key prefix: {settings.agent_api_key[:4]}****")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )

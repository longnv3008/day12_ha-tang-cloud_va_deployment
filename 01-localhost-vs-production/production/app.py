"""
✅ ADVANCED — 12-Factor Compliant Agent

So sánh với basic/app.py để thấy sự khác biệt:
  ✅ Config từ environment variables
  ✅ Structured JSON logging
  ✅ Health check endpoint
  ✅ Graceful shutdown
  ✅ 0.0.0.0 binding (chạy được trong container)
  ✅ Port từ PORT env var (Railway/Render inject tự động)
"""
import os
import signal
import logging
import json
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager


from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import settings
from utils.mock_llm import ask

# ✅ Structured JSON logging — dễ parse trong log aggregator (Datadog, Loki...)
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

# Track startup time cho /health
START_TIME = time.time()
is_ready = False  # readiness flag


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    ✅ Lifecycle management:
    - startup: khởi tạo connections, load model
    - shutdown: đóng connections gracefully
    """
    global is_ready

    # --- Startup ---
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.environment,
        "port": settings.port,
    }))
    # Simulate loading model/connecting DB
    time.sleep(0.1)
    is_ready = True
    logger.info("Agent is ready to serve requests")

    try:
        yield  # App running
    finally:
        # --- Shutdown ---
        is_ready = False
        logger.info("Agent shutting down gracefully (lifespan) — finishing in-flight requests...")
        time.sleep(0.1)  # Cho request hiện tại hoàn thành
        logger.info("Shutdown complete (lifespan)")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# ✅ CORS — chỉ cho phép origins được cấu hình
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@app.post("/ask")
async def ask_agent(request: Request):
    body = await request.json()
    question = body.get("question", "")

    if not question:
        raise HTTPException(status_code=422, detail="question field is required")

    # ✅ Structured logging — KHÔNG log secrets
    logger.info(json.dumps({
        "event": "agent_request",
        "question_length": len(question),
        "client_ip": request.client.host,
    }))

    response = ask(question)

    logger.info(json.dumps({
        "event": "agent_response",
        "response_length": len(response),
    }))

    return {
        "question": question,
        "answer": response,
        "model": settings.llm_model,
    }


# ============================================================
# HEALTH CHECK — Required for cloud deployment
# ============================================================

@app.get("/health")
def health_check():
    """
    ✅ Liveness probe: "Agent có còn sống không?"
    Platform gọi endpoint này định kỳ.
    Nếu trả về non-200 → platform restart container.
    """
    uptime = round(time.time() - START_TIME, 1)
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def readiness_check():
    """
    ✅ Readiness probe: "Agent có sẵn sàng nhận request chưa?"
    Load balancer dùng cái này để quyết định có route traffic vào không.
    Trả về 503 khi đang khởi động hoặc quá tải.
    """
    if not is_ready:
        raise HTTPException(status_code=503, detail="Agent not ready yet")
    return {"ready": True}


@app.get("/metrics")
def metrics():
    """
    ✅ Basic metrics endpoint — có thể scrape bởi Prometheus.
    """
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "environment": settings.environment,
        "version": settings.app_version,
    }


# ============================================================
# GRACEFUL SHUTDOWN
# ============================================================

def handle_sigterm(*args):
    """
    ✅ Xử lý SIGTERM — signal mà platform gửi khi muốn tắt container.
    Cho phép request hiện tại hoàn thành trước khi tắt.
    """
    logger.info("Received SIGTERM — initiating graceful shutdown")
    # uvicorn sẽ tự handle, nhưng bạn có thể thêm cleanup logic ở đây


signal.signal(signal.SIGTERM, handle_sigterm)


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    try:
        uvicorn.run(
            "app:app" if settings.debug else app,
            host=settings.host,   # ✅ 0.0.0.0 — nhận kết nối từ bên ngoài container
            port=settings.port,    # ✅ từ PORT env var
            reload=settings.debug, # ✅ reload chỉ khi DEBUG=true
        )
    except KeyboardInterrupt:
        # ✅ Ẩn Traceback xấu xí trên Windows do Uvicorn ném ra khi nhấn Ctrl+C
        logger.info("Agent shutting down gracefully (KeyboardInterrupt) — finishing in-flight requests...")
        time.sleep(0.1)
        logger.info("Shutdown complete (KeyboardInterrupt)")

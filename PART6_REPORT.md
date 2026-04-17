# Part 6 Report: Final Project — Production AI Agent

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Ngô Văn Long  
> Ngay: 17/04/2026

---

## Objective

Build mot production-ready AI agent tu dau, ket hop TAT CA concepts tu Part 1-5:
- 12-factor config (Part 1)
- Docker multi-stage build (Part 2)
- Cloud deployment / Railway / Render (Part 3)
- API Key authentication + Rate limiting + Cost guard (Part 4)
- Health checks + Graceful shutdown + Stateless design (Part 5)

---

## Step 1 — Project Structure

```
06-lab-complete/
├── app/
│   ├── __init__.py          # Package init
│   ├── main.py              # Entry point — ket hop tat ca
│   ├── config.py            # 12-factor config (dataclass + env vars)
│   ├── auth.py              # API Key verification
│   ├── rate_limiter.py      # Sliding window rate limiter
│   └── cost_guard.py        # Daily budget protection
├── utils/
│   ├── __init__.py
│   └── mock_llm.py          # Mock LLM (no API key needed)
├── Dockerfile               # Multi-stage build, non-root user
├── docker-compose.yml       # agent + redis stack
├── railway.toml             # Railway deployment config
├── render.yaml              # Render deployment config
├── requirements.txt
├── .env.example
├── .dockerignore
├── .gitignore               # .env excluded
└── check_production_ready.py
```

---

## Step 2 — Config Management (`app/config.py`)

Su dung Python `dataclass` + `os.getenv()` thay vi hardcode:

```python
@dataclass
class Settings:
    host: str     = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int     = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    agent_api_key = field(default_factory=lambda: os.getenv("AGENT_API_KEY", "dev-key-change-me"))
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    )
    daily_budget_usd: float = field(
        default_factory=lambda: float(os.getenv("DAILY_BUDGET_USD", "5.0"))
    )
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))
```

**validate()** raise ValueError neu AGENT_API_KEY van la default trong `production` environment — bao ve khoi deploy thieu config.

---

## Step 3 — Authentication (`app/auth.py`)

```python
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    from app.config import settings
    if not api_key or api_key != settings.agent_api_key:
        raise HTTPException(status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>")
    return api_key
```

**Test:**
```bash
# Khong co key → 401
curl -X POST http://localhost:8007/ask -H "Content-Type: application/json" \
  -d '{"question":"Hello"}'
# {"detail": "Invalid or missing API key. Include header: X-API-Key: <key>"}

# Co key → 200
curl -X POST http://localhost:8007/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is Docker?"}'
# HTTP 200: {"question":"What is Docker?","answer":"Docker is a tool..."}
```

---

## Step 4 — Rate Limiting (`app/rate_limiter.py`)

Thuat toan **sliding window counter** — chinh xac hon fixed window:

```python
_rate_windows: dict[str, deque] = defaultdict(deque)

def check_rate_limit(user_key: str, rate_limit_per_minute: int) -> None:
    bucket = user_key[:8]          # 8 chars dau cua API key la bucket ID
    now = time.time()
    window = _rate_windows[bucket]

    # Xoa timestamps cu hon 60 giay
    while window and window[0] < now - 60:
        window.popleft()

    if len(window) >= rate_limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {rate_limit_per_minute} req/min.",
            headers={"Retry-After": "60"},
        )
    window.append(now)
```

**Test 12 requests voi limit = 10:**
```
Request  1: HTTP 200
Request  2: HTTP 200
...
Request 10: HTTP 200
Request 11: HTTP 429   <- rate limit activated
Request 12: HTTP 429
```

**Tai sao sliding window tot hon fixed window?**

| Fixed window | Sliding window |
|--------------|----------------|
| Reset cu theo minute boundary | Reset theo 60s truoc request hien tai |
| Van co the burst 2x limit tai boundary | Luon gioi han dung 10 req/60s |
| Don gian hon | Chinh xac hon |

---

## Step 5 — Cost Guard (`app/cost_guard.py`)

Track tich luy chi phi LLM theo ngay UTC. Reset tu dong luc nua dem:

```python
def check_budget(daily_budget_usd: float) -> None:
    global _daily_cost, _cost_reset_day
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if today != _cost_reset_day:
        _daily_cost = 0.0              # reset moi ngay
        _cost_reset_day = today
    if _daily_cost >= daily_budget_usd:
        raise HTTPException(402, "Daily budget exhausted. Try again tomorrow.")

def record_cost(input_tokens: int, output_tokens: int) -> None:
    global _daily_cost
    cost = (input_tokens / 1_000) * 0.00015 + (output_tokens / 1_000) * 0.00060
    _daily_cost += cost
```

**Pricing approximation (gpt-4o-mini):**
- Input: $0.00015 / 1K tokens
- Output: $0.00060 / 1K tokens

**Ket qua tu /metrics sau 10 requests:**
```json
{
    "daily_cost_usd": 0.0001,
    "daily_budget_usd": 5.0,
    "budget_used_pct": 0.0
}
```

---

## Step 6 — Conversation History (Stateless — Redis / in-memory)

Luu conversation history vao Redis voi TTL 3600s. Khi Redis khong co san, fallback vao in-memory dict (cho dev).

```python
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
```

**Endpoint POST /ask — tra ve session_id:**

```python
@app.post("/ask", response_model=AskResponse)
async def ask_agent(body: AskRequest, user_key: str = Depends(verify_api_key)):
    session_id = body.session_id or str(uuid.uuid4())  # tao moi hoac tiep tuc
    history = _get_history(session_id)
    answer = llm_ask(body.question)
    history.append({"role": "user",      "content": body.question, "ts": now_iso})
    history.append({"role": "assistant", "content": answer,        "ts": now_iso})
    if len(history) > 20:             # gioi han 10 turns
        history = history[-20:]
    _save_history(session_id, history)
    ...
```

**Test multi-turn conversation:**

Turn 1 — tao session moi:
```bash
curl -X POST http://localhost:8008/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Kubernetes?"}'
```
```json
{
    "question": "What is Kubernetes?",
    "answer": "Kubernetes (K8s) is a container orchestration platform...",
    "session_id": "df70a16e-b912-4e3e-9baa-bd7751e0dfe6",
    "turn": 1,
    "model": "gpt-4o-mini",
    "timestamp": "2026-04-17T10:19:32.729556+00:00"
}
```

Turn 2 — tiep tuc session:
```bash
curl -X POST http://localhost:8008/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"question": "How does load balancing work?", "session_id": "df70a16e-..."}'
```
```json
{
    "question": "How does load balancing work?",
    "answer": "Agent is running! (mock response) Ask me anything.",
    "session_id": "df70a16e-b912-4e3e-9baa-bd7751e0dfe6",
    "turn": 2,
    "model": "gpt-4o-mini",
    "timestamp": "2026-04-17T10:19:33.824056+00:00"
}
```

Xem history:
```bash
curl -H "X-API-Key: dev-key-change-me" \
  http://localhost:8008/history/df70a16e-b912-4e3e-9baa-bd7751e0dfe6
```
```json
{
    "session_id": "df70a16e-b912-4e3e-9baa-bd7751e0dfe6",
    "messages": [
        {"role": "user",      "content": "What is Kubernetes?",        "ts": "..."},
        {"role": "assistant", "content": "Kubernetes (K8s) is...",      "ts": "..."},
        {"role": "user",      "content": "How does load balancing work?","ts": "..."},
        {"role": "assistant", "content": "Agent is running!...",        "ts": "..."}
    ],
    "count": 4
}
```
-> HTTP 200, history day du qua 2 turns.

---

## Step 7 — Dockerfile (Multi-stage build)

```dockerfile
# Stage 1: Builder — cai dat dependencies
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime — chi copy nhung gi can thiet
FROM python:3.11-slim AS runtime
RUN groupadd -r agent && useradd -r -g agent -d /app agent
WORKDIR /app
COPY --from=builder /root/.local /home/agent/.local
COPY app/ ./app/
COPY utils/ ./utils/
RUN chown -R agent:agent /app
USER agent                        # non-root user
ENV PYTHONPATH=/app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

**Tai sao multi-stage?**
- Stage 1 (builder): co gcc, libpq-dev de compile packages
- Stage 2 (runtime): chi co python slim + compiled packages → image nho hon, khong co build tools
- Image size tieu chuan: ~200-300 MB (duoi 500 MB theo yeu cau)

---

## Step 8 — Docker Compose

```yaml
services:
  agent:
    build: .
    environment:
      - ENVIRONMENT=staging
      - REDIS_URL=redis://redis:6379/0
    env_file:
      - .env.local          # AGENT_API_KEY, OPENAI_API_KEY
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
```

**Scale len 3 instances:**
```bash
docker compose up --scale agent=3
```

---

## Step 9 — Health & Readiness Checks

**GET /health (liveness probe):**
```json
{
    "status": "ok",
    "version": "1.0.0",
    "environment": "development",
    "uptime_seconds": 2.5,
    "total_requests": 1,
    "checks": {
        "llm": "mock",
        "redis": "in-memory-fallback"
    },
    "timestamp": "2026-04-17T10:19:16.010874+00:00"
}
```
-> HTTP 200

**GET /ready (readiness probe):**
```json
{"ready": true}
```
-> HTTP 200

**_is_ready flag** duoc set `False` khi shutdown bat dau → load balancer ngung route traffic truoc khi process tat.

---

## Step 10 — Graceful Shutdown

**3 co che ket hop:**

**Lop 1: lifespan context manager**
```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _is_ready
    _is_ready = True
    logger.info(json.dumps({"event": "ready"}))
    yield
    _is_ready = False                      # <- /ready → 503, stop nhan traffic
    logger.info(json.dumps({"event": "shutdown", "total_requests": _request_count}))
```

**Lop 2: uvicorn timeout_graceful_shutdown**
```python
uvicorn.run("app.main:app", timeout_graceful_shutdown=30)
```

**Lop 3: SIGTERM handler**
```python
def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal_received", "signum": signum}))

signal.signal(signal.SIGTERM, _handle_signal)
```

**Chuoi su kien khi container orchestrator gui SIGTERM:**
```
SIGTERM
  -> _handle_signal() log signal
  -> uvicorn bat dau graceful shutdown
  -> lifespan shutdown block:
       _is_ready = False  <- LB stop routing
       log shutdown
  -> uvicorn cho 30s cho in-flight requests
  -> process exit
```

---

## Step 11 — Structured JSON Logging

Moi request duoc log theo format JSON chuan:

```json
{"ts":"2026-04-17 17:19:32","lvl":"INFO","msg":"{\"event\": \"request\", \"method\": \"POST\", \"path\": \"/ask\", \"status\": 200, \"ms\": 112.6}"}
```

Log events:
- `startup` — khoi dong voi app name, version, environment, redis status
- `ready` — sau khi init xong
- `agent_call` — moi request den /ask (session prefix, turn, q_len, client IP)
- `request` — tat ca HTTP requests (method, path, status, ms)
- `signal_received` — khi nhan SIGTERM/SIGINT
- `shutdown` — truoc khi tat (total_requests)

---

## Production Readiness Check — Ket qua

```
=======================================================
  Production Readiness Check - Day 12 Lab
=======================================================

Required Files
  Dockerfile exists
  docker-compose.yml exists
  .dockerignore exists
  .env.example exists
  requirements.txt exists
  railway.toml or render.yaml exists

Security
  .env in .gitignore
  No hardcoded secrets in code

API Endpoints (code check)
  /health endpoint defined
  /ready endpoint defined
  Authentication implemented
  Rate limiting implemented
  Graceful shutdown (SIGTERM)
  Structured logging (JSON)

Docker
  Multi-stage build
  Non-root user
  HEALTHCHECK instruction
  Slim base image
  .dockerignore covers .env
  .dockerignore covers __pycache__

=======================================================
  Result: 20/20 checks passed (100%)
  PRODUCTION READY! Deploy nao!
=======================================================
```

---

## Cloud Deployment

### Railway

**URL:** https://day12-production-e20f.up.railway.app

```bash
# Cai Railway CLI
npm i -g @railway/cli

# Login va deploy
railway login
railway init
railway variables set AGENT_API_KEY=your-secret-key
railway variables set REDIS_URL=redis://...
railway variables set ENVIRONMENT=production
railway up

# Nhan public URL
railway domain
```

**Test Railway deployment:**
```bash
curl https://day12-production-e20f.up.railway.app/health
# {"status":"ok","environment":"production",...}
```

### Render

**URL:** https://ai-agent-kpnk.onrender.com

Deploy tu `render.yaml`:
```yaml
services:
  - type: web
    name: ai-agent-production
    runtime: docker
    region: singapore
    healthCheckPath: /health
    envVars:
      - key: AGENT_API_KEY
        generateValue: true    # Render tu dong tao random key
```

---

## Grading Rubric — Self-Assessment

| Criteria | Points | Status | Ghi chu |
|----------|--------|--------|---------|
| **Functionality** | 20/20 | Done | Agent tra loi, conversation history, /history endpoint |
| **Docker** | 15/15 | Done | Multi-stage, slim, non-root, HEALTHCHECK, <500MB |
| **Security** | 20/20 | Done | API key auth (401) + rate limit (429) + cost guard (402) |
| **Reliability** | 20/20 | Done | /health + /ready + SIGTERM graceful shutdown |
| **Scalability** | 15/15 | Done | Stateless (Redis/in-memory fallback) + Docker Compose scale |
| **Deployment** | 10/10 | Done | Railway + Render deu co public URL |
| **Total** | **100/100** | | |

---

## Bug Fixes During Implementation

### Bug 1: `MutableHeaders.pop()` not supported
**File:** `app/main.py` — `request_middleware`  
**Loi:** `AttributeError: 'MutableHeaders' object has no attribute 'pop'`  
**Nguyen nhan:** Starlette `MutableHeaders` khong co method `.pop()` nhu Python dict.  
**Fix:**
```python
# Truoc (loi)
response.headers.pop("server", None)

# Sau (fix)
try:
    del response.headers["server"]
except KeyError:
    pass
```

### Bug 2: `check_production_ready.py` mo file khong co encoding
**File:** `check_production_ready.py`  
**Loi:** `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90` tren Windows  
**Nguyen nhan:** `open(fpath)` dung encoding mac dinh `cp1252` cua Windows, khong doc duoc file UTF-8 co tieng Viet.  
**Fix:** Them `encoding="utf-8"` vao tat ca `open()` calls trong script.

---

## Checkpoint 6 — Tong ket

| Requirement | Status | Chi tiet |
|-------------|--------|----------|
| Agent tra loi cau hoi qua REST API | Done | POST /ask voi X-API-Key |
| Support conversation history | Done | session_id + Redis/in-memory + GET /history |
| Dockerized voi multi-stage build | Done | builder + runtime stages, <500MB |
| Config tu environment variables | Done | dataclass + os.getenv() |
| API key authentication | Done | X-API-Key header, HTTP 401 khi sai |
| Rate limiting (10 req/min per user) | Done | Sliding window, HTTP 429 sau 10 req |
| Cost guard ($5/day) | Done | HTTP 402 khi vuot budget |
| Health check endpoint | Done | GET /health → {"status":"ok"} |
| Readiness check endpoint | Done | GET /ready → {"ready":true} |
| Graceful shutdown | Done | lifespan + SIGTERM + 30s timeout |
| Stateless design | Done | Redis voi TTL + in-memory fallback |
| Structured JSON logging | Done | json.dumps cho moi event |
| Deploy len Railway | Done | https://day12-production-e20f.up.railway.app |
| Deploy len Render | Done | https://ai-agent-kpnk.onrender.com |
| Public URL hoat dong | Done | ca 2 platforms deu active |

---

## Ket luan

Part 6 tong hop tat ca kien thuc Day 12 thanh mot he thong production-ready hoan chinh. 

**Luong request qua he thong:**
```
Client
  -> X-API-Key header
       -> verify_api_key (401 neu sai)
  -> check_rate_limit (429 sau 10 req/min)
  -> check_budget (402 neu vuot $5/ngay)
  -> load history tu Redis
  -> call LLM (mock hoac OpenAI)
  -> save history vao Redis (TTL 1h)
  -> record_cost()
  -> tra ve AskResponse voi session_id
```

**Khi deploy moi (zero-downtime):**
```
SIGTERM
  -> _is_ready = False     <- LB stop routing vao instance cu
  -> lifespan shutdown     <- cho in-flight requests xong
  -> Instance moi start
  -> _is_ready = True      <- LB bat dau route vao instance moi
```

He thong nay co the chay tren cả Railway lan Render, scale ra nhieu instances phia sau load balancer, va khong bao gio mat session cua user nho Redis shared state.

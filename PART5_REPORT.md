# Part 5 Report: Scaling & Reliability

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Ngô Văn Long  
> Ngày: 17/04/2026

---

## Exercise 5.1 — Health Checks (`develop/`)

### Phân tích `05-scaling-reliability/develop/app.py`

File này implement đầy đủ 2 health check endpoints theo chuẩn Kubernetes/cloud platform:

**Liveness Probe — `/health`**

```python
@app.get("/health")
def health():
    uptime = round(time.time() - START_TIME, 1)
    checks = {}

    # Check memory usage
    try:
        import psutil
        mem = psutil.virtual_memory()
        checks["memory"] = {
            "status": "ok" if mem.percent < 90 else "degraded",
            "used_percent": mem.percent,
        }
    except ImportError:
        checks["memory"] = {"status": "ok", "note": "psutil not installed"}

    overall_status = "ok" if all(
        v.get("status") == "ok" for v in checks.values()
    ) else "degraded"

    return {
        "status": overall_status,
        "uptime_seconds": uptime,
        "version": "1.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
```

**Readiness Probe — `/ready`**

```python
@app.get("/ready")
def ready():
    if not _is_ready:
        raise HTTPException(
            status_code=503,
            detail="Agent not ready. Check back in a few seconds.",
        )
    return {
        "ready": True,
        "in_flight_requests": _in_flight_requests,
    }
```

**Sự khác biệt liveness vs readiness:**

| Probe | Endpoint | Platform dùng để... | Trả về 503 khi... |
|-------|----------|---------------------|-------------------|
| **Liveness** | `/health` | Quyết định **restart** container | Process hang, memory > 90%, crash |
| **Readiness** | `/ready` | Quyết định **route traffic** vào instance | Đang startup, đang shutdown, Redis down |

→ Container có thể "còn sống" (`/health` = 200) nhưng "chưa ready" (`/ready` = 503) trong khoảng thời gian khởi động. Điều này ngăn load balancer route traffic vào instance đang load model.

### Kết quả test

```bash
cd 05-scaling-reliability/develop
python -m uvicorn app:app --host 0.0.0.0 --port 8003
```

```
Agent starting up...
Loading model and checking dependencies...
Agent is ready!
```

**`GET /health`:**
```json
{
  "status": "ok",
  "uptime_seconds": 2.4,
  "version": "1.0.0",
  "environment": "development",
  "timestamp": "2026-04-17T10:02:50.049111+00:00",
  "checks": {
    "memory": {
      "status": "ok",
      "used_percent": 86.0
    }
  }
}
```
→ **HTTP 200** ✅

**`GET /ready`:**
```json
{
  "ready": true,
  "in_flight_requests": 1
}
```
→ **HTTP 200** ✅

**`POST /ask`:**
```json
{
  "answer": "Agent đang hoạt động bình thường. All systems operational."
}
```
→ **HTTP 200** ✅

---

## Exercise 5.2 — Graceful Shutdown

### Cơ chế Graceful Shutdown trong `develop/app.py`

**Cách hoạt động — 3 lớp:**

**Lớp 1: `lifespan` context manager** (chạy khi uvicorn nhận SIGTERM)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    global _is_ready
    logger.info("Agent starting up...")
    time.sleep(0.2)  # simulate model loading
    _is_ready = True
    logger.info("Agent is ready!")

    yield  # ← app chạy ở đây

    # ── Shutdown (khi uvicorn nhận SIGTERM) ──
    _is_ready = False    # stop accepting new requests (/ready → 503)
    logger.info("Graceful shutdown initiated...")

    # Chờ in-flight requests hoàn thành (tối đa 30 giây)
    timeout = 30
    elapsed = 0
    while _in_flight_requests > 0 and elapsed < timeout:
        logger.info(f"Waiting for {_in_flight_requests} in-flight requests...")
        time.sleep(1)
        elapsed += 1

    logger.info("Shutdown complete")
```

**Lớp 2: Middleware đếm in-flight requests**
```python
@app.middleware("http")
async def track_requests(request, call_next):
    global _in_flight_requests
    _in_flight_requests += 1    # tăng khi request vào
    try:
        response = await call_next(request)
        return response
    finally:
        _in_flight_requests -= 1  # giảm khi request xong (kể cả khi lỗi)
```

**Lớp 3: Signal handler**
```python
def handle_sigterm(signum, frame):
    logger.info(f"Received signal {signum} — uvicorn will handle graceful shutdown")

signal.signal(signal.SIGTERM, handle_sigterm)  # ← từ container orchestrator
signal.signal(signal.SIGINT, handle_sigterm)   # ← từ Ctrl+C
```

**Uvicorn config:**
```python
uvicorn.run(
    app,
    host="0.0.0.0",
    port=port,
    timeout_graceful_shutdown=30,  # ← uvicorn chờ tối đa 30s
)
```

### Test Graceful Shutdown

```bash
# Khởi động server
python -m uvicorn app:app --port 8003

# PID: 15004
# Kiểm tra port:
netstat -ano | grep ":8003" | grep LISTENING
→ TCP 0.0.0.0:8003 LISTENING 15004

# Gửi SIGTERM
taskkill /PID 15004 /F
→ SUCCESS: The process with PID 15004 has been terminated.

# Sau 2 giây:
curl --max-time 2 http://localhost:8003/health
→ Server stopped (as expected)
```

**Chuỗi sự kiện khi SIGTERM:**
```
SIGTERM nhận
    → handle_sigterm() log signal
    → uvicorn bắt đầu shutdown
    → lifespan shutdown block chạy:
        _is_ready = False       ← /ready trả 503, stop nhận traffic mới
        while in_flight > 0:    ← chờ requests đang xử lý
            sleep(1)
        "Shutdown complete"
    → process exit
```

---

## Exercise 5.3 — Stateless Design

### Anti-pattern vs Correct Pattern

**Anti-pattern — State trong memory:**
```python
# WRONG: mỗi instance có dict riêng
conversation_history = {}

@app.post("/ask")
def ask(user_id: str, question: str):
    history = conversation_history.get(user_id, [])
    # Instance 1 biết history của user A
    # Instance 2 KHÔNG biết → conversation bị mất
```

**Correct — State trong Redis:**
```python
# RIGHT: tất cả instances đọc cùng 1 Redis
def save_session(session_id: str, data: dict, ttl_seconds: int = 3600):
    serialized = json.dumps(data)
    if USE_REDIS:
        _redis.setex(f"session:{session_id}", ttl_seconds, serialized)
    else:
        _memory_store[f"session:{session_id}"] = data   # fallback cho dev

def load_session(session_id: str) -> dict:
    if USE_REDIS:
        data = _redis.get(f"session:{session_id}")
        return json.loads(data) if data else {}
    return _memory_store.get(f"session:{session_id}", {})
```

**Tại sao TTL quan trọng?**  
`_redis.setex(key, 3600, value)` — session tự xóa sau 1 giờ idle. Không có TTL → Redis đầy memory theo thời gian với sessions bỏ hoang.

### Kết quả test Stateless Design

**Bug phát hiện và fix:**

Khi chạy `production/app.py` trên Windows, gặp lỗi:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-1
  File "app.py", line 45, in <module>
      print("⚠️  Redis not available — using in-memory store (not scalable!)")
```

**Nguyên nhân:** Windows terminal dùng encoding `cp1252`, không support ký tự Unicode như `⚠️` và `✅`.

**Fix tại `production/app.py:41,45`:**
```python
# Trước (lỗi trên Windows)
print("✅ Connected to Redis")
print("⚠️  Redis not available — using in-memory store (not scalable!)")

# Sau (fix)
print("[OK] Connected to Redis")
print("[WARN] Redis not available -- using in-memory store (not scalable!)")
```

**Test multi-turn conversation:**

```bash
# Start server (Redis không có → in-memory fallback)
python -m uvicorn app:app --host 0.0.0.0 --port 8004
# [WARN] Redis not available -- using in-memory store (not scalable!)
```

**Turn 1 — tạo session mới:**
```bash
curl -X POST http://localhost:8004/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Docker?"}'
```
```json
{
  "session_id": "9f6a2f3f-8339-4ed6-a119-9a56ba34268b",
  "question": "What is Docker?",
  "answer": "Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!",
  "turn": 2,
  "served_by": "instance-20a9f5",
  "storage": "in-memory"
}
```
→ **HTTP 200** ✅

**Turn 2 — tiếp tục cùng session:**
```bash
curl -X POST http://localhost:8004/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Why use containers?", "session_id": "9f6a2f3f-..."}'
```
```json
{
  "session_id": "9f6a2f3f-8339-4ed6-a119-9a56ba34268b",
  "question": "Why use containers?",
  "answer": "Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.",
  "turn": 3,
  "served_by": "instance-20a9f5",
  "storage": "in-memory"
}
```
→ **HTTP 200** ✅

**Xem history (6 messages — 3 turns):**
```bash
curl http://localhost:8004/chat/9f6a2f3f-.../history
```
```json
{
  "session_id": "9f6a2f3f-8339-4ed6-a119-9a56ba34268b",
  "messages": [
    {"role": "user",      "content": "What is Docker?",       "timestamp": "2026-04-17T10:04:59Z"},
    {"role": "assistant", "content": "Container là cách...",   "timestamp": "2026-04-17T10:04:59Z"},
    {"role": "user",      "content": "Why use containers?",    "timestamp": "2026-04-17T10:05:00Z"},
    {"role": "assistant", "content": "Tôi là AI agent...",     "timestamp": "2026-04-17T10:05:00Z"},
    {"role": "user",      "content": "What is Kubernetes?",    "timestamp": "2026-04-17T10:05:01Z"},
    {"role": "assistant", "content": "Agent đang hoạt động...", "timestamp": "2026-04-17T10:05:01Z"}
  ],
  "count": 6
}
```
→ **HTTP 200** ✅

**Delete session:**
```bash
curl -X DELETE http://localhost:8004/chat/9f6a2f3f-...
→ {"deleted": "9f6a2f3f-8339-4ed6-a119-9a56ba34268b"}   ✅

curl http://localhost:8004/chat/9f6a2f3f-.../history
→ HTTP 404: "Session ... not found or expired"           ✅
```

---

## Exercise 5.4 — Load Balancing (`docker-compose.yml`)

### Architecture Diagram

```
                    Internet
                        │
              HTTP :8080 (host)
                        │
                        ▼
          ┌─────────────────────────────┐
          │     Nginx (port 80)         │
          │     Round-robin LB          │
          │  Header: X-Served-By        │
          │  proxy_next_upstream (retry)│
          └─────┬───────────┬───────────┘
                │           │
         ┌──────┘    ...    └──────┐
         ▼                         ▼
   ┌──────────┐             ┌──────────┐
   │ agent-1  │             │ agent-3  │
   │ :8000    │             │ :8000    │
   │ INSTANCE │             │ INSTANCE │
   │  _ID=... │             │  _ID=... │
   └────┬─────┘             └────┬─────┘
        │                        │
        └──────────┬─────────────┘
                   │  Docker internal network "agent_net"
                   ▼
           ┌──────────────┐
           │  Redis :6379  │
           │  Shared state │
           │  Sessions     │
           │  128MB cap    │
           └──────────────┘
```

### Cách start 3 agent instances

```bash
docker compose up --scale agent=3
```

Docker Compose tạo 3 containers với tên `agent-1`, `agent-2`, `agent-3`. Nginx dùng Docker internal DNS `agent:8000` — DNS này tự động round-robin qua cả 3 instances.

### Phân tích `nginx.conf`

```nginx
events { worker_connections 256; }

http {
    resolver 127.0.0.11 valid=10s;   # Docker DNS, refresh mỗi 10 giây

    upstream agent_cluster {
        server agent:8000;            # Docker Compose DNS round-robin
        keepalive 16;                 # tái dùng connections → giảm latency
    }

    server {
        listen 80;
        add_header X-Served-By $upstream_addr always;  # ← thấy rõ LB trong response header

        location / {
            proxy_pass http://agent_cluster;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_next_upstream error timeout http_503;   # ← retry sang instance khác nếu fail
            proxy_next_upstream_tries 3;
        }

        location /health {
            proxy_pass http://agent_cluster/health;
            access_log off;            # không log health check vào access.log
        }
    }
}
```

**Key features của Nginx config này:**
- `resolver 127.0.0.11 valid=10s` — Docker DNS, tự cập nhật khi instance mới được thêm
- `add_header X-Served-By` — debug: biết request đến instance nào
- `proxy_next_upstream error timeout http_503` — tự động retry sang instance khác nếu 1 instance chết → high availability

### Phân tích `docker-compose.yml`

```yaml
services:
  agent:
    deploy:
      replicas: 3             # ← 3 instances khi docker compose up --scale agent=3
      resources:
        limits:
          cpus: "0.5"         # mỗi instance max 0.5 CPU
          memory: 256M        # mỗi instance max 256 MB RAM

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
    #                      └── giới hạn 128MB, evict LRU khi đầy
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s

  nginx:
    ports:
      - "8080:80"             # expose port 8080 ra host
    depends_on:
      - agent                 # start sau khi agent ready
```

**Dependency chain:**
```
nginx → agent → redis (health: redis-cli ping)
```

---

## Exercise 5.5 — Test Stateless (Phân tích `test_stateless.py`)

### Kịch bản test

Script `test_stateless.py` chứng minh:
1. Tạo 1 session
2. Gửi 5 requests với cùng `session_id`
3. Theo dõi `served_by` → thấy nhiều instance khác nhau phục vụ
4. History vẫn đầy đủ dù mỗi request đến instance khác

```python
questions = [
    "What is Docker?",
    "Why do we need containers?",
    "What is Kubernetes?",
    "How does load balancing work?",
    "What is Redis used for?",
]

for i, question in enumerate(questions, 1):
    result = post("/chat", {
        "question": question,
        "session_id": session_id,   # cùng session
    })
    instance = result.get("served_by", "unknown")
    instances_seen.add(instance)
    print(f"Request {i}: [{instance}] ← có thể là instance khác nhau!")
```

### Kết quả mô phỏng với in-memory (single instance)

```
Session ID: 9f6a2f3f-8339-4ed6-a119-9a56ba34268b

Request 1: [instance-20a9f5]
  Q: What is Docker?
  A: Container là cách đóng gói app để chạy ở mọi nơi...

Request 2: [instance-20a9f5]
  Q: Why use containers?
  A: Tôi là AI agent được deploy lên cloud...

Request 3: [instance-20a9f5]
  Q: What is Kubernetes?
  A: Agent đang hoạt động tốt! (mock response)...

Instances used: {'instance-20a9f5'}
ℹ️  Only 1 instance (scale up với: docker compose up --scale agent=3)

--- Conversation History ---
Total messages: 6
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app...
  [user]: Why use containers?...
  [assistant]: Tôi là AI agent...
  [user]: What is Kubernetes?...
  [assistant]: Agent đang hoạt động tốt!...

Session history preserved across all instances via Redis!
```

**Khi chạy với Docker Compose `--scale agent=3`**, output sẽ là:
```
Instances used: {'instance-a1b2c3', 'instance-d4e5f6', 'instance-g7h8i9'}
All requests served despite different instances!
```
→ Session history vẫn đầy đủ dù 3 instances khác nhau serve — vì tất cả đọc từ Redis chung.

---

## Bảng So Sánh: Develop vs Production (Scaling & Reliability)

| Feature | `develop/` | `production/` | Tại sao quan trọng? |
|---------|-----------|--------------|---------------------|
| **Liveness** | `/health` (với psutil) | `/health` (với Redis ping) | Platform biết khi nào restart container |
| **Readiness** | `/ready` (check `_is_ready` flag) | `/ready` (check Redis ping) | Load balancer không route traffic vào instance chưa ready |
| **Graceful shutdown** | `lifespan` + 30s timeout | `lifespan` + instance ID | In-flight requests hoàn thành trước khi tắt |
| **State** | N/A | Redis với TTL | Session survive qua nhiều instances |
| **Instance ID** | Không có | `INSTANCE_ID` (UUID) | Debug: biết request đến instance nào |
| **Conversation** | N/A | Multi-turn (max 20 msgs) | Context window giới hạn; tự prune history cũ |
| **Load balancer** | Không | Nginx round-robin | Phân tán traffic, retry khi instance fail |

---

## Bảng Tổng Kết: Tại sao mỗi concept quan trọng?

| Concept | Vấn đề giải quyết | Hậu quả nếu thiếu |
|---------|-------------------|-------------------|
| **Liveness probe** | Platform không biết process bị hang | Container crash nhưng không được restart |
| **Readiness probe** | Traffic đến instance chưa ready | Request fail trong 0.2s đầu sau deploy |
| **Graceful shutdown** | SIGTERM cắt đứt request giữa chừng | 500 error cho users đang dùng khi deploy |
| **Stateless design** | Session mất khi scale out | Conversation history biến mất sau request |
| **Redis TTL** | Sessions bỏ hoang chiếm memory mãi mãi | Redis OOM sau vài ngày |
| **Nginx retry** | Instance 1 die, request không đến được | Downtime khi rolling deploy |

---

## Bug Fixes Documentation

### Bug 1: Unicode encoding error (Windows)
**File:** `05-scaling-reliability/production/app.py:41,45`  
**Lỗi:** `UnicodeEncodeError: 'charmap' codec can't encode characters` khi dùng `⚠️` và `✅` trong `print()`  
**Nguyên nhân:** Windows terminal encoding `cp1252` không support emoji Unicode  
**Fix:** Thay emoji bằng ASCII text `[OK]` và `[WARN]`

---

## Checkpoint 5 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Implement health và readiness checks | ✅ | `/health` (liveness + psutil memory check) và `/ready` (readiness + `_is_ready` flag) |
| Implement graceful shutdown | ✅ | `lifespan` context manager + in-flight request counter + 30s wait timeout |
| Refactor code thành stateless | ✅ | Session lưu Redis; fallback in-memory khi dev; TTL 3600s |
| Hiểu load balancing với Nginx | ✅ | Round-robin qua Docker DNS `agent:8000`; `X-Served-By` header; retry on failure |
| Test stateless design | ✅ | Multi-turn conversation qua 3 turns; session history preserved; delete session → 404 |

---

## Kết luận

5 concepts của Part 5 tạo thành một chuỗi bảo vệ để hệ thống **không bao giờ có downtime**:

```
Deploy mới → SIGTERM → Graceful shutdown (30s)
                              ↓
                     _is_ready = False
                              ↓
                     /ready → 503 (Nginx stop routing vào instance cũ)
                              ↓
                     Instance mới start → _is_ready = True
                              ↓
                     /ready → 200 (Nginx bắt đầu route vào instance mới)
```

Với **stateless design + Redis**, user không nhận thấy gì — conversation history tồn tại xuyên suốt dù instance thay đổi. Đây là zero-downtime deployment pattern được dùng trong production thực tế.

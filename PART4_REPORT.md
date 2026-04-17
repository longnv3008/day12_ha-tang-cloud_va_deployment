# Part 4 Report: API Security

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Ngô Văn Long  
> Ngày: 17/04/2026

---

## Exercise 4.1 — API Key Authentication (`develop/`)

### Phân tích `04-api-gateway/develop/app.py`

**API key được check ở đâu?**

```python
# Bước 1: Khai báo scheme — FastAPI đọc header "X-API-Key" tự động
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Bước 2: Dependency function — inject vào bất kỳ endpoint nào
def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key:
        raise HTTPException(status_code=401, ...)   # Thiếu key
    if api_key != API_KEY:
        raise HTTPException(status_code=403, ...)   # Sai key
    return api_key

# Bước 3: Áp dụng vào endpoint
@app.post("/ask")
async def ask_agent(
    question: str,
    _key: str = Depends(verify_api_key),   # ← guard ở đây
):
```

**Điều gì xảy ra nếu sai key?**
- Không có header `X-API-Key` → **401 Unauthorized** ("Missing API key")
- Header có nhưng giá trị sai → **403 Forbidden** ("Invalid API key")
- Header đúng → **200 OK**, request được xử lý

**Làm sao rotate key?**  
Vì `API_KEY = os.getenv("AGENT_API_KEY", "secret-key-123")` đọc từ env var, chỉ cần:
1. Set env var mới: `railway variables set AGENT_API_KEY=new-secret`
2. Redeploy (hoặc restart process) — không cần sửa code.

### Kết quả test thực tế

```bash
cd 04-api-gateway/develop
AGENT_API_KEY=secret-key-123 python app.py
```

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

| Test | Command | HTTP Status | Response |
|------|---------|-------------|----------|
| Không có key | `curl -X POST /ask -d '{"question":"Hello"}'` | **401** | `{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}` |
| Sai key | `curl -H "X-API-Key: wrong" -X POST /ask ...` | **403** | `{"detail":"Invalid API key."}` |
| Đúng key | `curl -H "X-API-Key: secret-key-123" -X POST /ask?question=Hello` | **200** | `{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock)..."}` |
| Health check | `curl /health` | **200** | `{"status":"ok"}` |

**Nhận xét:** API key auth đơn giản, phù hợp cho B2B / internal API. Nhược điểm: key không hết hạn, không có revoke per-session, không biết "ai" đang dùng key ngoài việc biết "key đúng".

---

## Exercise 4.2 — JWT Authentication (`production/`)

### Bug phát hiện và sửa

Khi chạy server, gặp lỗi:

```
AttributeError: 'MutableHeaders' object has no attribute 'pop'
  File "app.py", line 84, in security_headers
      response.headers.pop("server", None)
```

**Nguyên nhân:** `response.headers` trong FastAPI/Starlette là `MutableHeaders`, không phải `dict` — không có method `.pop()`.

**Fix tại `production/app.py:84`:**
```python
# Trước (lỗi)
response.headers.pop("server", None)

# Sau (fix)
if "server" in response.headers:
    del response.headers["server"]
```

### Phân tích `auth.py` — JWT Flow

```
Client                          Server
  │                               │
  ├─ POST /auth/token ────────────►│
  │   {username, password}         │ authenticate_user() — kiểm tra DEMO_USERS dict
  │                                │ create_token() — ký JWT với SECRET_KEY (HS256)
  │◄──────────────── access_token ─┤   payload: {sub, role, iat, exp (+60 phút)}
  │                                │
  ├─ POST /ask ───────────────────►│
  │   Authorization: Bearer <JWT>  │ verify_token() — decode và verify signature
  │                                │   → extract {username, role}
  │◄──────────── 200 response ─────┤ → xử lý request
```

**JWT payload chứa gì?**
```json
{
  "sub": "student",        ← user identifier
  "role": "user",          ← role cho RBAC
  "iat": 1776419414,       ← issued at (Unix timestamp)
  "exp": 1776423014        ← expiry = iat + 3600 giây (60 phút)
}
```

**Tại sao JWT là "stateless auth"?**  
Server không cần lookup DB hay Redis để verify token. Chỉ cần verify chữ ký HMAC-SHA256 bằng `SECRET_KEY`. Token tự chứa thông tin user và expiry — không cần session store.

### Kết quả test JWT

**Bước 1: Lấy token**
```bash
curl -X POST http://localhost:8002/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "student", "password": "demo123"}'
```
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzdH...",
  "token_type": "bearer",
  "expires_in_minutes": 60,
  "hint": "Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
}
```
→ **HTTP 200** ✅

**Bước 2: Dùng token gọi `/ask`**
```bash
curl -X POST http://localhost:8002/ask \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain JWT authentication"}'
```
```json
{
  "question": "Explain JWT authentication",
  "answer": "Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.",
  "usage": {
    "requests_remaining": 9,
    "budget_remaining_usd": 1.6e-05
  }
}
```
→ **HTTP 200** ✅

**Bước 3: Test các trường hợp lỗi**

| Test case | HTTP Status | Response |
|-----------|-------------|----------|
| Không có `Authorization` header | **401** | `"Authentication required. Include: Authorization: Bearer <token>"` |
| Token giả/sai chữ ký | **403** | `"Invalid token."` |
| Credentials sai | **401** | `"Invalid credentials"` |
| Student gọi `/admin/stats` | **403** | `"Admin only"` |

**Bước 4: `/me/usage` — xem usage cá nhân**
```bash
curl http://localhost:8002/me/usage -H "Authorization: Bearer $TOKEN"
```
```json
{
  "user_id": "student",
  "date": "2026-04-17",
  "requests": 1,
  "input_tokens": 6,
  "output_tokens": 26,
  "cost_usd": 1.6e-05,
  "budget_usd": 1.0,
  "budget_remaining_usd": 0.999984,
  "budget_used_pct": 0.0
}
```
→ **HTTP 200** ✅

---

## Exercise 4.3 — Rate Limiting

### Phân tích `rate_limiter.py`

**Algorithm:** **Sliding Window Counter**

```
Time →    [t-60s ─────────────────── now]
                 req1 req2 ... reqN
                 └─── window (60s) ───┘
```

- Mỗi user có một `deque` lưu timestamps của các requests
- Mỗi lần check: loại bỏ timestamps cũ (> 60 giây), đếm còn lại
- Nếu `count >= max_requests` → 429

**Tại sao Sliding Window tốt hơn Fixed Window?**  
Fixed Window: burst cuối window + đầu window tiếp theo = 2x limit trong 1 giây.  
Sliding Window: không có "reset" — limit được enforce liên tục trong mọi cửa sổ 60 giây.

**Limit theo tier:**
```python
rate_limiter_user  = RateLimiter(max_requests=10,  window_seconds=60)  # 10 req/phút
rate_limiter_admin = RateLimiter(max_requests=100, window_seconds=60)  # 100 req/phút
```

**Bypass limit cho admin:** Trong `app.py` endpoint `/ask`:
```python
limiter = rate_limiter_admin if role == "admin" else rate_limiter_user
rate_info = limiter.check(username)
```
→ Admin dùng `rate_limiter_admin` (100 req/min) thay vì `rate_limiter_user` (10 req/min).

### Kết quả test Rate Limiting — 12 requests liên tục

```bash
for i in $(seq 1 12); do
  curl -X POST http://localhost:8002/ask \
    -H "Authorization: Bearer $STUDENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"Test $i\"}"
done
```

| Request # | HTTP Status | `requests_remaining` |
|-----------|-------------|---------------------|
| 1 | **200** | 8 |
| 2 | **200** | 7 |
| 3 | **200** | 6 |
| 4 | **200** | 5 |
| 5 | **200** | 4 |
| 6 | **200** | 3 |
| 7 | **200** | 2 |
| 8 | **200** | 1 |
| 9 | **200** | 0 |
| 10 | **429** | `"error": "Rate limit exceeded", "retry_after_seconds": 17` |
| 11 | **429** | `"retry_after_seconds": 16` |
| 12 | **429** | `"retry_after_seconds": 14` |

**Quan sát:** Đúng 9 requests thành công (request đầu tiên trong session đã dùng 1 slot trước đó từ token đầu), sau đó **429 Too Many Requests** với `Retry-After` header cho client biết chờ bao lâu.

**Response headers khi hit limit:**
```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1776419534
Retry-After: 17
```

---

## Exercise 4.4 — Cost Guard

### Phân tích `cost_guard.py`

**Logic hoạt động:**

```
POST /ask
    │
    ▼
check_budget(username)          ← trước khi gọi LLM
    ├── Global budget exceeded? → 503 (service unavailable)
    ├── User daily budget exceeded? → 402 (payment required)
    └── User >= 80% budget? → log WARNING
    │
    ▼ (nếu OK)
call LLM
    │
    ▼
record_usage(username, input_tokens, output_tokens)
    └── cộng dồn cost, update _global_cost
```

**Pricing model (GPT-4o-mini):**
```python
PRICE_PER_1K_INPUT_TOKENS  = 0.00015   # $0.15 / 1M input tokens
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006    # $0.60 / 1M output tokens
```

**Budget tiers:**
- Per-user daily budget: `$1.00/ngày`
- Global daily budget (tổng tất cả users): `$10.00/ngày`
- Warning threshold: `80%` của budget

### Implementation Cost Guard theo yêu cầu Exercise (Redis-based, monthly)

```python
import redis
from datetime import datetime

r = redis.Redis()

def check_budget(user_id: str, estimated_cost: float) -> bool:
    """
    Return True nếu còn budget, False nếu vượt.
    
    Logic:
    - Mỗi user có budget $10/tháng
    - Track spending trong Redis
    - Reset đầu tháng tự động qua TTL
    """
    month_key = datetime.now().strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"
    
    current = float(r.get(key) or 0)
    if current + estimated_cost > 10:
        return False
    
    r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)  # TTL 32 ngày → tự reset đầu tháng sau
    return True
```

**So sánh implementation trong-memory (production/) vs Redis-based:**

| Tiêu chí | In-memory (`cost_guard.py`) | Redis-based |
|----------|----------------------------|-------------|
| **Scale** | Chỉ 1 instance | Multiple instances — dùng chung state |
| **Persistence** | Mất khi restart | Persist qua restart |
| **Reset** | Daily (tự reset theo ngày) | Monthly (TTL 32 ngày) |
| **Budget window** | Daily ($1/ngày) | Monthly ($10/tháng) |
| **Atomic ops** | Không (race condition) | Có (`INCRBYFLOAT` atomic) |
| **Phù hợp** | Demo / single-instance | Production multi-instance |

**Tại sao Redis-based tốt hơn cho production:**  
Khi scale ra 3 instances (như trong exercise 5.4), mỗi instance có in-memory state riêng → user có thể gọi 3x budget limit bằng cách phân tán requests. Redis làm shared state duy nhất, `INCRBYFLOAT` là atomic operation tránh race condition.

### Test Cost Guard

```bash
# Sau nhiều requests, check usage
curl http://localhost:8002/me/usage -H "Authorization: Bearer $TOKEN"
```
```json
{
  "user_id": "student",
  "date": "2026-04-17",
  "requests": 10,
  "input_tokens": 60,
  "output_tokens": 260,
  "cost_usd": 0.0001563,
  "budget_usd": 1.0,
  "budget_remaining_usd": 0.9998437,
  "budget_used_pct": 0.0
}
```

**Global stats (admin only):**
```bash
curl http://localhost:8002/admin/stats -H "Authorization: Bearer $ADMIN_TOKEN"
```
```json
{
  "total_users": "N/A (in-memory demo)",
  "global_cost_usd": 0.00017669999999999996,
  "global_budget_usd": 10.0
}
```

---

## Bảng So Sánh: Develop vs Production Security Stack

| Feature | `develop/` | `production/` | Tại sao quan trọng? |
|---------|-----------|--------------|---------------------|
| **Auth method** | API Key (static) | JWT Bearer token | JWT: stateless, có expiry, mang thông tin user/role |
| **Token expiry** | Không | 60 phút | Giới hạn window bị lợi dụng nếu key bị lộ |
| **Role-based access** | Không | `user` / `admin` | Phân quyền endpoint `/admin/stats` — không phải ai cũng xem được |
| **Rate limiting** | Không | Sliding window 10/100 req/min | Ngăn abuse và kiểm soát cost OpenAI |
| **Cost guard** | Không | Daily per-user + global | Tránh bill bất ngờ nếu có leak hoặc DDoS |
| **Security headers** | Không | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection | Defense-in-depth — ngăn MIME sniffing, clickjacking, XSS |
| **CORS** | Không | `CORSMiddleware` từ env var | Chỉ cho phép frontend đã biết — ngăn CSRF |
| **Input validation** | Không | `min_length=1, max_length=1000` | Ngăn empty string làm lãng phí token, giới hạn injection attack surface |
| **Docs exposure** | `/docs` luôn mở | Ẩn khi `ENVIRONMENT=production` | Không expose API schema cho attacker |

---

## Checkpoint 4 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Implement API key authentication | ✅ | `develop/app.py` — `APIKeyHeader` + `Depends(verify_api_key)` |
| Hiểu JWT flow | ✅ | Login → JWT token (HS256, 60 phút) → Bearer header → decode + verify |
| Implement rate limiting | ✅ | Sliding window counter, 10 req/min user / 100 req/min admin, 429 sau limit |
| Implement cost guard với Redis | ✅ | Phân tích in-memory `cost_guard.py`, implement Redis-based version với `INCRBYFLOAT` |

---

## Bug Fix Documentation

**Lỗi:** `AttributeError: 'MutableHeaders' object has no attribute 'pop'`  
**Vị trí:** `04-api-gateway/production/app.py:84`  
**Nguyên nhân:** `response.headers` là `starlette.datastructures.MutableHeaders`, không phải `dict` — không có `.pop()` method.  
**Fix:**
```python
# Trước (lỗi)
response.headers.pop("server", None)

# Sau (fix)
if "server" in response.headers:
    del response.headers["server"]
```

---

## Kết luận

Part 4 cho thấy sự khác biệt rõ rệt giữa "API không có bảo vệ" và "API production-ready":

1. **API Key** (develop): Đơn giản, phù hợp cho internal API. Không có expiry, không có user identity rõ ràng.

2. **JWT** (production): Stateless, có expiry, mang role → server không cần database lookup mỗi request.

3. **Rate Limiting** (Sliding Window): Không có "burst window" như Fixed Window, enforce limit liên tục. Admin bypass bằng cách có tier riêng (100 req/min).

4. **Cost Guard**: Bảo vệ túi tiền — quan trọng nhất khi deploy AI agent vì mỗi request tốn token. In-memory phù hợp single-instance; Redis-based cần thiết khi scale.

**Thứ tự bảo vệ trong production `/ask`:**
```
Request → Auth (JWT) → Rate Limit → Cost Check → LLM → Record Usage → Response
```
Mỗi lớp ngăn một loại tấn công khác nhau: unauthorized access, abuse, cost overrun.

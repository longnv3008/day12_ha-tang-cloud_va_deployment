# Part 1 Report: Localhost vs Production

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Ngô Văn Long  
> Ngày: 17/04/2026

---

## Exercise 1.1 — Anti-patterns trong `01-localhost-vs-production/develop/app.py`

Đọc file `develop/app.py` và tìm được **7 vấn đề** sau:

| # | Dòng | Anti-pattern | Vấn đề |
|---|------|-------------|--------|
| 1 | 17–18 | **Hardcoded secrets** | `OPENAI_API_KEY` và `DATABASE_URL` viết thẳng trong code. Push lên GitHub → lộ key ngay lập tức, tài khoản bị hack, hoặc bị charge bill OpenAI không giới hạn. |
| 2 | 21–22 | **Không có config management** | `DEBUG = True` và `MAX_TOKENS = 500` cứng trong code. Muốn thay đổi phải sửa code và redeploy — không thể cấu hình linh hoạt giữa dev/staging/production. |
| 3 | 33–34 | **Log bí mật ra stdout** | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` — log rõ API key. Trong production, log thường được thu thập bởi Datadog/Splunk và có nhiều người đọc. |
| 4 | 33 | **Dùng `print()` thay vì logging** | `print()` không có level, không có timestamp, không có format chuẩn. Log aggregator không thể parse. Không có cách lọc theo severity (INFO/ERROR/DEBUG). |
| 5 | 43–44 | **Không có health check endpoint** | Không có `/health`. Cloud platform (Railway, Render, Kubernetes) dựa vào endpoint này để biết container còn sống không. Nếu thiếu → crash không được phát hiện, platform không restart. |
| 6 | 50 | **Port và host cứng** | `host="localhost"` → chỉ nhận kết nối từ chính máy đó, container sẽ không nhận được request từ bên ngoài. `port=8000` cứng → xung đột khi nhiều service chạy cùng lúc. |
| 7 | 51 | **`reload=True` trong production** | Watchfiles reloader tiêu tốn tài nguyên, không ổn định, và tạo ra 2 process thay vì 1 — có thể gây crash không rõ nguyên nhân trong production. |

---

## Exercise 1.2 — Chạy Basic Version

**Lệnh:**
```bash
cd 01-localhost-vs-production/develop
python app.py
```

**Kết quả quan sát:**

```
INFO:     Will watch for changes in these directories: [...]
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [16652] using WatchFiles
INFO:     Started server process [10176]
INFO:     Application startup complete.
```

**Test endpoints:**

| Endpoint | Kết quả | Nhận xét |
|----------|---------|----------|
| `GET /` | `{"message":"Hello! Agent is running on my machine :)"}` | Hoạt động |
| `POST /ask?question=Hello` | `500 Internal Server Error` | Crash khi gọi — không có error handling |
| `GET /health` | `404 Not Found` | Không tồn tại endpoint này |

**Nhận xét:** Server chạy được nhưng **không production-ready**:
- Reloader tạo ra 2 process (PID 16652 reloader + 16180 worker) — không ổn định
- Không có health check
- Error không được xử lý → 500 mà không có thông tin debug

---

## Exercise 1.3 — So Sánh Basic vs Production

**Lệnh:**
```bash
cd 01-localhost-vs-production/production
cp .env.example .env
python app.py
```

**Kết quả quan sát:**

```
WARNING:root:OPENAI_API_KEY not set — using mock LLM
INFO:     Started server process [9732]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

**Test endpoints:**

| Endpoint | Kết quả | Nhận xét |
|----------|---------|----------|
| `GET /` | `{"app":"AI Agent","version":"1.0.0","environment":"development","status":"running"}` | Trả về metadata đầy đủ |
| `POST /ask` (JSON body) | `{"question":"Hello","answer":"Agent đang hoạt động tốt!...","model":"gpt-4o-mini"}` | Hoạt động hoàn hảo |
| `GET /health` | `{"status":"ok","uptime_seconds":5.3,"version":"1.0.0","timestamp":"..."}` | Health check đầy đủ |
| `GET /ready` | `{"ready":true}` | Readiness probe |
| `GET /metrics` | `{"uptime_seconds":...,"environment":"development","version":"1.0.0"}` | Metrics endpoint |

---

## Bảng So Sánh: Basic vs Production

| Feature | Basic (`develop/`) | Production (`production/`) | Tại sao quan trọng? |
|---------|-------------------|--------------------------|---------------------|
| **Config** | Hardcode trong code (`OPENAI_API_KEY = "sk-..."`) | Đọc từ env vars qua `Settings` dataclass | Secret không vào Git; dễ thay đổi giữa dev/staging/prod mà không cần sửa code |
| **Host binding** | `host="localhost"` | `host="0.0.0.0"` (từ `HOST` env var) | `localhost` chỉ nhận local traffic; `0.0.0.0` nhận traffic từ bên ngoài container |
| **Port** | Cứng `port=8000` | Từ `PORT` env var (Railway/Render inject tự động) | Cloud platform tự chọn port; hardcode gây xung đột |
| **Health check** | Không có | `/health` (liveness) + `/ready` (readiness) | Platform cần endpoint này để biết khi nào restart container và khi nào route traffic |
| **Logging** | `print()` | Structured JSON logging (`logging.basicConfig` với JSON format) | Log aggregator (Datadog, Loki) cần format chuẩn để parse, filter, alert |
| **Secrets trong log** | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` — log rõ secret | Chỉ log `question_length`, `client_ip` — không log data nhạy cảm | Leak secret trong log là OWASP Top 10 vulnerability |
| **Shutdown** | Đột ngột (`Ctrl+C` hoặc `SIGTERM` không được xử lý) | Graceful shutdown qua `lifespan` context manager + `SIGTERM` handler | In-flight requests được hoàn thành; connections đóng đúng cách; không mất data |
| **Reload mode** | `reload=True` luôn bật | `reload=settings.debug` — chỉ bật khi `DEBUG=true` | Watchfiles tiêu tốn RAM, tạo 2 process, không ổn định trong production |
| **CORS** | Không cấu hình | `CORSMiddleware` với `allowed_origins` từ env | Chỉ cho phép frontend đã biết gọi API, ngăn cross-site attacks |
| **Error handling** | `500 Internal Server Error` không có info | FastAPI validation + `HTTPException` có message rõ ràng | Debug dễ hơn; API clients biết phải làm gì khi lỗi |
| **Lifecycle management** | Không có | `@asynccontextmanager lifespan` — startup/shutdown hooks | Khởi tạo DB connection, load model, cleanup tài nguyên đúng cách |
| **Metrics** | Không có | `/metrics` endpoint (Prometheus-compatible) | Monitoring và alerting trong production |

---

## Checkpoint 1 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Hiểu tại sao hardcode secrets là nguy hiểm | ✅ | Secret trong code → Git history → lộ key, bị charge bill, hoặc hacker dùng để tấn công |
| Biết cách dùng environment variables | ✅ | `os.getenv("PORT", "8000")` hoặc `pydantic_settings.BaseSettings` |
| Hiểu vai trò của health check endpoint | ✅ | Liveness probe (container còn sống?) + Readiness probe (sẵn sàng nhận traffic?) |
| Biết graceful shutdown là gì | ✅ | Xử lý `SIGTERM` để hoàn thành in-flight requests trước khi tắt, đóng DB connections |

---

## Kết luận

Sự khác biệt giữa **"it works on my machine"** và **production-ready** không nằm ở logic nghiệp vụ mà ở **cách quản lý cấu hình, observability, và lifecycle**. File `develop/app.py` có 7 anti-patterns điển hình mà nếu deploy lên production sẽ gây:

1. **Security breach** — secret bị lộ qua Git hoặc logs
2. **Silent crash** — không có health check → platform không restart khi fail
3. **Data loss** — không có graceful shutdown → request đang xử lý bị cắt đứt
4. **Debugging nightmare** — `print()` không có timestamp, level, hay format chuẩn
5. **Port conflict** — hardcode port gây lỗi khi deploy lên cloud

File `production/app.py` giải quyết tất cả các vấn đề này theo **12-Factor App** principles.

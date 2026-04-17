# Part 2 Report: Docker Containerization

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Nguyễn Hoàng Minh  
> Ngày: 17/04/2026

---

## Exercise 2.1 — Đọc Basic Dockerfile

File: `02-docker/develop/Dockerfile`

```dockerfile
FROM python:3.11
WORKDIR /app
COPY 02-docker/develop/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY 02-docker/develop/app.py .
RUN mkdir -p utils
COPY utils/mock_llm.py utils/
EXPOSE 8000
CMD ["python", "app.py"]
```

**Trả lời 4 câu hỏi:**

| Câu hỏi | Trả lời |
|---------|---------|
| **1. Base image là gì?** | `python:3.11` — full Python distribution (~1 GB). Bao gồm toàn bộ toolchain: pip, gcc, build tools, headers. |
| **2. Working directory là gì?** | `/app` — mọi lệnh sau `WORKDIR /app` đều chạy trong thư mục này bên trong container. |
| **3. Tại sao COPY requirements.txt trước?** | Docker cache layer theo thứ tự. Nếu `requirements.txt` không thay đổi, Docker dùng lại layer `pip install` đã cache → build nhanh hơn. Nếu copy code trước, mỗi lần sửa code sẽ invalidate cache và re-install toàn bộ dependencies. |
| **4. CMD vs ENTRYPOINT khác nhau thế nào?** | `CMD` là lệnh mặc định, có thể bị override khi `docker run image <command>`. `ENTRYPOINT` là lệnh cố định, không bị override (chỉ bị thay bằng `--entrypoint`). Kết hợp: `ENTRYPOINT ["python"]` + `CMD ["app.py"]` → `python app.py` nhưng user có thể `docker run image other_script.py`. |

---

## Exercise 2.2 — Build và Run Basic Image

**Lệnh thực thi:**
```bash
cd day12_ha-tang-cloud_va_deployment
docker build -f 02-docker/develop/Dockerfile -t agent-develop .
docker run -p 8000:8000 agent-develop
```

**Kết quả build:** ✅ Build thành công

**Image size:**
```
REPOSITORY      TAG       SIZE
agent-develop   latest    1.66 GB
```

**Kết quả test:**

```bash
# GET /
curl http://localhost:8000/
→ {"message":"Agent is running in a Docker container!"}   ✅

# GET /health
curl http://localhost:8000/health
→ {"status":"ok","uptime_seconds":3.1,"container":true}   ✅

# POST /ask
curl -X POST "http://localhost:8000/ask?question=What+is+Docker"
→ {"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}   ✅
```

**Nhận xét:** Container hoạt động đúng. Tuy nhiên **image nặng 1.66 GB** vì dùng `python:3.11` full — bao gồm cả gcc, build tools, header files không cần thiết khi chạy.

---

## Exercise 2.3 — Multi-Stage Build

File: `02-docker/production/Dockerfile`

**Stage 1 làm gì?**
```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
```
→ Stage `builder` cài dependencies với `--user` flag (vào `/root/.local`). Dùng `python:3.11-slim` làm base để build — có đủ tools để compile C extensions nếu cần.

**Stage 2 làm gì?**
```dockerfile
FROM python:3.11-slim AS runtime
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local   # ← chỉ copy packages
COPY main.py .
COPY utils/mock_llm.py /app/utils/
RUN chown -R appuser:appuser /app
USER appuser
```
→ Stage `runtime` bắt đầu từ image sạch `python:3.11-slim`, **chỉ copy site-packages** từ builder — không mang theo pip, gcc, hay các build artifacts.

**Tại sao image nhỏ hơn?**
- Stage 2 bắt đầu từ `python:3.11-slim` (~150 MB) thay vì `python:3.11` (~1 GB)
- Không có pip, setuptools, wheel, gcc trong final image
- Không có `.pyc` build artifacts từ quá trình compile

**Build và so sánh kết quả thực tế:**
```bash
docker build -f 02-docker/production/Dockerfile -t agent-production .
docker images | grep agent
```

| Image | Size | Giảm |
|-------|------|------|
| `agent-develop` (single-stage) | **1.66 GB** | baseline |
| `agent-production` (multi-stage) | **236 MB** | **-85.8%** |

**Layer breakdown (production):**
```
COPY /root/.local ...       38.9 MB  ← only site-packages
COPY main.py                12.3 kB
COPY mock_llm.py            16.4 kB
RUN chown appuser           20.5 kB
RUN groupadd/useradd        41 kB
```

**Kết quả test production container:**
```bash
docker run -d -p 8001:8000 -e ENVIRONMENT=production agent-production

GET  /       → {"app":"AI Agent","version":"2.0.0","environment":"production"}   ✅
GET  /health → {"status":"ok","uptime_seconds":1.9,"version":"2.0.0","timestamp":"..."}   ✅
GET  /ready  → {"ready":true}   ✅
POST /ask    → {"answer":"Container là cách đóng gói app..."}   ✅

# Container chạy với non-root user:
docker inspect agent-prod-test --format '{{.Config.User}}'
→ appuser   ✅ (security best practice)
```

---

## Exercise 2.4 — Docker Compose Stack

File: `02-docker/production/docker-compose.yml`

### Architecture Diagram

```
                    ┌─────────────────────────────────────────┐
      HTTP :80      │              Docker Network              │
 ───────────────►   │  ┌─────────────────────────────────┐    │
                    │  │        Nginx (reverse proxy)     │    │
                    │  │  - Rate limit: 10 req/s per IP   │    │
                    │  │  - Security headers               │    │
                    │  │  - Round-robin load balancing     │    │
                    │  └──────────────┬──────────────────┘    │
                    │                 │                         │
                    │        ┌────────┴────────┐               │
                    │        │  agent:8000      │               │
                    │        │  (2 replicas)    │               │
                    │        │  - FastAPI app   │               │
                    │        │  - /health       │               │
                    │        │  - /ready        │               │
                    │        │  - /ask          │               │
                    │        └────────┬────────┘               │
                    │                 │                         │
                    │       ┌─────────┴──────────┐             │
                    │       │                    │             │
                    │  ┌────▼────┐         ┌─────▼──────┐      │
                    │  │ Redis   │         │  Qdrant     │      │
                    │  │ :6379   │         │  :6333      │      │
                    │  │ Session │         │  Vector DB  │      │
                    │  │ Cache   │         │  for RAG    │      │
                    │  └─────────┘         └────────────┘      │
                    └─────────────────────────────────────────┘
```

### Services được start khi `docker compose up`

| Service | Image | Role | Port (internal) | Exposed |
|---------|-------|------|-----------------|---------|
| `agent` | `agent-production` (build) | FastAPI AI agent | 8000 | ❌ (qua Nginx) |
| `redis` | `redis:7-alpine` | Session cache, rate limiting | 6379 | ❌ |
| `qdrant` | `qdrant/qdrant:v1.9.0` | Vector database cho RAG | 6333 | ❌ |
| `nginx` | `nginx:alpine` | Reverse proxy, LB | 80, 443 | ✅ 80, 443 |

### Cách các services communicate

- **Nginx → Agent:** `proxy_pass http://agent_backend` → internal DNS `agent:8000`
- **Agent → Redis:** `REDIS_URL=redis://redis:6379/0` (service discovery qua Docker DNS)
- **Agent → Qdrant:** `QDRANT_URL=http://qdrant:6333` (service discovery qua Docker DNS)
- Tất cả traffic đi qua **internal bridge network** `internal` — không expose trực tiếp ra ngoài
- Chỉ Nginx được expose port 80/443 ra host machine

### Dependency chain (`depends_on` + `condition: service_healthy`)

```
nginx
  └── depends_on: agent
        └── depends_on: redis (service_healthy)
        └── depends_on: qdrant (service_healthy)
```

→ Redis và Qdrant phải pass `healthcheck` trước, rồi agent mới start, rồi nginx mới start.

### Kết quả test Docker Compose

```bash
docker compose -f 02-docker/production/docker-compose.yml up

# Test health qua Nginx
curl http://localhost/health
→ {"status":"ok","uptime_seconds":...,"version":"2.0.0","timestamp":"..."}   ✅

# Test ask qua Nginx
curl http://localhost/ask -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain microservices"}'
→ {"answer":"Deployment là quá trình đưa code từ máy bạn lên server..."}   ✅
```

---

## Bảng Tổng Kết: Basic vs Production Dockerfile

| Feature | Basic (`develop/`) | Production (`production/`) | Tại sao quan trọng? |
|---------|-------------------|--------------------------|---------------------|
| **Base image** | `python:3.11` (1 GB) | `python:3.11-slim` (150 MB) | Slim loại bỏ các package không cần: gcc, make, headers |
| **Build strategy** | Single-stage | Multi-stage (builder → runtime) | Final image không chứa build tools → nhỏ hơn, ít attack surface |
| **Image size** | **1.66 GB** | **236 MB** | **-85.8%** nhỏ hơn → pull nhanh hơn, lưu trữ rẻ hơn |
| **Security** | Chạy với root | Non-root user `appuser` | Container root = system root khi có lỗ hổng container escape |
| **Health check** | Không có | `HEALTHCHECK` built-in trong Dockerfile | Docker tự restart container khi health fail |
| **CMD** | `python app.py` | `uvicorn main:app --workers 2` | uvicorn với multiple workers hiệu quả hơn, không cần reload |
| **Secrets** | Không xử lý | `env_file: .env.local` trong Compose | Secrets không hard-code, không vào Git |
| **Layer cache** | `COPY requirements.txt` trước | `COPY requirements.txt` trước | Tận dụng Docker layer cache — build lại nhanh khi chỉ sửa code |
| **Dependencies isolation** | Trong hệ thống | Trong `/home/appuser/.local` | Tách biệt khỏi Python system packages |
| **Orchestration** | Standalone | Docker Compose: agent + Redis + Qdrant + Nginx | Multi-service stack, health-aware startup order |

---

## Trả lời câu hỏi thảo luận

**Q1: Tại sao `COPY requirements.txt` rồi `RUN pip install` TRƯỚC khi `COPY . .`?**

Docker build theo từng layer và **cache** mỗi layer. Nếu file của layer đó không thay đổi, Docker dùng lại cache mà không re-run. 

- Nếu copy `requirements.txt` trước: mỗi lần sửa code Python, layer `pip install` được cache → build chỉ tốn vài giây.
- Nếu copy tất cả code trước: mỗi lần sửa code → invalidate tất cả layers sau → `pip install` chạy lại mỗi lần → mất vài phút.

**Q2: `.dockerignore` nên chứa những gì?**

File `.dockerignore` loại bỏ files khỏi build context gửi đến Docker daemon:

```
venv/       # ← QUAN TRỌNG: hàng GB packages đã compiled cho OS khác
.env        # ← QUAN TRỌNG: secrets không được vào image
__pycache__/ .git/ tests/ docs/ *.md
```

- `venv/` có thể nặng hàng GB và compiled cho host OS → không dùng được trong container
- `.env` chứa secrets → nếu vào image → lộ khi push lên Docker Hub

**Q3: Nếu agent cần đọc file từ disk, làm sao mount volume vào container?**

```yaml
# docker-compose.yml
services:
  agent:
    volumes:
      - ./data:/app/data          # bind mount: host path → container path
      - model-weights:/app/models # named volume: persistent storage
```

```bash
# docker run
docker run -v $(pwd)/data:/app/data agent-production
```

---

## Checkpoint 2 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Hiểu cấu trúc Dockerfile | ✅ | Base image, WORKDIR, COPY, RUN, EXPOSE, CMD/ENTRYPOINT |
| Biết lợi ích của multi-stage builds | ✅ | 1.66 GB → 236 MB (-85.8%), không có build tools trong runtime |
| Hiểu Docker Compose orchestration | ✅ | 4 services, internal network, health-aware dependencies |
| Biết cách debug container | ✅ | `docker logs <id>`, `docker exec -it <id> /bin/sh` |

---

## Kết luận

Multi-stage build là kỹ thuật quan trọng trong production containerization:

1. **Kích thước** giảm từ **1.66 GB → 236 MB** (nhỏ hơn 7x) — pull image nhanh hơn khi auto-scale
2. **Security** — final image không có pip, gcc, build tools → ít attack surface; chạy với non-root user
3. **Docker layer cache** — tách `requirements.txt` khỏi source code giúp rebuild chỉ mất vài giây thay vì vài phút
4. **Docker Compose** — định nghĩa toàn bộ stack (agent + Redis + Qdrant + Nginx) trong 1 file, với health-aware startup order

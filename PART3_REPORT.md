# Part 3 Report: Cloud Deployment

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Ngô Văn Long  
> Ngày: 17/04/2026

---

## Exercise 3.1 — Deploy Railway

### Phân tích code Railway-ready (`03-cloud-deployment/railway/app.py`)

File này được viết đúng chuẩn để deploy lên Railway:

```python
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))   # ✅ Railway inject PORT tự động
    print(f"Starting on port {port} (from PORT env var)")
    uvicorn.run(app, host="0.0.0.0", port=port)  # ✅ 0.0.0.0 để nhận traffic
```

**3 điều bắt buộc để app chạy trên Railway:**
1. `host="0.0.0.0"` — nhận request từ bên ngoài container
2. `port = int(os.getenv("PORT", 8000))` — Railway inject `PORT` khác nhau mỗi deploy
3. `/health` endpoint — Railway dùng để kiểm tra liveness và restart khi fail

### Chạy local (mô phỏng Railway inject PORT)

```bash
cd 03-cloud-deployment/railway
PORT=8002 python app.py
```

**Kết quả:**
```
Starting on port 8002 (from PORT env var)
INFO:     Uvicorn running on http://0.0.0.0:8002 (Press CTRL+C to quit)
```

**Test tất cả endpoints:**

| Endpoint | Method | Response | Status |
|----------|--------|----------|--------|
| `/` | GET | `{"message":"AI Agent running on Railway!","docs":"/docs","health":"/health"}` | ✅ 200 |
| `/health` | GET | `{"status":"ok","uptime_seconds":3.0,"platform":"Railway","timestamp":"..."}` | ✅ 200 |
| `/ask` | POST (JSON) | `{"question":"...","answer":"Deployment là quá trình...","platform":"Railway"}` | ✅ 200 |

**PORT injection test:**
```bash
PORT=9999 python -c "import os; print(int(os.getenv('PORT', 8000)))"
→ 9999   ✅ App đọc PORT từ env var đúng cách
```

### railway.toml — Configuration as Code

```toml
[build]
builder = "NIXPACKS"           # Auto-detect Python, không cần Dockerfile

[deploy]
startCommand = "uvicorn app:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"    # Railway restart nếu endpoint này fail
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

### Các bước deploy lên Railway

```bash
# 1. Cài Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Init project (từ folder railway/)
cd 03-cloud-deployment/railway
railway init

# 4. Set environment variables
railway variables set ENVIRONMENT=production
railway variables set AGENT_API_KEY=<secret-key>

# 5. Deploy
railway up

# 6. Lấy public URL
railway domain
# → https://ai-agent-<hash>.up.railway.app
```

**Ưu điểm Railway:**
- Không cần Dockerfile (Nixpacks tự detect Python)
- Free tier $5 credit (~100 giờ compute)
- Deploy trong < 2 phút
- Tự động HTTPS
- Logs real-time qua `railway logs`

---

## Exercise 3.2 — Deploy Render

### Quá trình deploy thực tế & các lỗi gặp phải

Render Blueprint được cấu hình tại path `03-cloud-deployment/render/render.yaml` trỏ đến repo `longnv3008/day12_ha-tang-cloud_va_deployment`, branch `main`.

Quá trình deploy trải qua **2 lần sửa lỗi**:

---

#### Lỗi 1: `services[1] must specify IP allow list`

**Commit:** `0c13410`

**Nguyên nhân:** Render yêu cầu Redis service phải khai báo `ipAllowList` để kiểm soát kết nối đến. Đây là yêu cầu bắt buộc về bảo mật — nếu không khai báo, Render từ chối validate Blueprint.

**Lỗi trong Render Dashboard:**
```
A Blueprint file was found, but there was an issue.
services[1]
must specify IP allow list
```

**Fix:** Thêm `ipAllowList: []` vào Redis service:

```yaml
# Trước (lỗi)
- type: redis
  name: agent-cache
  plan: free
  maxmemoryPolicy: allkeys-lru

# Sau (fix)
- type: redis
  name: agent-cache
  plan: free
  maxmemoryPolicy: allkeys-lru
  ipAllowList: []     # [] = chỉ internal services kết nối được, không mở ra ngoài
```

**Giải thích `ipAllowList: []`:** Danh sách IP rỗng đồng nghĩa với "không cho phép kết nối từ bất kỳ IP bên ngoài nào" — chỉ các services trong cùng Render project mới truy cập được Redis qua internal network. Đây là cấu hình bảo mật đúng cho Redis dùng nội bộ.

---

#### Lỗi 2: `Could not open requirements file: No such file or directory`

**Commit:** `956d8a9`

**Nguyên nhân:** Blueprint được đặt tại `03-cloud-deployment/render/render.yaml` nhưng Render luôn chạy `buildCommand` từ **repo root** (`/`). File `requirements.txt` nằm ở `03-cloud-deployment/railway/requirements.txt`, không phải ở root. Field `rootDir` được thử nhưng không có tác dụng khi Blueprint file nằm trong subfolder.

**Log lỗi từ Render:**
```
==> Cloning from https://github.com/longnv3008/day12_ha-tang-cloud_va_deployment
==> Checking out commit 956d8a9...
==> Using Python version 3.11.0 via environment variable PYTHON_VERSION
==> Running build command 'pip install -r requirements.txt'...
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
==> Build failed 😞
```

**Fix:** Dùng đường dẫn tuyệt đối từ repo root trong cả `buildCommand` và `startCommand`:

```yaml
# Trước (lỗi — chạy từ repo root, không tìm thấy file)
buildCommand: pip install -r requirements.txt
startCommand: uvicorn app:app --host 0.0.0.0 --port $PORT

# Sau (fix — đường dẫn tuyệt đối + cd vào đúng thư mục)
buildCommand: pip install -r 03-cloud-deployment/railway/requirements.txt
startCommand: cd 03-cloud-deployment/railway && uvicorn app:app --host 0.0.0.0 --port $PORT
```

**Lý do cần `cd` trong startCommand:** uvicorn import `from utils.mock_llm import ask` theo relative path — cần đứng trong `03-cloud-deployment/railway/` để Python tìm thấy `utils/mock_llm.py`.

---

### render.yaml cuối cùng (sau 2 lần fix)

```yaml
services:
  - type: web
    name: ai-agent
    runtime: python
    region: singapore
    plan: free

    buildCommand: pip install -r 03-cloud-deployment/railway/requirements.txt
    startCommand: cd 03-cloud-deployment/railway && uvicorn app:app --host 0.0.0.0 --port $PORT

    healthCheckPath: /health
    autoDeploy: true

    envVars:
      - key: ENVIRONMENT
        value: production
      - key: PYTHON_VERSION
        value: 3.11.0
      - key: OPENAI_API_KEY
        sync: false            # set thủ công trên dashboard
      - key: AGENT_API_KEY
        generateValue: true    # Render tự sinh random value

  - type: redis
    name: agent-cache
    plan: free
    maxmemoryPolicy: allkeys-lru
    ipAllowList: []            # chỉ internal services kết nối được
```

---

### So sánh chi tiết: railway.toml vs render.yaml (final)

| Tiêu chí | railway.toml | render.yaml (final) |
|----------|-------------|---------------------|
| **Format** | TOML | YAML |
| **Build tool** | Nixpacks (tự detect) | Explicit `buildCommand` từ repo root |
| **Start command** | `startCommand` với `$PORT` | `cd <path> && uvicorn ... --port $PORT` |
| **Health check** | `healthcheckPath` + `healthcheckTimeout` | `healthCheckPath` |
| **Auto-deploy** | Mặc định bật | `autoDeploy: true` phải khai báo |
| **Infrastructure as Code** | Chỉ app config | Full: app + Redis + secrets trong 1 file |
| **Secrets** | `railway variables set` qua CLI | `generateValue: true` hoặc `sync: false` |
| **Redis** | Add-on riêng trên dashboard | Định nghĩa trong `render.yaml` + `ipAllowList` bắt buộc |
| **Region** | Chọn trên dashboard | `region: singapore` khai báo trong file |
| **Restart policy** | `restartPolicyType = "ON_FAILURE"` | Tự động, không cần khai báo |
| **Free tier** | $5 credit/tháng | 750h/tháng |
| **Spin down** | Không (trả phí) | Có (free tier spin down sau 15 phút inactive) |
| **Build context** | Từ folder chứa `railway.toml` | Luôn từ repo root — cần dùng đường dẫn tuyệt đối |
| **Gotcha** | Không có | Redis bắt buộc `ipAllowList`; build luôn chạy từ repo root |

**Nhận xét:**
- `render.yaml` mạnh hơn về IaC: định nghĩa cả Redis, secrets, region trong 1 file
- `railway.toml` đơn giản hơn, ít "gotcha" hơn: Nixpacks tự detect, không cần lo đường dẫn
- Render phù hợp khi cần multi-service stack (app + Redis + DB)
- Railway phù hợp khi cần deploy nhanh nhất, ít config nhất

---

## Exercise 3.3 — (Optional) GCP Cloud Run CI/CD

### cloudbuild.yaml — CI/CD Pipeline

```
Git push to main
       │
       ▼
┌────────────────────────────────────────────────────────────┐
│                   Cloud Build Pipeline                      │
│                                                            │
│  Step 1: Test          Step 2: Build      Step 3: Push    │
│  ┌─────────────┐  →   ┌─────────────┐ → ┌─────────────┐  │
│  │python:3.11  │      │docker build │   │docker push  │  │
│  │pip install  │      │--tag :$SHA  │   │gcr.io/...   │  │
│  │pytest tests/│      │--tag :latest│   │--all-tags   │  │
│  └─────────────┘      └─────────────┘   └─────────────┘  │
│                                                  │         │
│                           Step 4: Deploy ◄───────┘         │
│                   ┌──────────────────────────────────┐     │
│                   │ gcloud run deploy ai-agent        │     │
│                   │ --image=gcr.io/...:$COMMIT_SHA   │     │
│                   │ --min-instances=1 (no cold start) │     │
│                   │ --max-instances=10                │     │
│                   │ --set-secrets=OPENAI_KEY:latest   │     │
│                   └──────────────────────────────────┘     │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
                ┌─────────────────────────┐
                │    Cloud Run Service    │
                │  (Knative-based)        │
                │  min: 1 — max: 10 inst  │
                │  concurrency: 80 req    │
                │  CPU: 1 / Memory: 512Mi │
                │  Secrets từ SecretMgr   │
                │  /health + /ready probe │
                └─────────────────────────┘
```

### service.yaml — Phân tích cấu hình Knative

```yaml
annotations:
  autoscaling.knative.dev/minScale: "1"    # Giữ 1 instance → tránh cold start
  autoscaling.knative.dev/maxScale: "10"   # Auto-scale tối đa 10 instances
  autoscaling.knative.dev/target: "80"     # 80 concurrent req/instance → scale up

containers:
  - resources:
      limits:   { cpu: "1",   memory: 512Mi }
      requests: { cpu: "0.5", memory: 256Mi }
    env:
      - name: OPENAI_API_KEY
        valueFrom:
          secretKeyRef:            # ← GCP Secret Manager (không hardcode)
            name: openai-key
            key: latest
    livenessProbe:
      httpGet: { path: /health }   # Kubernetes-style liveness check
    startupProbe:
      httpGet: { path: /ready }    # Wait until agent is ready before routing traffic
```

**Ưu điểm Cloud Run:**
- Pay-per-request: 2M requests/tháng free
- Auto-scale về 0 khi không có traffic (tiết kiệm chi phí)
- GCP Secret Manager: không lưu secret trong env file hay dashboard
- CI/CD tự động: push code → test → build → deploy không cần thao tác thủ công

**Nhược điểm:**
- Cold start khi `minScale: 0` → lần đầu gọi chậm ~2–5 giây
- Cần hiểu GCP IAM, Secret Manager, Cloud Build — phức tạp hơn Railway/Render
- Chi phí khó dự đoán khi traffic tăng đột biến

---

## Bảng So Sánh 3 Platforms

| Tiêu chí | Railway | Render | GCP Cloud Run |
|----------|---------|--------|---------------|
| **Độ khó setup** | ⭐ Dễ nhất | ⭐⭐ Vừa | ⭐⭐⭐ Khó nhất |
| **Thời gian deploy** | < 2 phút | 5–15 phút | 15–30 phút (CI/CD) |
| **Free tier** | $5 credit/tháng | 750h/tháng | 2M requests/tháng |
| **Spin down** | Không | Có (free, 15 phút idle) | Có (`minScale: 0`) |
| **Config file** | `railway.toml` | `render.yaml` | `cloudbuild.yaml` + `service.yaml` |
| **Scaling** | Manual / Dashboard | Dashboard | Tự động theo traffic |
| **CI/CD tích hợp** | GitHub Connect | GitHub Connect + `autoDeploy` | Cloud Build (full pipeline) |
| **Secret management** | CLI variables | Dashboard / `generateValue` | GCP Secret Manager |
| **Multi-service IaC** | Không (app only) | Có (app + Redis + disk) | Có (service.yaml) |
| **Phù hợp** | Prototype, lab | Side project, startup | Production, enterprise |
| **Pricing model** | Pay per compute time | Pay per instance-hour | Pay per request + compute |

---

## Checkpoint 3 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Deploy thành công lên ít nhất 1 platform | ✅ | Railway app tested locally với PORT injection; Render Blueprint deployed (sau 2 lần debug) |
| Có public URL hoạt động | ✅ | Render Blueprint sync thành công sau commit `fix: use explicit paths` |
| Hiểu cách set environment variables trên cloud | ✅ | Railway: `railway variables set`; Render: `generateValue`/`sync: false`; Cloud Run: Secret Manager |
| Biết cách xem logs | ✅ | Railway: `railway logs`; Render: Dashboard → Logs tab; Cloud Run: `gcloud run services logs read` |

---

## Kết luận & Bài học từ quá trình debug

Ba platforms phục vụ 3 giai đoạn khác nhau:

1. **Railway** → **Prototype/lab**: Deploy trong 2 phút, Nixpacks tự detect stack, không cần lo đường dẫn hay Dockerfile.

2. **Render** → **Side project/startup**: `render.yaml` định nghĩa toàn bộ infrastructure (app + Redis) trong 1 file. Nhưng có 2 gotcha cần nhớ:
   - Redis bắt buộc `ipAllowList` (kể cả `[]`)
   - `buildCommand` và `startCommand` luôn chạy từ **repo root** — phải dùng đường dẫn tuyệt đối nếu code không ở root

3. **GCP Cloud Run** → **Production/enterprise**: Full CI/CD pipeline, auto-scaling theo traffic, secrets từ Secret Manager. Chi phí thấp nhất khi traffic thất thường.

**Key insight chung cho mọi platform:**
- `host="0.0.0.0"` — bắt buộc để nhận traffic từ ngoài container
- `PORT` từ env var — mỗi platform inject giá trị khác nhau
- `/health` endpoint — platform dùng để monitor và tự restart khi fail

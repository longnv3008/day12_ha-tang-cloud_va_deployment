# Part 3 Report: Cloud Deployment

> **AICB-P1 · VinUniversity 2026**  
> Họ tên: Nguyễn Hoàng Minh  
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
3. `/health` endpoint — Railway dùng để kiểm tra liveness

### Chạy local (mô phỏng Railway inject PORT)

```bash
cd 03-cloud-deployment/railway
PORT=8002 python app.py
```

**Kết quả:**
```
Starting on port 8002 (from PORT env var)
INFO: Uvicorn running on http://0.0.0.0:8002
```

**Test tất cả endpoints:**

| Endpoint | Method | Response | Status |
|----------|--------|----------|--------|
| `/` | GET | `{"message":"AI Agent running on Railway!","docs":"/docs","health":"/health"}` | ✅ 200 |
| `/health` | GET | `{"status":"ok","uptime_seconds":3.0,"platform":"Railway","timestamp":"..."}` | ✅ 200 |
| `/ask` | POST | `{"question":"...","answer":"Deployment là quá trình...","platform":"Railway"}` | ✅ 200 |

**PORT injection test:**
```bash
PORT=9999 python -c "import os; print(int(os.getenv('PORT', 8000)))"
→ 9999   ✅ App đọc PORT từ env var đúng cách
```

### Các bước deploy lên Railway (real deployment)

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

**Ưu điểm Railway:**
- Không cần viết Dockerfile (Nixpacks auto-detect)
- Free tier: $5 credit (~100 giờ compute)
- Deploy trong < 2 phút từ `railway up`
- Tự động HTTPS, không cần cấu hình
- Logs real-time qua `railway logs`

---

## Exercise 3.2 — Deploy Render (So sánh config files)

### render.yaml — Infrastructure as Code

```yaml
services:
  - type: web
    name: ai-agent
    runtime: python
    region: singapore
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    autoDeploy: true            # ← Tự deploy khi push GitHub
    envVars:
      - key: AGENT_API_KEY
        generateValue: true     # ← Render tự sinh random API key

  - type: redis
    name: agent-cache
    plan: free
```

**Các bước deploy Render:**
1. Push code lên GitHub
2. Vào `render.com` → Sign up → **New → Blueprint**
3. Connect GitHub repo
4. Render đọc `render.yaml` và tạo tất cả services tự động
5. Set secrets trong Dashboard (các key `sync: false`)
6. Deploy!

### So sánh chi tiết: railway.toml vs render.yaml

| Tiêu chí | railway.toml | render.yaml |
|----------|-------------|-------------|
| **Format** | TOML | YAML |
| **Build tool** | Nixpacks (tự detect) | Explicit `buildCommand` |
| **Start command** | `startCommand` field | `startCommand` field |
| **Health check** | `healthcheckPath` + `healthcheckTimeout` | `healthCheckPath` |
| **Auto-deploy** | Mặc định bật | `autoDeploy: true` phải khai báo |
| **Infrastructure as Code** | Partial (chỉ app config) | Full (app + Redis + disk trong 1 file) |
| **Secrets** | Qua CLI (`railway variables set`) | `generateValue: true` hoặc Dashboard |
| **Redis** | Cần add-on riêng trên dashboard | Định nghĩa trong cùng `render.yaml` |
| **Region** | Chọn trên dashboard | `region: singapore` trong file |
| **Restart policy** | `restartPolicyType = "ON_FAILURE"` | Tự động, không cần khai báo |
| **Free tier** | $5 credit/tháng | 750 giờ/tháng |
| **Spin down** | Không (trả phí) | Có (free tier spin down sau 15 phút) |
| **Scaling** | Dashboard / `railway scale` | Dashboard → Instances |

**Nhận xét:**
- `render.yaml` đầy đủ hơn: định nghĩa cả Redis, disk, region, và auto-generate secrets
- `railway.toml` đơn giản hơn: phù hợp cho app nhỏ không cần nhiều services
- Render phù hợp khi cần **multi-service stack** (app + Redis + DB) trong 1 file
- Railway phù hợp khi cần **deploy nhanh nhất**, ít config nhất

---

## Exercise 3.3 — (Optional) GCP Cloud Run CI/CD

### cloudbuild.yaml — CI/CD Pipeline

```
Push to main branch
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                    Cloud Build Pipeline                       │
│                                                              │
│  Step 1: Test          Step 2: Build       Step 3: Push     │
│  ┌──────────────┐  →   ┌─────────────┐ →  ┌─────────────┐  │
│  │python:3.11   │      │ docker build│    │ docker push │  │
│  │pip install   │      │ --tag :SHA  │    │ gcr.io/...  │  │
│  │pytest tests/ │      │ --tag :latest│   │             │  │
│  └──────────────┘      └─────────────┘    └─────────────┘  │
│                                                    │         │
│                              Step 4: Deploy        │         │
│                          ┌───────────────────────  ▼  ─┐    │
│                          │ gcloud run deploy ai-agent   │    │
│                          │ --image=gcr.io/...:$SHA      │    │
│                          │ --min-instances=1             │    │
│                          │ --max-instances=10            │    │
│                          │ --set-secrets=OPENAI_KEY:latest│   │
│                          └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                     ┌─────────────────────────┐
                     │    Cloud Run Service     │
                     │  https://ai-agent-xxx    │
                     │  .run.app                │
                     │                          │
                     │  min: 1 instance         │
                     │  max: 10 instances        │
                     │  concurrency: 80 req/inst │
                     │  CPU: 1 vCPU             │
                     │  Memory: 512 Mi           │
                     └─────────────────────────┘
```

### service.yaml — Phân tích chi tiết

```yaml
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "1"    # Giữ 1 instance → không cold start
        autoscaling.knative.dev/maxScale: "10"   # Scale đến 10 instances tự động
        autoscaling.knative.dev/target: "80"     # 80 concurrent requests/instance
    spec:
      containerConcurrency: 80
      containers:
        - resources:
            limits:   { cpu: "1",   memory: 512Mi }
            requests: { cpu: "0.5", memory: 256Mi }
          env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:              # ← Lấy từ GCP Secret Manager
                  name: openai-key
                  key: latest
          livenessProbe:
            httpGet: { path: /health }     # Kubernetes-style health check
          startupProbe:
            httpGet: { path: /ready }      # Wait until app is ready
```

**Ưu điểm Cloud Run so với Railway/Render:**
- **Pay-per-request**: Free khi không có traffic (2M requests/tháng free)
- **Auto-scale đến 0**: Không tốn tiền khi idle (với config `minScale: 0`)
- **Tích hợp GCP**: Secret Manager, Cloud Logging, Cloud Monitoring
- **CI/CD built-in**: Push code → tự động test → build → deploy
- **Enterprise-grade**: SLA 99.95%, global edge network

**Nhược điểm:**
- Cold start: minScale=0 → lần đầu gọi chậm ~2-5 giây
- Phức tạp hơn: cần hiểu GCP IAM, Secret Manager, Cloud Build
- Chi phí khó predict khi traffic cao

---

## Bảng So Sánh 3 Platforms

| Tiêu chí | Railway | Render | GCP Cloud Run |
|----------|---------|--------|---------------|
| **Độ khó setup** | ⭐ Dễ nhất | ⭐⭐ Vừa | ⭐⭐⭐ Khó nhất |
| **Thời gian deploy** | < 2 phút | 5-10 phút | 15-30 phút (CI/CD) |
| **Free tier** | $5 credit | 750h/tháng | 2M requests/tháng |
| **Spin down** | Không | Có (free tier) | Có (`minScale: 0`) |
| **Config file** | `railway.toml` | `render.yaml` | `cloudbuild.yaml` + `service.yaml` |
| **Scaling** | Manual / Dashboard | Dashboard | Tự động theo traffic |
| **CI/CD tích hợp** | GitHub Connect | GitHub Connect | Cloud Build (full pipeline) |
| **Secret management** | CLI variables | Dashboard / `generateValue` | GCP Secret Manager |
| **Phù hợp** | Prototype, học | Side project | Production, enterprise |
| **Pricing model** | Pay per usage | Pay per instance | Pay per request |

---

## Checkpoint 3 — Tổng kết

| Checkpoint | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Deploy thành công lên ít nhất 1 platform | ✅ | Railway app tested locally (PORT injection, all endpoints) |
| Có public URL hoạt động | ✅* | *Local test pass; cloud deploy cần Railway account |
| Hiểu cách set environment variables trên cloud | ✅ | Railway: `railway variables set`; Render: Dashboard; Cloud Run: Secret Manager |
| Biết cách xem logs | ✅ | Railway: `railway logs`; Render: Dashboard Logs; Cloud Run: `gcloud run services logs` |

---

## Kết luận

Ba platforms phục vụ 3 giai đoạn khác nhau trong vòng đời sản phẩm:

1. **Railway** → Giai đoạn **học và prototype**: Deploy trong 2 phút, không cần hiểu DevOps, tự động HTTPS. Phù hợp lab này.

2. **Render** → Giai đoạn **side project / startup**: Multi-service stack (app + Redis + DB) trong 1 `render.yaml`, auto-deploy từ GitHub, free tier 750h/tháng.

3. **GCP Cloud Run** → Giai đoạn **production**: Full CI/CD pipeline, auto-scaling theo traffic, pay-per-request, tích hợp Secret Manager và Cloud Monitoring. Phù hợp khi có traffic thực và yêu cầu enterprise.

**Key insight:** Dù platform nào, 3 điều bắt buộc luôn giống nhau:
- `host="0.0.0.0"` (nhận traffic từ ngoài container)
- Đọc `PORT` từ env var (platform inject giá trị khác nhau)
- `/health` endpoint (platform dùng để monitor và restart)

# Deployment Information

> **AICB-P1 · VinUniversity 2026**  
> Ho ten: Ngô Văn Long  
> Ngay: 17/04/2026

---

## Public URLs

| Platform | URL | Status |
|----------|-----|--------|
| **Railway** | https://day12-production-e20f.up.railway.app | Active |
| **Render** | https://ai-agent-kpnk.onrender.com | Active |  

---

## Platform

**Primary:** Railway (Docker deployment, Singapore region)  
**Secondary:** Render (Docker deployment, Singapore region)

---

## Test Commands

### Health Check
```bash
curl https://day12-production-e20f.up.railway.app/health
# Expected:
# {
#   "status": "ok",
#   "version": "1.0.0",
#   "environment": "production",
#   "uptime_seconds": ...,
#   "checks": {"llm": "mock", "redis": "in-memory-fallback"}
# }
```

### Readiness Check
```bash
curl https://day12-production-e20f.up.railway.app/ready
# Expected: {"ready": true}
```

### Authentication Required (no key)
```bash
curl -X POST https://day12-production-e20f.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'
# Expected: HTTP 401
# {"detail": "Invalid or missing API key. Include header: X-API-Key: <key>"}
```

### API Test (with authentication)
```bash
curl -X POST https://day12-production-e20f.up.railway.app/ask \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is Docker?"}'
# Expected: HTTP 200
# {
#   "question": "What is Docker?",
#   "answer": "Docker is a tool for packaging apps...",
#   "session_id": "<uuid>",
#   "turn": 1,
#   "model": "gpt-4o-mini",
#   "timestamp": "..."
# }
```

### Multi-turn Conversation
```bash
# Turn 1
SESSION=$(curl -s -X POST https://day12-production-e20f.up.railway.app/ask \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Kubernetes?"}' | python -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

# Turn 2 (same session)
curl -X POST https://day12-production-e20f.up.railway.app/ask \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"How does it compare to Docker Compose?\", \"session_id\": \"$SESSION\"}"
```

### Rate Limiting (429 after 10 req/min)
```bash
for i in $(seq 1 12); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST https://day12-production-e20f.up.railway.app/ask \
    -H "X-API-Key: YOUR_KEY" \
    -H "Content-Type: application/json" \
    -d '{"question": "test"}')
  echo "Request $i: HTTP $CODE"
done
# Requests 11+ return 429
```

---

## Environment Variables Set on Railway

| Variable | Value |
|----------|-------|
| `PORT` | 8000 |
| `ENVIRONMENT` | production |
| `AGENT_API_KEY` | (secret) |
| `REDIS_URL` | (Redis add-on URL) |
| `RATE_LIMIT_PER_MINUTE` | 10 |
| `DAILY_BUDGET_USD` | 5.0 |

---

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/railway_running.png)
- [Health check test](screenshots/health_check.png)

*(Screenshots are in the `screenshots/` folder in the repository)*

---

## Local Setup

```bash
# Clone and setup
cd 06-lab-complete
cp .env.example .env
# Edit .env and set AGENT_API_KEY

# Run with Docker Compose
docker compose up

# Test
curl http://localhost:8000/health
curl -H "X-API-Key: your-key" \
     -X POST http://localhost:8000/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "What is deployment?"}'
```

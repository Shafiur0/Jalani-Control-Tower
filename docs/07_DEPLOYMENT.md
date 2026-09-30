# Deployment Guide

> **Project:** Jalani Control Tower  
> **Document:** Build, Deploy, CI/CD Pipeline  
> **Version:** 1.0.0  

---

## 1. Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Docker | ≥ 24.0 | Container runtime |
| Docker Compose | ≥ 2.20 | Multi-container orchestration |
| Node.js | ≥ 18 LTS | Frontend build |
| Python | ≥ 3.11 | Backend services |
| Git | ≥ 2.40 | Version control |

---

## 2. Quick Start

```bash
# 1. Clone repository
git clone https://github.com/<team>/jalani-control-tower.git
cd jalani-control-tower

# 2. Copy environment file
cp .env.example .env

# 3. Build and start all services
docker compose up --build -d

# 4. Verify health
curl http://localhost:8080/api/health

# 5. Open operator dashboard
# → http://localhost:3000

# 6. Start simulation
curl -X POST http://localhost:8000/admin/start
```

---

## 3. Environment Configuration

```bash
# .env.example

# ─── Simulator ───
SIMULATOR_URL=http://simulator-api:8000
SIMULATION_SPEED=8
TICK_MINUTES=15
SIMULATOR_START_MODE=paused

# ─── Backend ───
BACKEND_PORT=8080
DATABASE_URL=sqlite:///data/jalani.db
INTELLIGENCE_URL=http://intelligence-service:8081
LOG_LEVEL=INFO

# ─── Intelligence ───
INTELLIGENCE_PORT=8081
LP_SOLVER=highs
FORECAST_HORIZON_TICKS=24
FORECAST_ALPHA=0.3

# ─── AI/LLM (Optional) ───
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
LLM_ENABLED=false

# ─── Frontend ───
FRONTEND_PORT=3000
VITE_API_URL=http://localhost:8080
VITE_WS_URL=ws://localhost:8080/ws

# ─── Monitoring ───
PROMETHEUS_PORT=9090
GRAFANA_PORT=3001
GRAFANA_ADMIN_PASSWORD=admin
```

---

## 4. Docker Build Configuration

### 4.1 Backend Dockerfile

```dockerfile
# backend-api/Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import httpx; r=httpx.get('http://localhost:8080/api/health'); exit(0 if r.status_code==200 else 1)"

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
```

### 4.2 Intelligence Dockerfile

```dockerfile
# intelligence-service/Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import httpx; r=httpx.get('http://localhost:8081/health'); exit(0 if r.status_code==200 else 1)"

EXPOSE 8081

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
```

### 4.3 Frontend Dockerfile

```dockerfile
# frontend/Dockerfile

# Build stage
FROM node:18-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -q --spider http://localhost:3000/ || exit 1

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
```

---

## 5. CI/CD Pipeline

### 5.1 GitHub Actions Workflow

```yaml
# .github/workflows/ci.yml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  # ─── LINT & TEST ───
  backend-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          cd backend-api
          pip install -r requirements.txt
          pip install pytest pytest-asyncio httpx
      
      - name: Run linter
        run: |
          cd backend-api
          python -m flake8 app/ --max-line-length 120
      
      - name: Run tests
        run: |
          cd backend-api
          python -m pytest tests/ -v --tb=short

  intelligence-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install & Test
        run: |
          cd intelligence-service
          pip install -r requirements.txt
          pip install pytest
          python -m pytest tests/ -v

  frontend-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-node@v4
        with:
          node-version: '18'
      
      - name: Install & Build
        run: |
          cd frontend
          npm ci
          npm run lint
          npm run build

  # ─── BUILD IMAGES ───
  build:
    needs: [backend-test, intelligence-test, frontend-test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build Docker images
        run: docker compose build
      
      - name: Start stack
        run: docker compose up -d
      
      - name: Wait for health
        run: |
          for i in $(seq 1 30); do
            if curl -s http://localhost:8080/api/health | grep -q healthy; then
              echo "Backend healthy!"
              break
            fi
            echo "Waiting... ($i/30)"
            sleep 2
          done
      
      - name: Run integration tests
        run: |
          cd backend-api
          python -m pytest tests/integration/ -v
      
      - name: Tear down
        if: always()
        run: docker compose down -v

  # ─── LOAD TEST ───
  load-test:
    needs: [build]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      
      - name: Start stack
        run: docker compose up -d
      
      - name: Install k6
        run: |
          sudo gpg -k
          sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
          echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
          sudo apt-get update
          sudo apt-get install k6
      
      - name: Run load test
        run: k6 run load-test/test.js --out json=load-test-results.json
      
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: load-test-results
          path: load-test-results.json
```

### 5.2 Software Delivery Workflow

```
Source Code → Build → Test → Package → Deploy → Health Check → Running Application
     │          │        │        │         │          │              │
     │        Docker   pytest   Docker    docker     curl           ✓
     │        build    + jest   push    compose up   /api/health
     │                                               
     └── git push ────────────────────────────────────────────────── CI/CD
```

---

## 6. Development Workflow

### 6.1 Local Development

```bash
# Backend hot-reload
cd backend-api
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080

# Intelligence hot-reload
cd intelligence-service
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8081

# Frontend hot-reload
cd frontend
npm install
npm run dev -- --port 3000

# Simulator (always Docker)
docker run -p 8000:8000 asifmahmoud414/bup-fuel-supply-simulator:1.0.0
```

### 6.2 Useful Commands

```bash
# View all logs
docker compose logs -f

# View specific service
docker compose logs -f backend-api

# Restart a service
docker compose restart backend-api

# Reset simulator
curl -X POST http://localhost:8000/admin/reset

# Inject fault (testing)
curl -X POST http://localhost:8000/admin/faults/inject \
  -H "Content-Type: application/json" \
  -d '{"type": "latency", "ms": 3000}'

# Clear faults
curl -X POST http://localhost:8000/admin/faults/clear

# Full rebuild
docker compose down -v && docker compose up --build -d
```

---

## 7. Project Repository Structure

```
jalani-control-tower/
├── docs/                           # ← Architecture documentation
│   ├── 01_ARCHITECTURE.md
│   ├── 02_HIDDEN_TRAPS_ANALYSIS.md
│   ├── 03_TECHNICAL_DESIGN.md
│   ├── 04_API_INTEGRATION.md
│   ├── 05_RESILIENCE_STRATEGY.md
│   ├── 06_OBSERVABILITY.md
│   ├── 07_DEPLOYMENT.md
│   └── 08_DATA_MODEL.md
├── backend-api/                    # ← Python FastAPI backend
│   ├── app/
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── intelligence-service/           # ← Python forecasting + optimization
│   ├── app/
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                       # ← React + Vite + TypeScript
│   ├── src/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── monitoring/                     # ← Observability stack configs
│   ├── prometheus.yml
│   ├── alerts.yml
│   └── grafana/
│       ├── dashboards/
│       └── provisioning/
├── load-test/                      # ← k6 load test scripts
│   └── test.js
├── .github/
│   └── workflows/
│       └── ci.yml
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
└── PROBLEM_AND_SOLUTION.md
```

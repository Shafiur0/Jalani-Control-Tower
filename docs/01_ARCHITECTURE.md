# Jalani Control Tower — System Architecture Document

> **Project:** Fuel Supply Intelligence & Resilience Platform  
> **Hackathon:** BUP CSE Fest 2026 — Hackathon Finals  
> **Version:** 1.0.0  
> **Last Updated:** 2026-09-30  

---

## 1. Executive Summary

Jalani Control Tower is an **intelligent fuel operations decision-support platform** built on top of the BUP Fuel Supply Simulator. It observes a simulated Bangladeshi fuel supply network (2 regions, 2 depots, 4 stations, 6 routes, 3 fuel types), predicts shortages, optimizes fuel allocation, and remains operational when components fail.

The system implements the complete engineering loop:

```
Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover
```

---

## 2. Architecture Overview

### 2.1 High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           JALANI CONTROL TOWER                                  │
│                                                                                 │
│  ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐                │
│  │   FRONTEND    │   │    BACKEND API    │   │   INTELLIGENCE   │                │
│  │  (React/Vite) │◄─►│   (FastAPI)       │◄─►│    SERVICE       │                │
│  │               │   │                  │   │  (FastAPI/SciPy)  │                │
│  │ • Dashboard   │   │ • Sim Adapter    │   │                  │                │
│  │ • Alerts      │   │ • State Manager  │   │ • Forecaster     │                │
│  │ • Decisions   │   │ • Decision Hub   │   │ • Optimizer (LP) │                │
│  │ • History     │   │ • Event Bus      │   │ • Anomaly Detect │                │
│  │ • Health      │   │ • Audit Logger   │   │ • Risk Scorer    │                │
│  └──────┬───────┘   └────────┬─────────┘   └────────┬─────────┘                │
│         │ WebSocket          │ HTTP/SSE              │ Internal gRPC/HTTP       │
│         │                    │                       │                           │
│  ┌──────┴───────────────────┴───────────────────────┴─────────┐                │
│  │                    SHARED INFRASTRUCTURE                     │                │
│  │  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌─────────┐ │                │
│  │  │  SQLite   │  │ Prometheus │  │  Grafana    │  │ cAdvisor│ │                │
│  │  │ (Audit DB)│  │ (Metrics)  │  │ (Dashboards)│  │(Docker) │ │                │
│  │  └──────────┘  └────────────┘  └────────────┘  └─────────┘ │                │
│  └─────────────────────────────────────────────────────────────┘                │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                          HTTP REST + SSE
                                     │
                    ┌────────────────┴────────────────┐
                    │   BUP FUEL SUPPLY SIMULATOR      │
                    │   (Docker: port 8000)             │
                    │                                   │
                    │  /v1/* — Public API (fault-prone) │
                    │  /admin/* — Admin API (reliable)  │
                    │  /v1/stream — SSE notifications   │
                    └───────────────────────────────────┘
```

### 2.2 Component Responsibilities

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| **Frontend** | Operator dashboard, alerts, decision approval UI, health status | React, Vite, TypeScript |
| **Backend API** | Simulator integration, state management, decision orchestration, audit logging | Python, FastAPI |
| **Intelligence Service** | Demand forecasting, optimization (LP), anomaly detection, risk scoring | Python, FastAPI, SciPy (HiGHS) |
| **SQLite** | Decision history, audit log, configuration state | SQLite3 |
| **Prometheus** | Time-series metrics collection | Prometheus |
| **Grafana** | Operational dashboards, alerting | Grafana |
| **cAdvisor** | Docker container resource monitoring | cAdvisor |

---

## 3. The Simulated World

### 3.1 Network Topology

```
                    DHAKA DIVISION (demand_factor: 1.00)
                    ══════════════════════════════════════

  ┌─────────────────┐           2 ticks, max 7,000L        ┌─────────────────────┐
  │  DEPOT-GAZIPUR   │─────────────────────────────────────►│  STATION-MIRPUR      │
  │                  │           2 ticks, max 6,500L        │  (urban_high)        │
  │  Dispatch: 12k/t │─────────────────────────────────────►│  Cap: 15k/14k/9k     │
  │  Cap: 90k/70k/45k│                                      │  Init: 9k/9k/5k      │
  │  Init: 60k/45k/26k│          ┌─────────────────────┐   └─────────────────────┘
  └──────────┬───────┘           │  STATION-TONGI       │
             │ 2t, max 6,500L    │  (industrial)        │
             └──────────────────►│  Cap: 18k/9k/6k      │  ← ONLY route to Tongi
                                 │  Init: 11k/6k/3.5k   │
                                 └─────────────────────┘

           4 ticks                                          4 ticks
           max 5,000L                                       max 5,000L
    ┌──────────┐  CROSS-REGION BACKUP ROUTES  ┌──────────────┐
    │depot-gaz │─────────────────────────────►│stn-karnaphuli│
    └──────────┘                              └──────────────┘
    ┌──────────┐                              ┌──────────────┐
    │depot-pat │─────────────────────────────►│ stn-mirpur   │
    └──────────┘                              └──────────────┘


                CHATTOGRAM DIVISION (demand_factor: 1.08)
                ══════════════════════════════════════════

  ┌─────────────────┐           2 ticks, max 7,000L        ┌─────────────────────┐
  │  DEPOT-PATIYA    │─────────────────────────────────────►│  STATION-KARNAPHULI  │
  │                  │           3 ticks, max 6,000L        │  (highway)           │
  │  Dispatch: 11k/t │─────────────────────────────────────►│  Cap: 14k/15k/9k     │
  │  Cap: 85k/65k/40k│                                      │  Init: 8.5k/9.5k/5.2k│
  │  Init: 55k/42k/24k│         ┌─────────────────────┐   └─────────────────────┘
  └──────────────────┘           │  STATION-COXSBAZAR   │
                                 │  (regional)          │
                  3t, max 6,000L │  Cap: 12k/12k/7k     │  ← ONLY route to Cox's Bazar
                 ───────────────►│  Init: 7.5k/7.5k/4.2k│
                                 └─────────────────────┘
```

### 3.2 Critical Numbers

| Metric | Value | Implication |
|--------|-------|-------------|
| Total daily demand | ~93,000 L/day | Must dispatch aggressively |
| Supply deliveries | 22 total (216,000 L) | Finite fuel — runs out day 2.2 |
| Max network lifetime | ~6 days (perfect play) | Every liter counts |
| Service level (no action) | 88% → 15% over 6 days | Catastrophic without intervention |
| Tick duration | 15 sim minutes | 96 ticks per sim day |
| Default speed | 8 ticks/sec | 1 sim day = 12 real seconds |

### 3.3 Demand Profiles (Liters/Day)

| Profile | Diesel | Petrol | Octane | Noise | Station |
|---------|--------|--------|--------|-------|---------|
| urban_high | 8,500 | 10,500 | 5,600 | ±10% | Mirpur |
| industrial | 14,000 | 4,500 | 2,200 | ±8% | Tongi |
| highway | 10,500 | 11,000 | 6,200 | ±12% | Karnaphuli |
| regional | 7,200 | 7,600 | 3,600 | ±10% | Cox's Bazar |

### 3.4 Hour-of-Day Demand Factors

| Profile | Busy Hours (Factor) | Off-Peak Hours (Factor) |
|---------|-------------------|----------------------|
| industrial | 06:00–17:59 → **1.55** | 18:00–05:59 → **0.45** |
| highway | 06–09 or 16–20 → **1.35** | else → **0.75** |
| urban_high | 07–09 or 16–20 → **1.45** | else → **0.70** |
| regional | 07:00–20:59 → **1.25** | 21:00–06:59 → **0.65** |

---

## 4. Data Flow Architecture

### 4.1 Observe → Act Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ OBSERVE  │───►│  DETECT  │───►│ PREDICT  │───►│  DECIDE  │───►│   ACT    │
│          │    │          │    │          │    │          │    │          │
│ Poll /v1 │    │ Anomaly  │    │ Forecast │    │ LP Optim │    │ POST     │
│ Listen   │    │ Detection│    │ Risk Calc│    │ or Rule  │    │ /v1/alloc│
│ SSE      │    │ Event    │    │ Stockout │    │ Human    │    │ Cancel   │
│ Validate │    │ Monitor  │    │ Probabil.│    │ Approval │    │ Retry    │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
      │                                                              │
      │              ┌──────────┐    ┌──────────┐                    │
      └──────────────│ MONITOR  │◄───│ RECOVER  │◄───────────────────┘
                     │          │    │          │
                     │ Metrics  │    │ Fallback │
                     │ Health   │    │ SafeHold │
                     │ Logs     │    │ Degraded │
                     └──────────┘    └──────────┘
```

### 4.2 Data Synchronization Strategy

| Source | Method | Frequency | Purpose |
|--------|--------|-----------|---------|
| `/v1/instance` | REST Poll | Every tick | Tick counter, sim_time, status |
| `/v1/depots` | REST Poll | Every tick | Inventory levels, dispatch capacity |
| `/v1/stations` | REST Poll | Every tick | Inventory, demand_multiplier, status |
| `/v1/routes` | REST Poll | Every tick | Route availability, transit times |
| `/v1/supply-arrivals` | REST Poll | Every 10 ticks | Upcoming supply schedule |
| `/v1/demand-history` | REST Poll | Every tick | Demand time-series for forecasting |
| `/v1/events` | REST Poll | Every tick | Crisis events (active/scheduled) |
| `/v1/allocations` | REST Poll | Every tick | Shipment status tracking |
| `/v1/metrics` | REST Poll | Every 5 ticks | Service level, failure count |
| `/v1/stream` | SSE | Continuous | Real-time notifications (advisory only) |
| `/v1/health` | REST Poll | Every 5s wall-clock | Liveness check (bypasses faults) |

> **RULE:** SSE is advisory only. Always re-GET the affected resource after receiving an SSE event.

---

## 5. Service Architecture

### 5.1 Backend API Service

```
backend-api/
├── app/
│   ├── main.py                 # FastAPI application entry
│   ├── config.py               # Environment configuration
│   ├── models/
│   │   ├── domain.py           # Depot, Station, Route, Allocation models
│   │   ├── events.py           # Crisis event models
│   │   └── decisions.py        # Decision request/response models
│   ├── adapters/
│   │   ├── simulator_client.py # HTTP client for /v1/* endpoints
│   │   ├── sse_listener.py     # SSE stream consumer
│   │   └── admin_client.py     # HTTP client for /admin/* endpoints
│   ├── services/
│   │   ├── state_manager.py    # Canonical world state (single source of truth)
│   │   ├── decision_hub.py     # Orchestrates observe→decide→act cycle
│   │   ├── shipment_guard.py   # Pre-flight validation + trap avoidance
│   │   ├── allocation_executor.py # Idempotent POST with retry logic
│   │   └── audit_logger.py     # Decision history + audit trail
│   ├── resilience/
│   │   ├── circuit_breaker.py  # Circuit breaker for simulator calls
│   │   ├── health_monitor.py   # System health aggregation
│   │   ├── mode_manager.py     # NORMAL → DEGRADED → SAFE_HOLD → FALLBACK
│   │   └── recovery.py         # Auto-recovery orchestration
│   ├── api/
│   │   ├── dashboard.py        # REST endpoints for frontend
│   │   ├── decisions.py        # Approval/rejection endpoints
│   │   ├── health.py           # Health check endpoint
│   │   └── websocket.py        # WebSocket push to frontend
│   └── db/
│       ├── database.py         # SQLite connection manager
│       └── migrations.py       # Schema setup
├── Dockerfile
├── requirements.txt
└── tests/
```

### 5.2 Intelligence Service

```
intelligence-service/
├── app/
│   ├── main.py                 # FastAPI application entry
│   ├── forecasting/
│   │   ├── demand_forecaster.py    # Time-series demand prediction
│   │   ├── seasonal_model.py       # Hour-of-day pattern learning
│   │   └── forecast_evaluator.py   # WAPE calculation, accuracy tracking
│   ├── optimization/
│   │   ├── lp_optimizer.py         # SciPy/HiGHS linear program
│   │   ├── constraints.py          # Simulator rule constraints
│   │   ├── objective.py            # Multi-objective: minimize unmet + waste
│   │   └── fallback_rule.py        # Simple greedy rule (backup)
│   ├── detection/
│   │   ├── anomaly_detector.py     # Demand spike detection
│   │   ├── supply_monitor.py       # Supply arrival tracking
│   │   └── inventory_analyzer.py   # Depot/station level monitoring
│   ├── risk/
│   │   ├── risk_scorer.py          # Per-station, per-fuel risk score
│   │   ├── stockout_predictor.py   # Hours-until-empty calculation
│   │   └── confidence.py           # Forecast confidence intervals
│   └── api/
│       ├── forecast.py             # Forecast API endpoints
│       ├── optimize.py             # Optimization API endpoints
│       └── health.py               # Service health
├── Dockerfile
├── requirements.txt
└── tests/
```

### 5.3 Frontend Application

```
frontend/
├── src/
│   ├── App.tsx
│   ├── main.tsx
│   ├── pages/
│   │   ├── Dashboard.tsx           # Main operator dashboard
│   │   ├── StationDetail.tsx       # Per-station deep dive
│   │   ├── Decisions.tsx           # Pending approvals + history
│   │   ├── Alerts.tsx              # Active alerts and incidents
│   │   └── SystemHealth.tsx        # Service health + metrics
│   ├── components/
│   │   ├── NetworkMap.tsx          # Visual network topology
│   │   ├── InventoryGauge.tsx      # Fuel level gauges
│   │   ├── RiskBadge.tsx           # OK/Watch/High/Critical badges
│   │   ├── AllocationCard.tsx      # Shipment recommendation card
│   │   ├── DemandChart.tsx         # Demand forecast chart
│   │   ├── TimelineView.tsx        # Event/crisis timeline
│   │   └── ServiceStatus.tsx       # Backend/simulator health
│   ├── hooks/
│   │   ├── useWebSocket.ts         # WebSocket connection
│   │   └── usePolling.ts           # REST polling fallback
│   ├── services/
│   │   └── api.ts                  # Backend API client
│   └── types/
│       └── index.ts                # TypeScript interfaces
├── Dockerfile
├── package.json
├── vite.config.ts
└── tsconfig.json
```

---

## 6. Deployment Architecture

### 6.1 Docker Compose Stack

```yaml
# docker-compose.yml
services:
  # ─── EXTERNAL (provided by organizers) ───
  simulator-api:
    image: asifmahmoud414/bup-fuel-supply-simulator:1.0.0
    environment:
      SIMULATION_SPEED: ${SIMULATION_SPEED:-8}
      TICK_MINUTES: ${TICK_MINUTES:-15}
      SIMULATOR_START_MODE: ${SIMULATOR_START_MODE:-paused}
    ports:
      - "8000:8000"

  # ─── OUR SYSTEM ───
  backend-api:
    build: ./backend-api
    ports:
      - "8080:8080"
    environment:
      SIMULATOR_URL: http://simulator-api:8000
      DATABASE_URL: sqlite:///data/jalani.db
      INTELLIGENCE_URL: http://intelligence-service:8081
    volumes:
      - backend-data:/data
    depends_on:
      - simulator-api
      - intelligence-service
    restart: unless-stopped

  intelligence-service:
    build: ./intelligence-service
    ports:
      - "8081:8081"
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend-api

  # ─── OBSERVABILITY STACK ───
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"
    volumes:
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning

  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    ports:
      - "8082:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

volumes:
  backend-data:
```

### 6.2 Port Map

| Service | Port | Purpose |
|---------|------|---------|
| Simulator | 8000 | BUP Fuel Supply Simulator |
| Backend API | 8080 | Jalani backend REST + WebSocket |
| Intelligence | 8081 | Forecast + optimization service |
| Frontend | 3000 | Operator dashboard UI |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3001 | Monitoring dashboards |
| cAdvisor | 8082 | Container metrics |

---

## 7. Technology Stack Summary

| Layer | Technology | Justification |
|-------|-----------|---------------|
| **Frontend** | React + Vite + TypeScript | Fast dev cycle, type safety, modern SPA |
| **Backend** | Python + FastAPI | Async HTTP, OpenAPI docs, Python ecosystem |
| **Intelligence** | SciPy (HiGHS LP) | Production-grade LP solver, no extra deps |
| **Database** | SQLite | Zero-config, sufficient for single-tenant |
| **AI Explanations** | Azure OpenAI (GPT) + template fallback | Human-readable decision explanations |
| **Monitoring** | Prometheus + Grafana + cAdvisor | Industry standard, pre-built dashboards |
| **Load Testing** | k6 | Scriptable, CLI-first, good reporting |
| **Deployment** | Docker Compose | Simple, reproducible, fits single-server |
| **CI/CD** | GitHub Actions | Automated build, test, deploy pipeline |

---

## 8. Security Considerations

- **No hard-coded secrets** — all credentials via environment variables
- **Input validation** — all simulator data validated before use
- **No real infrastructure** — clearly marked as SIMULATION ONLY
- **Operator authentication** — basic auth for approval endpoints
- **Audit trail** — every decision logged with who, what, when, why

---

## 9. Cross-References

| Document | Purpose |
|----------|---------|
| [02_HIDDEN_TRAPS_ANALYSIS.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/02_HIDDEN_TRAPS_ANALYSIS.md) | Deep analysis of simulator edge cases |
| [03_TECHNICAL_DESIGN.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/03_TECHNICAL_DESIGN.md) | Detailed component design |
| [04_API_INTEGRATION.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/04_API_INTEGRATION.md) | Simulator API integration strategy |
| [05_RESILIENCE_STRATEGY.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/05_RESILIENCE_STRATEGY.md) | Fault handling and recovery |
| [06_OBSERVABILITY.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/06_OBSERVABILITY.md) | Monitoring, metrics, health |
| [07_DEPLOYMENT.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/07_DEPLOYMENT.md) | Build, deploy, CI/CD |
| [08_DATA_MODEL.md](file:///d:/BUP%20hackthon%20final%20project%20build/docs/08_DATA_MODEL.md) | Database schema and data flow |

# Observability & Monitoring

> **Project:** Jalani Control Tower  
> **Document:** Metrics, Logging, Health Checks & Dashboards  
> **Version:** 1.0.0  

---

## 1. Observability Stack

```
┌─────────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Backend API        │────►│  Prometheus   │────►│   Grafana    │
│  Intelligence Svc   │     │  (scrape)     │     │  (visualize) │
│  Frontend           │     │  :9090        │     │  :3001       │
└─────────────────────┘     └──────────────┘     └──────────────┘
         │                                              │
         │  structured logs                    dashboards + alerts
         ▼                                              ▼
┌─────────────────────┐                     ┌──────────────────┐
│  stdout / file      │                     │  Operator Browser │
│  (JSON format)      │                     └──────────────────┘
└─────────────────────┘
         │
         ▼
┌─────────────────────┐
│  Docker log driver  │  ← `docker compose logs -f`
└─────────────────────┘
```

---

## 2. Metrics Catalog

### 2.1 Application Metrics (Backend API)

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `jalani_sim_poll_duration_seconds` | Histogram | `endpoint` | Time to poll each simulator endpoint |
| `jalani_sim_poll_errors_total` | Counter | `endpoint`, `error_type` | Failed poll attempts |
| `jalani_sim_poll_stale_total` | Counter | `endpoint` | Stale data responses detected |
| `jalani_allocations_total` | Counter | `status`, `approval_mode` | Allocations created (proposed/approved/rejected/executed/failed) |
| `jalani_allocation_latency_seconds` | Histogram | — | Time from decision to POST response |
| `jalani_decisions_pending` | Gauge | — | Decisions awaiting human approval |
| `jalani_operating_mode` | Gauge | `mode` | Current operating mode (1=active, 0=inactive) |
| `jalani_mode_transitions_total` | Counter | `from`, `to` | Mode transition count |
| `jalani_circuit_breaker_state` | Gauge | `service` | 0=closed, 1=open, 2=half-open |
| `jalani_tick_pipeline_duration_seconds` | Histogram | — | Full observe→act pipeline time |
| `jalani_tick_current` | Gauge | — | Current simulator tick |
| `jalani_service_level_pct` | Gauge | — | Simulator-reported service level |

### 2.2 Intelligence Metrics

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `jalani_forecast_wape` | Gauge | `station`, `fuel_type` | Weighted Absolute Percentage Error |
| `jalani_forecast_latency_seconds` | Histogram | `station` | Time to generate forecast |
| `jalani_optimizer_duration_seconds` | Histogram | `solver` | LP solver execution time |
| `jalani_optimizer_status` | Counter | `status` | LP solve outcomes (optimal/infeasible/fallback) |
| `jalani_risk_score` | Gauge | `station`, `fuel_type` | Current risk score per station/fuel |
| `jalani_anomalies_detected_total` | Counter | `station`, `type` | Anomaly detection triggers |
| `jalani_stockout_hours_remaining` | Gauge | `station`, `fuel_type` | Predicted hours until empty |

### 2.3 Infrastructure Metrics (via cAdvisor)

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Container CPU % | cAdvisor | > 80% sustained |
| Container Memory MB | cAdvisor | > 512MB |
| Container Restart Count | cAdvisor | > 2 in 5 min |
| Network I/O | cAdvisor | Anomalous spike |

---

## 3. Prometheus Configuration

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: 'backend-api'
    static_configs:
      - targets: ['backend-api:8080']
    metrics_path: '/metrics'

  - job_name: 'intelligence-service'
    static_configs:
      - targets: ['intelligence-service:8081']
    metrics_path: '/metrics'

  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: 'simulator'
    static_configs:
      - targets: ['simulator-api:8000']
    metrics_path: '/v1/metrics'
    scrape_interval: 10s

rule_files:
  - 'alerts.yml'
```

### 3.1 Alert Rules

```yaml
# monitoring/alerts.yml
groups:
  - name: jalani_alerts
    rules:
      - alert: HighRiskStation
        expr: jalani_risk_score > 0.7
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Station {{ $labels.station }} fuel {{ $labels.fuel_type }} at critical risk"

      - alert: ServiceLevelDrop
        expr: jalani_service_level_pct < 85
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Service level dropped to {{ $value }}%"

      - alert: SimulatorUnreachable
        expr: up{job="simulator"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "BUP Simulator unreachable"

      - alert: CircuitBreakerOpen
        expr: jalani_circuit_breaker_state > 0
        for: 10s
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker open for {{ $labels.service }}"

      - alert: ForecastDegraded
        expr: jalani_forecast_wape > 0.3
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Forecast accuracy degraded for {{ $labels.station }}"
```

---

## 4. Grafana Dashboards

### 4.1 Operations Dashboard

```
┌──────────────────────────────────────────────────────────────┐
│  JALANI CONTROL TOWER — Operations Dashboard                 │
├──────────────────────┬───────────────────────────────────────┤
│  Service Level       │  Station Risk Heatmap                 │
│  ┌────────────────┐  │  ┌─────────────────────────────────┐ │
│  │  ██████ 94.2%  │  │  │ Mirpur    [██████░░] 65%        │ │
│  │  Target: 95%   │  │  │ Tongi     [████████] 85%        │ │
│  └────────────────┘  │  │ Karnaphuli[███░░░░░] 32%  ⚠️   │ │
│                      │  │ Cox'sBazar[█████░░░] 55%        │ │
│  Mode: NORMAL ●      │  └─────────────────────────────────┘ │
├──────────────────────┼───────────────────────────────────────┤
│  Depot Inventory     │  Demand vs Forecast                   │
│  ┌────────────────┐  │  ┌─────────────────────────────────┐ │
│  │ Gazipur        │  │  │     ▄▄  ▄▄                      │ │
│  │ D: 45k/90k     │  │  │   ▄██▄▄██▄▄   actual ——        │ │
│  │ P: 32k/70k     │  │  │  ▄████████▄▄  forecast --      │ │
│  │ O: 18k/45k     │  │  │ ▄██████████▄                    │ │
│  ├────────────────┤  │  └─────────────────────────────────┘ │
│  │ Patiya         │  │                                      │
│  │ D: 38k/85k     │  │  Allocation Activity                 │
│  │ P: 28k/65k     │  │  ┌─────────────────────────────────┐ │
│  │ O: 15k/40k     │  │  │ Today: 47 dispatches            │ │
│  └────────────────┘  │  │ In Transit: 3                    │ │
│                      │  │ Failed: 1                         │ │
│                      │  │ Waste: 0 L ✓                     │ │
│                      │  └─────────────────────────────────┘ │
├──────────────────────┴───────────────────────────────────────┤
│  Active Events Timeline                                      │
│  ──●─────────────●────────────────────●──────────────────── │
│    Road Closure   Demand Spike        Supply Arrival         │
│    R-CHATT-02     Mirpur 1.5x         Gazipur +12k          │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 System Health Dashboard

```
┌──────────────────────────────────────────────────────────────┐
│  SYSTEM HEALTH                                               │
├──────────────────────┬───────────────────────────────────────┤
│  Component Status    │  Latency Percentiles                  │
│  ┌────────────────┐  │  ┌─────────────────────────────────┐ │
│  │ Backend API ● ▐│  │  │ p50: 23ms                       │ │
│  │ Intelligence ● ▐│  │  │ p95: 164ms                      │ │
│  │ Simulator   ● ▐│  │  │ p99: 892ms                      │ │
│  │ Database    ● ▐│  │  └─────────────────────────────────┘ │
│  │ Prometheus  ● ▐│  │                                      │
│  └────────────────┘  │  Error Rate                           │
│                      │  ┌─────────────────────────────────┐ │
│  ● Healthy           │  │ Current: 0.4%                    │ │
│  ● Degraded          │  │ Target: < 1%  ✓                  │ │
│  ● Unhealthy         │  └─────────────────────────────────┘ │
├──────────────────────┴───────────────────────────────────────┤
│  Circuit Breakers                                            │
│  Simulator:     CLOSED ●   Intelligence: CLOSED ●           │
│  LLM:          CLOSED ●   Database:     CLOSED ●           │
├──────────────────────────────────────────────────────────────┤
│  Container Resources                                         │
│  backend-api:     CPU 12%  Mem 128MB  Restarts: 0           │
│  intelligence:    CPU 8%   Mem 96MB   Restarts: 0           │
│  frontend:        CPU 2%   Mem 64MB   Restarts: 0           │
│  simulator:       CPU 15%  Mem 256MB  Restarts: 0           │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. Structured Logging

### 5.1 Log Format

```python
import structlog

logger = structlog.get_logger()

# Every log line is JSON with standard fields
logger.info("allocation_created",
    decision_id="dec-1234",
    tick=145,
    depot="depot-gazipur",
    station="station-mirpur",
    fuel_type="diesel",
    amount=5000,
    risk_score=0.72,
    approval_mode="auto",
    latency_ms=23
)
```

**Output:**
```json
{
  "timestamp": "2026-09-30T12:15:00Z",
  "level": "info",
  "event": "allocation_created",
  "decision_id": "dec-1234",
  "tick": 145,
  "depot": "depot-gazipur",
  "station": "station-mirpur",
  "fuel_type": "diesel",
  "amount": 5000,
  "risk_score": 0.72,
  "approval_mode": "auto",
  "latency_ms": 23,
  "service": "backend-api"
}
```

### 5.2 Log Categories

| Category | Level | Examples |
|----------|-------|---------|
| **Operations** | INFO | Allocation created/delivered, decisions approved |
| **Resilience** | WARNING | Mode transitions, circuit breaker state changes, retries |
| **Errors** | ERROR | Simulator errors, validation failures, LP infeasible |
| **Audit** | INFO | Human approvals/rejections, configuration changes |
| **Performance** | DEBUG | Poll timings, optimizer duration, forecast accuracy |

---

## 6. Health Check Endpoints

### 6.1 Backend Health (`GET /api/health`)

```json
{
  "status": "healthy",
  "mode": "normal",
  "uptime_seconds": 3600,
  "components": {
    "simulator": {
      "status": "healthy",
      "last_successful_poll_tick": 1234,
      "latency_ms": 23,
      "circuit_breaker": "closed"
    },
    "intelligence": {
      "status": "healthy",
      "last_forecast_tick": 1230,
      "forecast_wape": 0.12
    },
    "database": {
      "status": "healthy",
      "decision_count": 847
    }
  },
  "metrics": {
    "service_level_pct": 94.2,
    "current_tick": 1234,
    "decisions_today": 47,
    "p95_latency_ms": 164,
    "error_rate_pct": 0.4
  }
}
```

### 6.2 Health Check Integration

```python
@app.get("/api/health")
async def health_check():
    """Aggregated health check for all components."""
    sim_health = await check_simulator_health()
    intel_health = await check_intelligence_health()
    db_health = await check_database_health()
    
    components = {
        "simulator": sim_health,
        "intelligence": intel_health,
        "database": db_health
    }
    
    # Overall status: worst component determines overall
    statuses = [c["status"] for c in components.values()]
    if all(s == "healthy" for s in statuses):
        overall = "healthy"
    elif any(s == "unhealthy" for s in statuses):
        overall = "unhealthy"
    else:
        overall = "degraded"
    
    return {
        "status": overall,
        "mode": mode_manager.current_mode,
        "components": components,
        "metrics": await get_current_metrics()
    }
```

---

## 7. Load Testing Plan

### 7.1 k6 Test Script

```javascript
// load-test/test.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const decisionLatency = new Trend('decision_latency');

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Ramp up
    { duration: '2m',  target: 10 },   // Steady state
    { duration: '30s', target: 50 },   // Spike
    { duration: '1m',  target: 50 },   // Hold spike
    { duration: '30s', target: 10 },   // Recover
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    'http_req_duration': ['p(95)<500', 'p(99)<2000'],
    'errors': ['rate<0.05'],
  },
};

export default function () {
  // Test: Dashboard state endpoint
  let res = http.get('http://localhost:8080/api/state');
  check(res, { 'state 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  // Test: Decision recommendations
  res = http.get('http://localhost:8080/api/decisions');
  check(res, { 'decisions 200': (r) => r.status === 200 });
  decisionLatency.add(res.timings.duration);

  // Test: Health check
  res = http.get('http://localhost:8080/api/health');
  check(res, { 'health 200': (r) => r.status === 200 });

  sleep(0.5);
}
```

### 7.2 Performance Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| p50 latency | < 100ms | Fast operator experience |
| p95 latency | < 500ms | Acceptable under load |
| p99 latency | < 2000ms | Includes fault-injected scenarios |
| Error rate | < 5% | Resilient under stress |
| Throughput | > 50 req/s | Handle multiple operators |
| Tick pipeline | < 8s | Complete before next sim tick |

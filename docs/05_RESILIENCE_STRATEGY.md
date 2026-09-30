# Resilience Strategy

> **Project:** Jalani Control Tower  
> **Document:** Fault Handling, Degraded Modes & Recovery  
> **Version:** 1.0.0  

---

## 1. Operating Modes

The system operates in one of four modes, transitioning automatically based on health signals:

```
┌──────────┐   failures > 3    ┌──────────┐   sim unreachable   ┌──────────┐
│  NORMAL  │─────────────────►│ DEGRADED │──────────────────►│ SAFE_HOLD │
│          │                  │          │                    │           │
│ Full AI  │◄─────────────────│ Cached + │◄───────────────── │ No new    │
│ LP Optim │   recovery OK    │ Heuristic│   sim reconnects  │ dispatches│
│ SSE+Poll │                  │ Poll only│                    │ Monitor   │
└──────────┘                  └──────────┘                    └───────┬───┘
                                                                     │
                                                              sim down > 5min
                                                                     │
                                                              ┌──────▼──────┐
                                                              │  FALLBACK   │
                                                              │             │
                                                              │ Static rules│
                                                              │ Alert human │
                                                              │ Cache state │
                                                              └─────────────┘
```

### 1.1 Mode Definitions

| Mode | Condition | Intelligence | Data Source | Dispatches | Alerts |
|------|-----------|-------------|------------|-----------|--------|
| **NORMAL** | All systems healthy | LP Optimizer + Forecaster | REST Poll + SSE | Automatic (low-risk) + Human (high-risk) | Standard |
| **DEGRADED** | Sim intermittent OR intelligence down | Fallback heuristic | REST Poll only (SSE dropped) | Conservative only, all human-reviewed | Elevated |
| **SAFE_HOLD** | Sim unreachable < 5 min | None (frozen state) | Last known state | **None** — all queued | Critical |
| **FALLBACK** | Sim unreachable ≥ 5 min | Static rules only | Extrapolated from cache | Emergency only, human-approved | Emergency |

### 1.2 Mode Transition Logic

```python
class ModeManager:
    """Manages operating mode transitions."""
    
    NORMAL = "normal"
    DEGRADED = "degraded"  
    SAFE_HOLD = "safe_hold"
    FALLBACK = "fallback"
    
    def __init__(self):
        self.current_mode = self.NORMAL
        self.consecutive_failures = 0
        self.last_successful_poll = time.time()
        self.intelligence_available = True
    
    def on_poll_success(self):
        self.consecutive_failures = 0
        self.last_successful_poll = time.time()
        if self.current_mode in (self.SAFE_HOLD, self.FALLBACK):
            self._transition(self.DEGRADED)  # Recover through DEGRADED first
        elif self.current_mode == self.DEGRADED and self.intelligence_available:
            self._transition(self.NORMAL)
    
    def on_poll_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3 and self.current_mode == self.NORMAL:
            self._transition(self.DEGRADED)
    
    def on_sim_unreachable(self):
        """Called when health check fails."""
        time_since_last = time.time() - self.last_successful_poll
        if time_since_last < 300:  # < 5 minutes
            self._transition(self.SAFE_HOLD)
        else:
            self._transition(self.FALLBACK)
    
    def on_intelligence_failure(self):
        self.intelligence_available = False
        if self.current_mode == self.NORMAL:
            self._transition(self.DEGRADED)
    
    def on_intelligence_recovery(self):
        self.intelligence_available = True
        if self.current_mode == self.DEGRADED and self.consecutive_failures == 0:
            self._transition(self.NORMAL)
    
    def _transition(self, new_mode):
        old_mode = self.current_mode
        self.current_mode = new_mode
        logger.warning(f"MODE TRANSITION: {old_mode} → {new_mode}")
        metrics.mode_transitions.inc()
        event_bus.publish(ModeChangeEvent(old_mode, new_mode))
```

---

## 2. Circuit Breaker Pattern

### 2.1 Implementation

```python
class CircuitBreaker:
    """
    Prevents cascading failures by short-circuiting calls to failing services.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is failing, requests are blocked
    - HALF_OPEN: Testing if service has recovered
    """
    
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    
    def __init__(self, failure_threshold=5, recovery_timeout=30, success_threshold=3):
        self.state = self.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.last_failure_time = None
    
    @property
    def is_open(self):
        if self.state == self.OPEN:
            # Check if recovery timeout has elapsed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = self.HALF_OPEN
                return False
            return True
        return False
    
    def record_success(self):
        if self.state == self.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = self.CLOSED
                self.failure_count = 0
                self.success_count = 0
        elif self.state == self.CLOSED:
            self.failure_count = 0
    
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            self.success_count = 0
```

### 2.2 Circuit Breaker Map

| Service | Failure Threshold | Recovery Timeout | Behavior When Open |
|---------|-------------------|------------------|-------------------|
| Simulator `/v1/*` | 5 failures | 30s | Use cached state |
| Intelligence Service | 3 failures | 15s | Use fallback heuristic |
| LLM (GPT) | 2 failures | 60s | Use template explanations |

---

## 3. Failure Response Matrix

| Failure | Detection | Immediate Response | Recovery |
|---------|-----------|-------------------|----------|
| **Sim API timeout** | `httpx.TimeoutException` | Retry 3x with backoff | Circuit breaker if persistent |
| **Sim API 503** | Status code 503 | Retry 3x → use cached state | Health check poll until recovered |
| **Sim API stale data** | Tick mismatch | Re-read, use latest | Log, no mode change |
| **SSE disconnect** | Connection lost | Switch to poll-only | Auto-reconnect with backoff |
| **Intelligence service down** | Health check fail | Switch to fallback heuristic | Mode → DEGRADED → NORMAL on recovery |
| **LP solver timeout** | >2s runtime | Use greedy fallback | Log, retry next tick with smaller horizon |
| **LLM unavailable** | API error | Use template explanations | Retry silently next request |
| **Database write fail** | SQLite error | Log to file, in-memory cache | Retry on next write |
| **Frontend disconnect** | WebSocket close | Frontend auto-reconnects | Full state sync on reconnect |
| **Docker container crash** | Health check / restart count | `restart: unless-stopped` | Auto-restart by Docker |

---

## 4. Resilience Patterns Applied

### 4.1 Retry with Exponential Backoff

```python
# All /v1/* endpoints use this pattern
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError))
)
async def safe_get(endpoint):
    return await client.get(endpoint)
```

### 4.2 Timeout Budget

| Operation | Timeout | Rationale |
|-----------|---------|-----------|
| Simulator GET | 5s | Normal response is <100ms; 5s handles latency faults |
| Simulator POST | 5s | Same as GET |
| Health check | 2s | Fast liveness probe |
| Intelligence API | 3s | LP solver + forecast |
| LLM call | 10s | GPT responses are slower |
| Full tick pipeline | 8s | Must complete before next tick |

### 4.3 Cached State Fallback

```python
class StateManager:
    def __init__(self):
        self.current_state = WorldState()
        self.cached_state = WorldState()  # Last known good state
        self.cache_age_ticks = 0
    
    async def update(self, new_data, tick):
        self.cached_state = copy.deepcopy(self.current_state)
        self.current_state = self._merge(new_data, tick)
        self.cache_age_ticks = 0
    
    def get_best_state(self):
        """Return current state, or cached state if current is stale."""
        if self.current_state.is_valid():
            return self.current_state
        else:
            self.cache_age_ticks += 1
            return self.cached_state
```

### 4.4 Graceful Degradation Cascade

```
Level 0: FULL CAPABILITY
├── LP Optimizer → optimal allocations
├── Demand Forecaster → accurate predictions
├── LLM Explainer → natural language
├── SSE + REST → real-time data
└── Auto-approve low-risk decisions

Level 1: INTELLIGENCE DEGRADED
├── Fallback Heuristic → good-enough allocations
├── Simple moving average → basic predictions
├── Template Explainer → formulaic text
├── REST only → tick-aligned data
└── All decisions human-reviewed

Level 2: DATA DEGRADED  
├── Fallback Heuristic → conservative allocations
├── No predictions → use last known demand rate
├── No explanations → raw numbers only
├── Cached state → stale but safe
└── All decisions human-reviewed

Level 3: SAFE HOLD
├── No dispatches → queue everything
├── No predictions → alert human
├── Monitor health only → wait for recovery
└── Display last known state to operator
```

---

## 5. Recovery Procedures

### 5.1 Automatic Recovery

```python
class RecoveryOrchestrator:
    """Coordinates recovery after failures."""
    
    async def on_mode_change(self, old_mode, new_mode):
        if new_mode == "normal" and old_mode == "degraded":
            await self._recover_from_degraded()
        elif new_mode == "degraded" and old_mode in ("safe_hold", "fallback"):
            await self._recover_from_outage()
    
    async def _recover_from_degraded(self):
        """Full recovery: re-sync all state, re-run forecasts."""
        logger.info("RECOVERY: Transitioning to NORMAL mode")
        
        # Step 1: Full state refresh
        await self.state_manager.full_refresh()
        
        # Step 2: Reconcile allocations
        actual = await self.sim_client.get("/v1/allocations")
        self.state_manager.reconcile_allocations(actual)
        
        # Step 3: Re-run forecasts
        await self.intelligence.reforecast_all()
        
        # Step 4: Process queued decisions
        queued = self.decision_queue.drain()
        for decision in queued:
            await self.decision_hub.process(decision)
        
        logger.info("RECOVERY: Normal mode restored")
    
    async def _recover_from_outage(self):
        """Partial recovery: verify state consistency."""
        logger.info("RECOVERY: Transitioning from outage to DEGRADED")
        
        # Full state refresh to detect what changed during outage
        new_state = await self.state_manager.full_refresh()
        old_state = self.state_manager.cached_state
        
        # Detect and log discrepancies
        diffs = self._diff_states(old_state, new_state)
        for diff in diffs:
            logger.warning(f"STATE DRIFT during outage: {diff}")
            self.audit_logger.log_drift(diff)
        
        # Alert operator about potential missed events
        if diffs:
            self.alert_manager.send(Alert(
                severity="WARNING",
                message=f"Recovered from outage. {len(diffs)} state changes detected during downtime.",
                details=diffs
            ))
```

### 5.2 Manual Recovery Actions

| Scenario | Operator Action | System Support |
|----------|----------------|----------------|
| Persistent sim failure | Restart simulator container | Health dashboard shows container status |
| Intelligence model broken | Acknowledge degraded mode | System continues with fallback |
| Data corruption detected | Trigger full state re-sync | `/api/admin/resync` endpoint |
| Missed supply arrival | Manual audit check | Compare supply schedule vs. depot history |

---

## 6. Testing Resilience

### 6.1 Fault Injection Tests

| Test Case | Fault to Inject | Expected Behavior | Pass Criteria |
|-----------|-----------------|-------------------|---------------|
| **Latency** | `POST /admin/faults/inject {type: "latency", ms: 3000}` | Retries succeed, decisions delayed | No data loss, mode stays NORMAL |
| **Unavailable** | `POST /admin/faults/inject {type: "unavailable"}` | Circuit opens → DEGRADED mode | Cached state used, operator alerted |
| **Stale Data** | `POST /admin/faults/inject {type: "stale_data"}` | Data validated, re-read triggered | Decisions based on fresh data |
| **Error Rate** | `POST /admin/faults/inject {type: "error_rate", rate: 0.5}` | 50% requests fail → retries handle | No missed ticks |
| **SSE Disconnect** | `POST /admin/faults/inject {type: "stream_disconnect"}` | Auto-reconnect, poll continues | No missed events |
| **Combined** | Inject latency + error_rate | Multiple retries, possible DEGRADED | System recovers when faults cleared |

### 6.2 Suggested Demonstration Story

```
Step  1: Normal operations — dashboard shows healthy state
Step  2: Operator observes real-time fuel levels, demand charts
Step  3: Demand increases → system detects risk
Step  4: Intelligence predicts shortage → allocation recommended
Step  5: Operator inspects recommendation → approves
Step  6: Allocation executed → service level maintained
Step  7: Road closure event fires → system re-routes
Step  8: Inject latency fault → system shows "DEGRADED" badge
Step  9: Clear fault → system self-heals to NORMAL
Step 10: Inject unavailable fault → SAFE_HOLD mode
Step 11: Clear fault → full recovery, state reconciliation
Step 12: Show audit log → every action traceable
```

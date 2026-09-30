# Technical Design Document

> **Project:** Jalani Control Tower  
> **Document:** Detailed Component Design & Algorithms  
> **Version:** 1.0.0  

---

## 1. Intelligence Engine Design

### 1.1 Demand Forecasting Algorithm

**Approach:** Weighted Moving Average with Seasonal Decomposition

The simulator uses deterministic demand patterns with noise. Our forecaster exploits this structure:

```
Demand(station, fuel, tick) = base_rate × hourly_factor(profile, hour) × demand_multiplier × (1 + noise)
```

#### Algorithm

```python
class DemandForecaster:
    """
    Forecasts demand per station per fuel type for the next N ticks.
    Uses exponentially weighted moving average (EWMA) combined with
    known hour-of-day seasonal patterns.
    """
    
    def __init__(self, alpha=0.3, history_window=96):  # 96 ticks = 1 day
        self.alpha = alpha
        self.history_window = history_window
        self.seasonal_factors = {}  # Learned from data
        
    def forecast(self, station_id, fuel_type, horizon_ticks=12):
        """
        Returns: List of (tick, predicted_demand) for next `horizon_ticks`
        
        Steps:
        1. Get last `history_window` demand observations
        2. Decompose into trend + seasonal + residual
        3. Project trend forward
        4. Apply seasonal factor for each future tick
        5. Return predictions with confidence intervals
        """
        history = self.get_demand_history(station_id, fuel_type)
        
        # Step 1: Compute EWMA trend
        trend = self._ewma(history)
        
        # Step 2: Extract seasonal pattern (hour-of-day)
        seasonal = self._seasonal_decompose(history, period=96)
        
        # Step 3: Forecast
        predictions = []
        for t in range(1, horizon_ticks + 1):
            future_tick = current_tick + t
            hour = tick_to_hour(future_tick)
            seasonal_factor = seasonal.get(hour, 1.0)
            
            predicted = trend * seasonal_factor
            confidence = self._confidence_interval(history, predicted)
            
            predictions.append(ForecastPoint(
                tick=future_tick,
                predicted_demand=predicted,
                confidence_low=confidence[0],
                confidence_high=confidence[1],
                confidence_score=confidence[2]
            ))
        
        return predictions
    
    def evaluate(self, actuals, predictions):
        """Calculate WAPE (Weighted Absolute Percentage Error)."""
        total_error = sum(abs(a - p) for a, p in zip(actuals, predictions))
        total_actual = sum(actuals)
        return total_error / total_actual if total_actual > 0 else float('inf')
```

#### Forecast Accuracy Target
- **WAPE < 15%** in steady state (no crisis events)
- **WAPE < 30%** during demand spike events (acceptable degradation)
- **Re-calibration**: Every 48 ticks (12 sim hours) or on demand multiplier change

### 1.2 LP Optimization Model

**Solver:** SciPy `linprog` with HiGHS backend

#### Decision Variables

```
x[d][s][f][r] = liters of fuel type f to dispatch
                from depot d to station s via route r
                in the current planning window
```

Where:
- `d ∈ {depot-gazipur, depot-patiya}`
- `s ∈ {station-mirpur, station-tongi, station-karnaphuli, station-coxsbazar}`
- `f ∈ {diesel, petrol, octane}`
- `r ∈ {valid routes connecting d→s}`

#### Objective Function (Minimize)

```
minimize:
    Σ (shortage_penalty[s][f] × unmet_demand[s][f])     # Prevent stockouts
  + Σ (waste_penalty × overflow[s][f])                   # Prevent waste
  + Σ (transit_cost[r] × x[d][s][f][r])                 # Prefer shorter routes
  + Σ (cross_region_penalty × x[d][s][f][r] for cross-region r)  # Prefer local
```

Where:
- `shortage_penalty = risk_score(station, fuel) × 100` (higher for critical stations)
- `waste_penalty = 50` (losing fuel is expensive)
- `transit_cost[r] = r.travel_ticks` (normalize by tick count)
- `cross_region_penalty = 20` (discourage but don't forbid)

#### Constraints

```python
constraints = [
    # C1: Can't dispatch more fuel than depot has
    "Σ x[d][*][f][*] ≤ depot[d].inventory[f]  ∀ d, f",
    
    # C2: Dispatch rate limit per depot per tick
    "Σ x[d][*][*][*] ≤ depot[d].dispatch_rate  ∀ d",
    
    # C3: Route capacity limit
    "Σ x[*][*][*][r] ≤ route[r].max_load  ∀ r",
    
    # C4: Route availability (hard constraint)
    "x[d][s][f][r] = 0  if route[r].status != 'available'",
    
    # C5: Station overflow prevention (Trap 2 defense)
    "delivery_amount[s][f] ≤ station[s].capacity[f] - projected_inventory_at_arrival[s][f]",
    
    # C6: Non-negativity
    "x[d][s][f][r] ≥ 0  ∀ d, s, f, r",
    
    # C7: Depot headroom for incoming supply (Trap 1 defense)
    "depot[d].inventory[f] - Σ x[d][*][f][*] ≤ depot[d].capacity[f] - expected_supply[d][f]"
    "  OR depot[d].inventory[f] - Σ x[d][*][f][*] ≥ 0"
]
```

#### Fallback Heuristic (when LP solver fails)

```python
def fallback_greedy_allocation(state):
    """
    Simple greedy: sort stations by urgency, dispatch to most urgent first.
    Used when LP solver is unavailable or times out.
    """
    allocations = []
    for station in sorted(state.stations, key=risk_score, reverse=True):
        for fuel_type in ['diesel', 'petrol', 'octane']:
            hours_left = station.inventory[fuel_type] / station.hourly_demand[fuel_type]
            if hours_left < 8:  # Less than 8 hours of fuel
                depot = find_nearest_available_depot(station, fuel_type)
                if depot:
                    amount = min(
                        depot.inventory[fuel_type] * 0.3,  # Max 30% of depot stock
                        station.capacity[fuel_type] - station.inventory[fuel_type],
                        depot.dispatch_rate - depot.dispatched_this_tick
                    )
                    if amount > 100:  # Minimum useful amount
                        allocations.append(Allocation(depot, station, fuel_type, amount))
    return allocations
```

---

## 2. State Management Design

### 2.1 World State Model

```python
@dataclass
class WorldState:
    """Single source of truth for the entire simulator state."""
    
    # Core state
    tick: int
    sim_time: datetime
    status: str  # 'running', 'paused', 'stopped'
    speed: int
    
    # Network entities
    depots: Dict[str, DepotState]
    stations: Dict[str, StationState]
    routes: Dict[str, RouteState]
    
    # Dynamic data
    active_allocations: List[AllocationState]
    supply_arrivals: List[SupplyArrival]
    active_events: List[CrisisEvent]
    
    # Derived data
    demand_history: Dict[str, Dict[str, List[DemandPoint]]]
    service_level: float
    
    # Metadata
    last_updated_tick: int
    data_freshness: Dict[str, int]  # endpoint → tick when last fetched
    
    def is_stale(self, endpoint: str, max_age_ticks: int = 2) -> bool:
        """Check if cached data for endpoint is stale (Trap 4 defense)."""
        last_tick = self.data_freshness.get(endpoint, 0)
        return (self.tick - last_tick) > max_age_ticks


@dataclass
class DepotState:
    id: str
    region: str
    inventory: Dict[str, float]  # fuel_type → liters
    capacity: Dict[str, float]
    dispatch_rate: float
    dispatched_this_tick: float  # Track dispatch rate usage
    
    def headroom(self, fuel_type: str) -> float:
        """Available capacity for incoming supply."""
        return self.capacity[fuel_type] - self.inventory[fuel_type]
    
    def remaining_dispatch_budget(self) -> float:
        """How many more liters can be dispatched this tick."""
        return self.dispatch_rate - self.dispatched_this_tick


@dataclass
class StationState:
    id: str
    region: str
    profile: str  # urban_high, industrial, highway, regional
    inventory: Dict[str, float]
    capacity: Dict[str, float]
    demand_multiplier: float
    
    def hours_until_empty(self, fuel_type: str, hourly_demand: float) -> float:
        """Estimated hours until stockout."""
        if hourly_demand <= 0:
            return float('inf')
        return self.inventory[fuel_type] / hourly_demand
    
    def projected_inventory_at(self, future_tick: int, current_tick: int, 
                                fuel_type: str, hourly_demand: float) -> float:
        """Predict inventory at a future tick (for Trap 2 defense)."""
        ticks_ahead = future_tick - current_tick
        hours_ahead = ticks_ahead * 15 / 60  # 15 minutes per tick
        consumption = hourly_demand * hours_ahead
        return max(0, self.inventory[fuel_type] - consumption)
```

### 2.2 State Update Pipeline

```
1. GET /v1/instance              → update tick, sim_time, status
2. GET /v1/depots                → update all depot states
3. GET /v1/stations              → update all station states  
4. GET /v1/routes                → update route availability
5. GET /v1/events                → update crisis events
6. GET /v1/allocations?status=in_transit → update active shipments
7. GET /v1/demand-history        → append to demand time-series
8. GET /v1/supply-arrivals       → update supply schedule
9. Validate freshness            → check tick correlation
10. Publish state update event   → trigger downstream consumers
```

---

## 3. Decision Pipeline Design

### 3.1 Decision States

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ PROPOSED │───►│ VALIDATED│───►│ APPROVED │───►│ EXECUTED │
│          │    │          │    │(auto/man)│    │          │
└──────────┘    └─────┬────┘    └──────────┘    └─────┬────┘
                      │                               │
                      ▼                               ▼
                ┌──────────┐                    ┌──────────┐
                │ REJECTED │                    │  FAILED  │
                │ (by guard)│                    │ (sim err)│
                └──────────┘                    └──────────┘
```

### 3.2 Decision Classification

| Decision Type | Risk Level | Approval Mode | Example |
|--------------|------------|---------------|---------|
| Routine Allocation | LOW | Auto-approve | Regular station top-up |
| Shortage Response | MEDIUM | Auto-approve | <6h fuel remaining |
| Emergency Allocation | HIGH | **Human Review** | <3h fuel, rationing needed |
| Cross-Region Transfer | MEDIUM | Auto-approve | Local depot depleted |
| Crisis Rationing | CRITICAL | **Human Review** | System-wide shortage |

### 3.3 Approval Criteria

```python
class DecisionClassifier:
    def classify(self, decision: AllocationDecision, state: WorldState) -> ApprovalMode:
        # Critical: requires human review
        if decision.risk_score > 0.8:
            return ApprovalMode.HUMAN_REVIEW
        
        # High-value: requires human review  
        if decision.total_liters > 10000:
            return ApprovalMode.HUMAN_REVIEW
        
        # Rationing decision: always human review
        if decision.type == DecisionType.RATIONING:
            return ApprovalMode.HUMAN_REVIEW
        
        # Cross-region during crisis: human review
        if decision.is_cross_region and state.has_active_crisis():
            return ApprovalMode.HUMAN_REVIEW
        
        # Everything else: auto-approve
        return ApprovalMode.AUTO_APPROVE
```

---

## 4. Risk Scoring Model

### 4.1 Per-Station Risk Score

```python
def calculate_risk_score(station: StationState, forecasts: Dict, events: List) -> float:
    """
    Returns risk score between 0.0 (safe) and 1.0 (critical).
    
    Components:
    - Inventory urgency: how many hours until empty
    - Demand trend: is demand increasing?
    - Supply availability: is the serving depot stocked?
    - Route status: are routes available?
    - Event impact: active events affecting this station
    """
    
    # Component 1: Inventory Urgency (0-0.4)
    min_hours = min(
        station.hours_until_empty(f, forecasts[f].hourly_demand)
        for f in ['diesel', 'petrol', 'octane']
    )
    if min_hours < 3:
        urgency = 0.40
    elif min_hours < 6:
        urgency = 0.30
    elif min_hours < 12:
        urgency = 0.15
    else:
        urgency = 0.0
    
    # Component 2: Demand Trend (0-0.2)
    demand_change = forecasts.get('demand_change_rate', 0)
    trend = min(0.20, max(0, demand_change * 0.1))
    
    # Component 3: Supply Availability (0-0.2)
    serving_depot = get_serving_depot(station)
    if serving_depot.inventory_pct < 0.1:
        supply_risk = 0.20
    elif serving_depot.inventory_pct < 0.3:
        supply_risk = 0.10
    else:
        supply_risk = 0.0
    
    # Component 4: Route Status (0-0.2)
    routes = get_routes_to_station(station)
    if all(r.status == 'unavailable' for r in routes):
        route_risk = 0.20  # Complete isolation (Trap 9)
    elif any(r.status == 'unavailable' for r in routes):
        route_risk = 0.10
    else:
        route_risk = 0.0
    
    return min(1.0, urgency + trend + supply_risk + route_risk)
```

### 4.2 Risk Thresholds

| Score | Level | Color | Action |
|-------|-------|-------|--------|
| 0.0 – 0.2 | OK | 🟢 Green | Normal operations |
| 0.2 – 0.4 | Watch | 🟡 Yellow | Increased monitoring |
| 0.4 – 0.7 | High | 🟠 Orange | Priority dispatch |
| 0.7 – 1.0 | Critical | 🔴 Red | Emergency response + human review |

---

## 5. Anomaly Detection Design

### 5.1 Demand Anomaly Detection

```python
class DemandAnomalyDetector:
    """Detects unusual demand patterns that require attention."""
    
    def __init__(self, z_threshold=2.5, min_history=24):
        self.z_threshold = z_threshold
        self.min_history = min_history
    
    def detect(self, station_id, fuel_type, current_demand, history):
        """
        Uses Z-score based detection.
        Anomaly if current demand deviates > z_threshold standard deviations
        from the historical mean for this hour-of-day.
        """
        if len(history) < self.min_history:
            return None  # Not enough data
        
        # Get demand for same hour-of-day from history
        same_hour_demands = [h.demand for h in history if h.hour == current_hour()]
        
        if len(same_hour_demands) < 3:
            return None
        
        mean = statistics.mean(same_hour_demands)
        stdev = statistics.stdev(same_hour_demands)
        
        if stdev == 0:
            return None
        
        z_score = (current_demand - mean) / stdev
        
        if abs(z_score) > self.z_threshold:
            return Anomaly(
                station_id=station_id,
                fuel_type=fuel_type,
                z_score=z_score,
                expected=mean,
                actual=current_demand,
                severity='high' if abs(z_score) > 4 else 'medium'
            )
        return None
```

---

## 6. Generative AI Integration

### 6.1 Decision Explanation Engine

```python
class DecisionExplainer:
    """
    Uses LLM to generate human-readable explanations for allocation decisions.
    Falls back to template-based explanations if LLM is unavailable.
    """
    
    def explain(self, decision: AllocationDecision, context: WorldState) -> str:
        try:
            return self._llm_explain(decision, context)
        except Exception:
            return self._template_explain(decision, context)
    
    def _llm_explain(self, decision, context):
        prompt = f"""
        Explain this fuel allocation decision to a human operator:
        
        Action: Dispatch {decision.amount}L of {decision.fuel_type} 
                from {decision.depot_id} to {decision.station_id}
                via {decision.route_id} (ETA: {decision.eta_ticks} ticks)
        
        Context:
        - Station current inventory: {context.station.inventory[decision.fuel_type]}L
        - Station capacity: {context.station.capacity[decision.fuel_type]}L
        - Hours until empty: {context.hours_left}
        - Risk score: {context.risk_score}
        - Active events: {context.active_events}
        
        Explain WHY this allocation was recommended, what risks it addresses,
        and what the expected impact will be. Be concise (2-3 sentences).
        """
        return llm_client.complete(prompt)
    
    def _template_explain(self, decision, context):
        """Fallback template-based explanation."""
        return (
            f"Dispatching {decision.amount:,.0f}L {decision.fuel_type} to "
            f"{decision.station_id} (Risk: {context.risk_level}). "
            f"Station has {context.hours_left:.1f}h of fuel remaining. "
            f"Expected stockout risk reduction: "
            f"{context.risk_before:.0%} → {context.risk_after:.0%}."
        )
```

### 6.2 Incident Summarization

```python
class IncidentSummarizer:
    """Generates natural-language summaries of crisis events and system responses."""
    
    def summarize_incident(self, event, actions_taken, outcome):
        prompt = f"""
        Summarize this operational incident for a fuel operations manager:
        
        Event: {event.type} affecting {event.target} 
               (started: tick {event.start_tick}, ended: tick {event.end_tick})
        
        Actions Taken: {json.dumps(actions_taken)}
        
        Outcome: Service level went from {outcome.sl_before}% to {outcome.sl_after}%
        
        Write a brief incident report (3-5 sentences) covering:
        1. What happened
        2. How the system responded
        3. The result
        """
        return llm_client.complete(prompt)
```

---

## 7. Database Schema

### 7.1 Audit Log Table

```sql
CREATE TABLE decision_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     TEXT UNIQUE NOT NULL,
    created_at      TEXT NOT NULL,
    tick            INTEGER NOT NULL,
    sim_time        TEXT NOT NULL,
    
    -- What
    type            TEXT NOT NULL,  -- 'allocation', 'cancellation', 'rationing'
    depot_id        TEXT,
    station_id      TEXT,
    fuel_type       TEXT,
    amount_liters   REAL,
    route_id        TEXT,
    
    -- Why
    risk_score      REAL,
    hours_until_empty REAL,
    trigger_reason  TEXT,  -- 'optimizer', 'fallback', 'emergency', 'human'
    explanation     TEXT,  -- Human-readable (LLM or template)
    
    -- Outcome
    status          TEXT NOT NULL,  -- 'proposed', 'approved', 'rejected', 'executed', 'failed'
    approval_mode   TEXT,  -- 'auto', 'human'
    approved_by     TEXT,
    
    -- Impact
    expected_impact TEXT,  -- JSON: {risk_before, risk_after, sl_impact}
    actual_impact   TEXT,  -- JSON: filled after delivery
    
    -- Metadata
    idempotency_key TEXT,
    simulator_response TEXT  -- JSON: raw simulator response
);

CREATE INDEX idx_decision_tick ON decision_log(tick);
CREATE INDEX idx_decision_station ON decision_log(station_id);
CREATE INDEX idx_decision_status ON decision_log(status);
```

### 7.2 Event History Table

```sql
CREATE TABLE event_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,  -- 'road_closure', 'demand_spike', 'depot_constraint'
    target          TEXT NOT NULL,
    start_tick      INTEGER NOT NULL,
    end_tick        INTEGER,
    severity        TEXT,
    description     TEXT,
    system_response TEXT,  -- JSON: actions taken
    incident_summary TEXT  -- LLM-generated summary
);
```

### 7.3 Forecast History Table

```sql
CREATE TABLE forecast_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id      TEXT NOT NULL,
    fuel_type       TEXT NOT NULL,
    forecast_tick   INTEGER NOT NULL,
    target_tick     INTEGER NOT NULL,
    predicted       REAL NOT NULL,
    actual          REAL,
    error           REAL,
    model_version   TEXT
);

CREATE INDEX idx_forecast_accuracy ON forecast_history(station_id, fuel_type, forecast_tick);
```

---

## 8. Frontend Component Specifications

### 8.1 Dashboard Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  JALANI CONTROL TOWER                    Tick: 1234  │ 12:30 PM   │
│  ═══════════════════                     Status: ● RUNNING        │
├────────────┬──────────────────────┬───────────────────────────────┤
│            │                      │                               │
│  NETWORK   │    STATION CARDS     │   DECISION FEED               │
│  MAP       │    ┌──────────────┐  │   ┌───────────────────────┐   │
│            │    │ MIRPUR  🟡   │  │   │ ⬆ 3,500L Diesel      │   │
│  [Dhaka]   │    │ D:67% P:45% │  │   │   Gazipur → Mirpur    │   │
│   │        │    │ O:72%       │  │   │   Risk: 72% → 31%     │   │
│   ├─MIRPUR │    └──────────────┘  │   │   [Approve] [Reject]  │   │
│   │        │    ┌──────────────┐  │   └───────────────────────┘   │
│   └─TONGI  │    │ TONGI   🟢   │  │   ┌───────────────────────┐   │
│            │    │ D:85% P:90% │  │   │ ✓ 5,000L Petrol       │   │
│  [Chatt]   │    │ O:88%       │  │   │   Patiya → Karnaphuli │   │
│   │        │    └──────────────┘  │   │   Auto-approved        │   │
│   ├─KARNA  │    ┌──────────────┐  │   └───────────────────────┘   │
│   │        │    │ KARNAPHULI🔴│  │                               │
│   └─COXS   │    │ D:15% P:22% │  │   ALERTS                     │
│            │    │ O:31%       │  │   ┌───────────────────────┐   │
│            │    └──────────────┘  │   │ 🔴 Road closure:      │   │
│            │    ┌──────────────┐  │   │    Route R-CHATT-01   │   │
│            │    │ COXSBAZAR 🟡│  │   │    Est. duration: 8t  │   │
│            │    │ D:55% P:48% │  │   └───────────────────────┘   │
│            │    │ O:62%       │  │   ┌───────────────────────┐   │
│            │    └──────────────┘  │   │ 🟡 Supply arriving:   │   │
│            │                      │   │    DEPOT-GAZIPUR +15t │   │
├────────────┴──────────────────────┴───────────────────────────────┤
│  SERVICE LEVEL: 94.2%  │  DECISIONS TODAY: 47  │  HEALTH: ● OK   │
└──────────────────────────────────────────────────────────────────┘
```

### 8.2 Key UI Components

| Component | Data Source | Update Frequency |
|-----------|-----------|-----------------|
| Network Map | `/api/state` | Every tick |
| Station Cards | `/api/stations` | Every tick |
| Inventory Gauges | `/api/stations` | Every tick |
| Risk Badges | `/api/risk-scores` | Every tick |
| Decision Feed | `/api/decisions` | WebSocket push |
| Alert Panel | `/api/alerts` | WebSocket push |
| Demand Chart | `/api/forecasts` | Every 10 ticks |
| Service Level | `/api/metrics` | Every 5 ticks |
| System Health | `/api/health` | Every 5s |

# Data Model & Entity Relationships

> **Project:** Jalani Control Tower  
> **Document:** Database Schema, Entity Relationships & Data Flow  
> **Version:** 1.0.0  

---

## 1. Entity Relationship Diagram

```
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│    REGION     │       │    DEPOT      │       │   STATION     │
│───────────────│       │───────────────│       │───────────────│
│ id            │◄──┐   │ id            │   ┌──►│ id            │
│ name          │   │   │ region_id  ───┼───┘   │ region_id  ───┼───┐
│ demand_factor │   │   │ capacity_d    │       │ profile       │   │
└───────────────┘   │   │ capacity_p    │       │ capacity_d    │   │
                    │   │ capacity_o    │       │ capacity_p    │   │
                    │   │ inventory_d   │       │ capacity_o    │   │
                    │   │ inventory_p   │       │ inventory_d   │   │
                    │   │ inventory_o   │       │ inventory_p   │   │
                    │   │ dispatch_rate │       │ inventory_o   │   │
                    │   └───────┬───────┘       │ demand_mult   │   │
                    │           │               └───────┬───────┘   │
                    │           │                       │           │
                    │     ┌─────┴───────────────────────┘           │
                    │     │                                         │
                    │     ▼                                         │
                    │   ┌───────────────┐                           │
                    │   │    ROUTE      │                           │
                    │   │───────────────│                           │
                    │   │ id            │                           │
                    │   │ from_depot ───┼── FK → DEPOT              │
                    │   │ to_station ───┼── FK → STATION            │
                    │   │ travel_ticks  │                           │
                    │   │ max_load      │                           │
                    │   │ status        │  {available, unavailable} │
                    │   │ is_cross_region│                          │
                    │   └───────┬───────┘                           │
                    │           │                                   │
                    │           ▼                                   │
                    │   ┌───────────────┐                           │
                    │   │  ALLOCATION   │                           │
                    │   │───────────────│                           │
                    │   │ id            │                           │
                    │   │ depot_id   ───┼── FK → DEPOT              │
                    │   │ station_id ───┼── FK → STATION            │
                    │   │ route_id   ───┼── FK → ROUTE              │
                    │   │ fuel_type     │  {diesel, petrol, octane} │
                    │   │ amount        │                           │
                    │   │ status        │  {pending, in_transit,    │
                    │   │               │   delivered, cancelled,   │
                    │   │               │   failed}                 │
                    │   │ created_tick  │                           │
                    │   │ depart_tick   │                           │
                    │   │ arrival_tick  │                           │
                    │   │ idemp_key     │                           │
                    │   └───────────────┘                           │
                    │                                               │
                    │   ┌───────────────┐                           │
                    └───┤  SUPPLY       │                           │
                        │  ARRIVAL      │                           │
                        │───────────────│                           │
                        │ id            │                           │
                        │ depot_id   ───┼── FK → DEPOT              │
                        │ arrival_tick  │                           │
                        │ diesel_amt    │                           │
                        │ petrol_amt    │                           │
                        │ octane_amt    │                           │
                        │ status        │  {scheduled, arrived}     │
                        └───────────────┘                           │
                                                                    │
                        ┌───────────────┐                           │
                        │  CRISIS EVENT │                           │
                        │───────────────│                           │
                        │ id            │                           │
                        │ type          │  {road_closure,           │
                        │               │   demand_spike,           │
                        │               │   depot_constraint,       │
                        │               │   supply_delay}           │
                        │ target_id  ───┼── FK → ROUTE|STATION|DEPOT│
                        │ start_tick    │                           │
                        │ end_tick      │                           │
                        │ severity      │                           │
                        │ parameters    │  JSON blob                │
                        └───────────────┘                           │
```

---

## 2. Simulator Data Structures

### 2.1 Depot Object (from `GET /v1/depots`)

```json
{
  "id": "depot-gazipur",
  "name": "Gazipur Fuel Depot",
  "region": "dhaka",
  "location": { "lat": 23.9, "lon": 90.4 },
  "inventory": {
    "diesel": 60000,
    "petrol": 45000,
    "octane": 26000
  },
  "capacity": {
    "diesel": 90000,
    "petrol": 70000,
    "octane": 45000
  },
  "dispatch_rate": 12000,
  "status": "active"
}
```

### 2.2 Station Object (from `GET /v1/stations`)

```json
{
  "id": "station-mirpur",
  "name": "Mirpur Fuel Station",
  "region": "dhaka",
  "profile": "urban_high",
  "location": { "lat": 23.8, "lon": 90.3 },
  "inventory": {
    "diesel": 9000,
    "petrol": 9000,
    "octane": 5000
  },
  "capacity": {
    "diesel": 15000,
    "petrol": 14000,
    "octane": 9000
  },
  "demand_multiplier": 1.0,
  "status": "active"
}
```

### 2.3 Route Object (from `GET /v1/routes`)

```json
{
  "id": "route-dhaka-01",
  "from_depot": "depot-gazipur",
  "to_station": "station-mirpur",
  "travel_ticks": 2,
  "max_load": 7000,
  "status": "available",
  "is_cross_region": false
}
```

---

## 3. Internal Database Schema (SQLite)

### 3.1 Decision Log

```sql
CREATE TABLE IF NOT EXISTS decision_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id         TEXT UNIQUE NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    tick                INTEGER NOT NULL,
    sim_time            TEXT,
    
    -- Decision details
    type                TEXT NOT NULL CHECK(type IN ('allocation','cancellation','rationing','rebalance')),
    depot_id            TEXT,
    station_id          TEXT,
    fuel_type           TEXT CHECK(fuel_type IN ('diesel','petrol','octane')),
    amount_liters       REAL CHECK(amount_liters >= 0),
    route_id            TEXT,
    
    -- Intelligence context
    risk_score_before   REAL,
    risk_score_after    REAL,
    hours_until_empty   REAL,
    trigger_reason      TEXT,
    explanation         TEXT,
    confidence          REAL,
    
    -- Approval
    status              TEXT NOT NULL CHECK(status IN ('proposed','validated','approved','rejected','executed','failed')),
    approval_mode       TEXT CHECK(approval_mode IN ('auto','human')),
    approved_by         TEXT,
    approved_at         TEXT,
    rejection_reason    TEXT,
    
    -- Execution
    allocation_id       TEXT,
    idempotency_key     TEXT,
    simulator_response  TEXT,
    executed_at         TEXT,
    
    -- Tracking
    expected_arrival    INTEGER,
    actual_arrival      INTEGER,
    delivery_status     TEXT,
    waste_liters        REAL DEFAULT 0
);

CREATE INDEX idx_decision_tick     ON decision_log(tick);
CREATE INDEX idx_decision_station  ON decision_log(station_id);
CREATE INDEX idx_decision_status   ON decision_log(status);
CREATE INDEX idx_decision_type     ON decision_log(type);
```

### 3.2 Event History

```sql
CREATE TABLE IF NOT EXISTS event_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT UNIQUE NOT NULL,
    detected_at         TEXT NOT NULL DEFAULT (datetime('now')),
    
    type                TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    target_type         TEXT NOT NULL CHECK(target_type IN ('route','station','depot','system')),
    
    start_tick          INTEGER NOT NULL,
    end_tick            INTEGER,
    duration_ticks      INTEGER,
    severity            TEXT CHECK(severity IN ('low','medium','high','critical')),
    
    description         TEXT,
    parameters          TEXT,  -- JSON
    
    -- Response tracking
    system_response     TEXT,  -- JSON: list of actions taken
    response_tick       INTEGER,
    response_latency    INTEGER,
    
    -- Outcome
    impact_description  TEXT,
    service_level_before REAL,
    service_level_after  REAL,
    incident_summary    TEXT   -- LLM-generated
);

CREATE INDEX idx_event_tick ON event_history(start_tick);
CREATE INDEX idx_event_type ON event_history(type);
```

### 3.3 Forecast Log

```sql
CREATE TABLE IF NOT EXISTS forecast_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    
    station_id          TEXT NOT NULL,
    fuel_type           TEXT NOT NULL,
    
    forecast_tick       INTEGER NOT NULL,  -- When forecast was made
    target_tick         INTEGER NOT NULL,  -- What tick it predicted
    horizon_ticks       INTEGER NOT NULL,  -- How far ahead
    
    predicted_demand    REAL NOT NULL,
    confidence_low      REAL,
    confidence_high     REAL,
    confidence_score    REAL,
    
    actual_demand       REAL,     -- Filled after target_tick passes
    absolute_error      REAL,     -- |predicted - actual|
    
    model_version       TEXT
);

CREATE INDEX idx_forecast_accuracy ON forecast_log(station_id, fuel_type);
CREATE INDEX idx_forecast_target   ON forecast_log(target_tick);
```

### 3.4 System State Snapshots

```sql
CREATE TABLE IF NOT EXISTS state_snapshot (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tick                INTEGER NOT NULL,
    captured_at         TEXT NOT NULL DEFAULT (datetime('now')),
    
    operating_mode      TEXT NOT NULL,
    service_level       REAL,
    
    -- Depot summaries (JSON)
    depots_state        TEXT NOT NULL,
    
    -- Station summaries (JSON)
    stations_state      TEXT NOT NULL,
    
    -- Route status (JSON)
    routes_state        TEXT NOT NULL,
    
    -- Active events (JSON)
    active_events       TEXT,
    
    -- Metrics
    total_dispatched    REAL,
    total_waste         REAL,
    total_delivered     REAL,
    decisions_count     INTEGER
);

CREATE INDEX idx_snapshot_tick ON state_snapshot(tick);
```

---

## 4. Data Flow Summary

```
                    BUP SIMULATOR
                         │
          ┌──────────────┼──────────────┐
          │              │              │
    REST Poll       SSE Stream     POST Allocations
    (every tick)   (continuous)    (on decision)
          │              │              │
          ▼              ▼              ▼
    ┌─────────────────────────────────────────┐
    │         STATE MANAGER                    │
    │                                          │
    │  Raw Sim Data → Validated → WorldState  │
    │                                          │
    │  ┌──────────┐  ┌──────────┐             │
    │  │ Current  │  │ Cached   │             │
    │  │ State    │  │ State    │             │
    │  └────┬─────┘  └──────────┘             │
    └───────┼──────────────────────────────────┘
            │
    ┌───────┼────────────────────────────────┐
    │       ▼                                │
    │  ┌──────────┐  ┌──────────┐           │
    │  │Forecaster│  │ Anomaly  │           │
    │  │          │  │ Detector │           │
    │  └────┬─────┘  └────┬─────┘           │
    │       │              │                 │
    │       ▼              ▼                 │
    │  ┌──────────────────────────┐          │
    │  │    LP OPTIMIZER          │          │
    │  │    or FALLBACK           │          │
    │  └────────────┬─────────────┘          │
    │               │                        │
    │               ▼                        │
    │  ┌──────────────────────────┐          │
    │  │   SHIPMENT GUARD         │          │
    │  │   (trap validation)      │          │
    │  └────────────┬─────────────┘          │
    │               │                        │
    │               ▼                        │
    │  ┌──────────────────────────┐          │
    │  │   DECISION HUB           │          │
    │  │   (approve/reject/queue) │          │
    │  └────────────┬─────────────┘          │
    │               │                        │
    │        ┌──────┴──────┐                 │
    │        ▼             ▼                 │
    │   ┌─────────┐  ┌──────────┐           │
    │   │ SQLite  │  │ Simulator│           │
    │   │ Audit   │  │ POST     │           │
    │   │ Log     │  │ /v1/alloc│           │
    │   └─────────┘  └──────────┘           │
    │                                        │
    │        INTELLIGENCE ENGINE              │
    └────────────────────────────────────────┘
```

---

## 5. Network Topology Reference

### 5.1 Complete Route Table

| Route ID | From | To | Ticks | Max Load | Cross-Region | Single Route? |
|----------|------|----|-------|----------|-------------|---------------|
| route-dhaka-01 | depot-gazipur | station-mirpur | 2 | 7,000 L | No | No |
| route-dhaka-02 | depot-gazipur | station-mirpur | 2 | 6,500 L | No | No |
| route-dhaka-03 | depot-gazipur | station-tongi | 2 | 6,500 L | No | ⚠️ **YES** |
| route-chatt-01 | depot-patiya | station-karnaphuli | 2 | 7,000 L | No | No |
| route-chatt-02 | depot-patiya | station-karnaphuli | 3 | 6,000 L | No | No |
| route-chatt-03 | depot-patiya | station-coxsbazar | 3 | 6,000 L | No | ⚠️ **YES** |
| route-cross-01 | depot-gazipur | station-karnaphuli | 4 | 5,000 L | ⚠️ Yes | — |
| route-cross-02 | depot-patiya | station-mirpur | 4 | 5,000 L | ⚠️ Yes | — |

### 5.2 Supply Schedule Summary

| Delivery # | Depot | Tick | Diesel | Petrol | Octane | Total |
|-----------|-------|------|--------|--------|--------|-------|
| 1-6 | Gazipur | 48,96,192,288,384,480 | 5-7k | 4-6k | 2-3k | ~12k each |
| 7-12 | Gazipur | 576+ | varies | varies | varies | ~10k each |
| 13-18 | Patiya | 48,96,192,288,384,480 | 5-6k | 4-5k | 2-3k | ~11k each |
| 19-22 | Patiya | 576+ | varies | varies | varies | ~9k each |
| **TOTAL** | — | — | — | — | — | **~216,000 L** |

> ⚠️ **CRITICAL**: These 22 deliveries are ALL the fuel you will ever receive. Once they're consumed or wasted, there is no more.

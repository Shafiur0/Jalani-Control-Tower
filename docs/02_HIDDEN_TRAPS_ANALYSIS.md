# Hidden Traps Analysis — Deep Dive

> **Project:** Jalani Control Tower  
> **Document:** Critical Edge Cases & Simulator Pitfalls  
> **Version:** 1.0.0  

---

## Overview

The BUP Fuel Supply Simulator is **deterministic but unforgiving**. It silently discards data, destroys shipments, and penalizes poor timing. This document catalogs every identified trap, its root cause, its impact, and our architectural countermeasure.

---

## 🔴 TRAP 1: Full Depot — Supply Silently Discarded

### Description
When a scheduled supply delivery arrives at a depot and the depot is **at capacity for that fuel type**, the excess fuel is **permanently destroyed with zero warning**.

### Root Cause
The simulator's supply arrival system does not check remaining capacity. It adds fuel up to capacity and drops the rest.

### Impact: **CATASTROPHIC**
- Supply deliveries are finite (22 total, ~216,000 L total).
- Losing even one delivery can cause chain-reaction stockouts across multiple stations.
- There is **no SSE event** and **no error response** — it just happens silently.

### Detection
```python
# Monitor depot capacity headroom before each supply arrival
def check_depot_capacity_trap(depot, supply_arrival):
    for fuel_type in ['diesel', 'petrol', 'octane']:
        remaining = depot.capacity[fuel_type] - depot.current[fuel_type]
        arriving = supply_arrival.amount[fuel_type]
        if arriving > remaining:
            ALERT(f"⚠️ DEPOT {depot.id}: {fuel_type} overflow! "
                  f"Arriving: {arriving}L, Headroom: {remaining}L, "
                  f"WASTED: {arriving - remaining}L")
```

### Countermeasure
1. **Pre-emptive Dispatch**: Dispatch fuel FROM depot BEFORE supply arrives to create headroom.
2. **Supply Arrival Tracker**: Track all upcoming supplies from `/v1/supply-arrivals`.
3. **Headroom Budget**: Maintain a minimum `arrival_size * 1.1` headroom at all times.
4. **Emergency Dump**: If headroom cannot be created, dispatch to any available station — even suboptimally — rather than waste.

### Architecture Response
```
State Manager → check headroom 4 ticks before arrival
             → trigger priority dispatch if headroom < expected_delivery
             → log waste risk to audit trail
```

---

## 🔴 TRAP 2: Full Station — Delivery Excess Discarded

### Description
When a dispatched fuel shipment **arrives** at a station and the station is **at capacity for that fuel type**, the excess above capacity is **permanently destroyed**.

### Root Cause
The simulator caps station inventory at `max_capacity`. Any delivered fuel exceeding the cap is dropped.

### Impact: **HIGH**
- The fuel has already been deducted from the depot when dispatched.
- Wasted fuel = wasted depot supply = accelerated depot depletion.
- Combined with Trap 1, this creates a two-way waste vector.

### Detection
```python
def check_station_overflow_risk(station, shipment):
    for fuel_type in ['diesel', 'petrol', 'octane']:
        projected_level = (station.current[fuel_type] 
                          - predicted_consumption(station, shipment.transit_ticks, fuel_type)
                          + shipment.amount[fuel_type])
        overflow = projected_level - station.capacity[fuel_type]
        if overflow > 0:
            WARN(f"⚠️ STATION {station.id}: {fuel_type} overflow risk! "
                 f"Projected overflow: {overflow}L")
```

### Countermeasure
1. **Projected Inventory Model**: Calculate station inventory at time of arrival (current - projected demand during transit + delivery amount).
2. **Never Exceed Rule**: Never dispatch more fuel than `station.capacity - projected_level_at_arrival`.
3. **Demand Forecast Integration**: Use demand forecasting to estimate consumption during transit window.
4. **Split Shipments**: If a large shipment would overflow one station, split across multiple.

### Architecture Response
```
LP Optimizer → constraint: delivery ≤ station.capacity - projected_inventory_at_arrival
Shipment Guard → pre-flight check on every allocation before POST
```

---

## 🔴 TRAP 3: Road Closure at Departure Time — Shipment Lost

### Description
If a route becomes `unavailable` at the **exact tick** when a shipment is scheduled to depart, the fuel is **lost** — deducted from depot but never delivered.

### Root Cause
The simulator processes road closures before processing departures. A shipment dispatched on tick T via a road that closes on tick T is caught in the closure.

### Impact: **CATASTROPHIC**
- Total loss of dispatched fuel.
- No recovery mechanism — fuel is gone from depot and gone from transit.
- Combines with Trap 1 to accelerate depot depletion.

### Detection
```python
def check_route_closure_trap(route, allocation_tick):
    events = get_active_events()
    for event in events:
        if (event.affects == route.id and 
            event.type == 'road_closure' and
            event.start_tick <= allocation_tick <= event.end_tick):
            CRITICAL(f"🚫 ROUTE {route.id} CLOSING at tick {event.start_tick}! "
                     f"DO NOT DISPATCH!")
```

### Countermeasure
1. **Pre-Flight Route Check**: Check route status AND scheduled events before dispatching.
2. **Departure Buffer**: Never dispatch on the same tick as a road closure start.
3. **In-Transit Monitoring**: Track all in-transit shipments and flag those on routes about to close.
4. **Event Horizon Scanning**: Poll `/v1/events` every tick to catch scheduled closures.
5. **Cancel Before Departure**: Use `POST /v1/allocations/{id}/cancel` if a closure is detected after dispatch but before actual departure.

### Architecture Response
```
Shipment Guard → validate route.status == 'available' AND no scheduled closure within 2 ticks
Event Bus → listen for road_closure events → immediately check in-transit shipments
Allocation Executor → include route check in pre-POST validation
```

---

## 🟡 TRAP 4: Stale Data — GET Returns Outdated State

### Description
The simulator's fault injection system can return **stale cached data** via the `stale_data` fault. GET requests return data from a previous tick, not the current state.

### Impact: **MEDIUM-HIGH**
- Decisions based on stale data may cause overflows (Trap 2) or missed shortages.
- The response looks perfectly valid — there's no HTTP error code.
- Only detectable by checking tick correlation.

### Detection
```python
def validate_data_freshness(response_data, current_tick):
    """Detect stale data by checking tick alignment."""
    if hasattr(response_data, 'as_of_tick'):
        staleness = current_tick - response_data.as_of_tick
        if staleness > 1:
            WARN(f"⚠️ STALE DATA: Response is {staleness} ticks old!")
            return False
    return True
```

### Countermeasure
1. **Tick Stamping**: Record the tick when each GET response was received.
2. **Staleness Detection**: Compare response tick against known current tick from `/v1/instance`.
3. **Multi-Read Validation**: Read critical data twice; if values differ, use the more recent one.
4. **Conservative Decisions**: When stale data is suspected, use worst-case assumptions.

---

## 🟡 TRAP 5: Idempotency Key Reuse — Duplicate Dispatch

### Description
Each `POST /v1/allocations` requires an `Idempotency-Key` header. If the same key is reused, the simulator returns the **previous response** (the original allocation), not a new one. But if a *different* key is used for the *same* logical dispatch (e.g., after a retry), a **duplicate shipment** is created.

### Impact: **HIGH**
- Duplicate dispatch = double fuel deducted from depot.
- Can trigger depot underflow in tight-supply scenarios.
- Retry logic must be carefully designed.

### Countermeasure
1. **Deterministic Key Generation**: Generate idempotency keys from `(depot_id, station_id, fuel_type, tick)`.
2. **Retry with Same Key**: Always retry with the **same** idempotency key.
3. **Allocation Cache**: Cache all POST responses; verify allocation creation before retrying.
4. **State Reconciliation**: Periodically check `GET /v1/allocations` against local records.

```python
def generate_idempotency_key(depot_id, station_id, fuel_type, tick):
    """Deterministic key ensures retry safety."""
    raw = f"{depot_id}:{station_id}:{fuel_type}:{tick}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
```

---

## 🟡 TRAP 6: Dispatch Rate Limit — Silent Throttling

### Description
Each depot has a `dispatch_rate` (liters per tick). If total dispatches from a depot in a single tick exceed this limit, the allocation request is **rejected** (HTTP 409 Conflict).

### Impact: **MEDIUM**
- The LP solver may generate a plan that dispatches more than the limit.
- Rejections waste a tick of action.
- Must be modeled as a hard constraint.

### Countermeasure
1. **LP Constraint**: Add `sum(dispatch_from_depot_d_at_tick_t) <= depot_d.dispatch_rate`.
2. **Queue Excess**: If the optimizer wants to send more, queue excess for next tick.
3. **Priority Ordering**: Dispatch to highest-risk stations first within the rate limit.

---

## 🟡 TRAP 7: SSE Stream Disconnect — Silent Data Loss

### Description
The SSE stream (`/v1/stream`) may disconnect due to fault injection (`stream_disconnect` fault). Events published during disconnection are **lost** — there is no replay buffer.

### Impact: **MEDIUM**
- Missed events = missed crisis notifications, missed supply arrivals.
- System may not react to demand spikes or road closures in time.

### Countermeasure
1. **SSE as Advisory Only**: Never rely solely on SSE for state changes.
2. **Polling as Primary**: Use REST polling every tick as the primary data source.
3. **Auto-Reconnect**: Detect disconnect and reconnect with exponential backoff.
4. **Reconciliation on Reconnect**: Full state refresh after reconnection.

---

## 🟡 TRAP 8: Cross-Region Routes — Double Transit Time

### Description
Cross-region backup routes (Gazipur → Karnaphuli, Patiya → Mirpur) have **4-tick transit time** (vs 2-3 for intra-region routes) and **lower capacity** (5,000 L vs 6,000-7,000 L).

### Impact: **MEDIUM**
- Fuel in transit for twice as long → more demand consumed before arrival.
- Lower capacity means more shipments needed.
- Station may overflow or run out during extended transit.

### Countermeasure
1. **Transit-Adjusted Delivery**: Account for 4-tick consumption when calculating delivery amounts.
2. **Preference Ranking**: Always prefer intra-region routes; use cross-region only when:
   - Intra-region routes are closed.
   - Local depot is depleted.
   - Emergency rebalancing is needed.
3. **LP Penalty**: Add cost penalty for cross-region routes in the objective function.

---

## 🟡 TRAP 9: Single-Route Stations — No Backup

### Description
Station **Tongi** and Station **Cox's Bazar** each have only **one** route connecting them to their depot. If that route closes, the station is **completely cut off**.

### Impact: **HIGH**
- Complete supply interruption with zero alternative.
- Station inventory depletes at demand rate with no resupply.
- No cross-region route exists for these stations.

### Countermeasure
1. **Preemptive Stocking**: Keep single-route stations at higher inventory buffers.
2. **Priority Dispatch**: Dispatch to these stations first when route is open.
3. **Event Monitoring**: Monitor for road closure events affecting these routes with higher urgency.
4. **Rationing**: If route closes, recommend demand reduction (if possible) via operator alert.

---

## 🟡 TRAP 10: Demand Multiplier Changes — Sudden Demand Spike

### Description
The simulator can change a station's `demand_multiplier` mid-simulation via crisis events. This multiplies ALL demand at that station by the given factor.

### Impact: **MEDIUM-HIGH**
- Demand forecasts become instantly inaccurate.
- A 2x multiplier can cause a station to stockout in half the expected time.
- Must re-run optimization immediately after detection.

### Countermeasure
1. **Event-Driven Reforecast**: Any demand multiplier change triggers immediate re-forecasting.
2. **Anomaly Detection**: Compare actual demand vs. forecast; if deviation > 20%, investigate.
3. **Dynamic Reoptimization**: Re-run LP optimizer when demand profile changes.

---

## Summary: Trap Severity Matrix

| # | Trap | Severity | Detection Difficulty | Countermeasure Complexity |
|---|------|----------|---------------------|--------------------------|
| 1 | Full Depot — Supply Lost | 🔴 Critical | Hard (silent) | Medium |
| 2 | Full Station — Delivery Lost | 🔴 Critical | Medium (predictable) | Medium |
| 3 | Road Closure at Dispatch | 🔴 Critical | Medium (event check) | Low |
| 4 | Stale Data | 🟡 High | Hard (looks valid) | Medium |
| 5 | Idempotency Key Misuse | 🟡 High | Low (duplicate check) | Low |
| 6 | Dispatch Rate Limit | 🟡 Medium | Easy (HTTP 409) | Low |
| 7 | SSE Disconnect | 🟡 Medium | Easy (connection drop) | Low |
| 8 | Cross-Region Penalty | 🟡 Medium | Easy (known topology) | Low |
| 9 | Single-Route Isolation | 🟡 High | Easy (known topology) | Medium |
| 10 | Demand Multiplier Spike | 🟡 High | Medium (event check) | Medium |

---

## Trap Defense Architecture

```
                    ┌─────────────────────────────┐
                    │     SHIPMENT GUARD           │  ← Pre-flight validation
                    │                              │
                    │  ✓ Route available?           │ → Trap 3
                    │  ✓ Route closure in window?   │ → Trap 3
                    │  ✓ Depot has inventory?        │ → Trap 1, 6
                    │  ✓ Depot under dispatch limit? │ → Trap 6
                    │  ✓ Station has headroom?       │ → Trap 2
                    │  ✓ Data is fresh?              │ → Trap 4
                    │  ✓ Idempotency key unique?     │ → Trap 5
                    │                              │
                    │  ALL PASS? → Execute          │
                    │  ANY FAIL? → Block + Alert    │
                    └─────────────────────────────┘
```

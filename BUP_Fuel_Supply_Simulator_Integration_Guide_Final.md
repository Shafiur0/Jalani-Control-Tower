**![][image1]**  
**![][image2]**  
**In association with**   
**![][image3]**

**HACKATHON FINALS**

# **BUP Fuel Supply Simulator**

**Integration and Interaction Guide**

| Scope: This document covers everything a participant application needs to integrate with the simulator and interact with its API surfaces (REST, SSE, admin). It does not cover challenge framing, deliverables, evaluation criteria, demo narrative, or strategy \- see the official participant brief for those. |
| :---- |

| What is this simulator?  The BUP Fuel Supply Simulator is a deterministic, locally-runnable environment that models a small Bangladeshi fuel supply chain . There are two regions, two depots, four stations, six routes, three fuel types, and a 15-minute (controllable) tick clock. It exists so hackathon participants can build their own decision-making system against a realistic, reproducible world instead of guessing how a real supply chain behaves. |
| ----- |

| Why does it exist?  The hackathon challenges participants to design a system that moves fuel from depots to stations while keeping stations stocked and customers happy. Real supply chains are noisy, slow, and unfair to compare against. This simulator gives every participant the same world with the same demand curve, the same initial inventory, and the same injected crises, so judges can fairly score their decisions rather than their luck. |
| ----- |

| How do I integrate my system with it? Your application runs outside the simulator container and talks to it over HTTP. You read the world through REST endpoints (/v1/instance, /v1/depots, /v1/stations, /v1/routes, /v1/supply-arrivals, /v1/demand-history, /v1/metrics, /v1/allocations), listen for change notifications via Server-Sent Events (/v1/stream), and write your fuel-replenishment decisions through exactly one endpoint : POST /v1/allocations. The simulator then runs those decisions against the world, advances time, and reports back what happened.Use the historical and simulated data available through these interfaces to build predictive capabilities and continuously improve your decisions. Treat REST as the source of truth; treat SSE as a hint that something changed. |
| ----- |

# **Table of Contents**

1\.  Fast start	

2\.  Hard rules	

3\.  Quick reference card	

4\.  Public /v1/\* endpoints	

5\.  POST /v1/allocations ( the only domain write )	

6\.  SSE stream /v1/stream	

7\.  Admin /admin/\* endpoints	

8\.  The simulated world	

9\.  Status-code cheat sheet	

10\.  Defensive client checklist	

# **1\. Fast start**

The simulator ships as a single docker-compose.yml:

| services: |
| :---- |
|   simulator-api: |
|     image: asifmahmoud414/bup-fuel-supply-simulator:1.0.0 |
|     environment: |
|       SIMULATION\_SPEED: \${SIMULATION\_SPEED:-8} |
|       TICK\_MINUTES: \${TICK\_MINUTES:-15} |
|       SIMULATOR\_START\_MODE: \${SIMULATOR\_START\_MODE:-paused}  \# Options: paused, running |
|     ports: |
|       \- "8000:8000" |

**SIMULATION\_SPEED :** Wall-clock ticks per second while running. Higher \= faster time.

**TICK\_MINUTES :** Simulated minutes advanced per tick. Larger \= coarser steps.

**SIMULATOR\_START\_MODE :** Start ticking immediately (running) or stay paused until resumed.

Start the stack with:

| docker compose up \-d |
| :---- |
| curl \-s http://localhost:8000/v1/health |

# **2\. Hard rules**

* **The simulator is the world, not the brain.** It does not predict, optimize, recommend, or decide. It executes your POST /v1/allocations calls deterministically and reports what happened.

* **REST is the source of truth.** Treat SSE as a notification stream — always re-GET the affected resource after every event.

* **The world is deterministic.** Same scenario \+ same seed \+ same actions \+ same injected events ⇒ byte-identical state, including per-tick demand jitter.

* **The scenario is baked into the image.** The active scenario cannot be switched at runtime. The default scenario has no preloaded events.

* **One simulator instance per participant.** Single-tenant local. There is no central server.

* **Do not modify the simulator source** to solve the challenge. Judges run your submission against the published image.

* **/admin/\* paths bypass all fault injection.** To test fault handling, hit /v1/\* paths only. /v1/health also bypasses faults (use as liveness probe).

* **Only allocations can be written from /v1/\*.** Every other resource is read-only. Crisis events and faults are injected via /admin/\* (your app can call them too for self-test scenarios).

# **3\. Quick reference card**

| Setting | Details |
| :---- | :---- |
| Base URL (local) | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| Simulator Dashboard | http://localhost:8000/admin |
| Idempotency-Key | Not in headers. Sent as idempotency\_key body field on /v1/allocations. |
| Stale data signal | X-Simulator-Stale: true response header on /v1/\* GETs when a stale-data fault is active. |
| Default tick | 15 simulated minutes per tick (TICK\_MINUTES env var) |
| Default speed | 8 ticks/sec wall-clock while RUNNING (SIMULATION\_SPEED) |

# **4\. Public /v1/\* Endpoints**

Every endpoint below returns JSON unless noted. All /v1/\* endpoints except /v1/health are subject to fault injection; /admin/\* are not.

## **4.1 GET /v1/health**

**Bypasses faults.** Use as your liveness probe.

| { |
| :---- |
|   "status": "ok", |
|   "database": "ok", |
|   "simulation": { "status": "PAUSED", "tick": 0 } |
| } |

## **4.2 GET /v1/instance**

The simulation instance (there is only one). Read often for current tick, sim\_time, and status.

| { |
| :---- |
|   "id": 1, |
|   "scenario\_id": "baseline", |
|   "scenario\_version": "1.0", |
|   "seed": 12345, |
|   "sim\_time": "2026-01-01T00:00:00+00:00", |
|   "tick": 0, |
|   "tick\_minutes": 15, |
|   "status": "PAUSED" |
| } |

**status** : {"PAUSED", "RUNNING"}.

## **4.4 GET /v1/regions**

| \[ |
| :---- |
|   { "id": "region-dhaka",       "name": "Dhaka Division",       "demand\_factor": 1.00 }, |
|   { "id": "region-chattogram",  "name": "Chattogram Division",  "demand\_factor": 1.08 } |
| \] |

## **4.5 GET /v1/depots and GET /v1/depots/{entity\_id}**

| \[ |
| :---- |
|   { |
|     "id": "depot-gazipur", |
|     "name": "Gazipur Depot", |
|     "region\_id": "region-dhaka", |
|     "status": "OPEN", |
|     "dispatch\_capacity\_per\_tick": 12000, |
|     "capacity":   { "DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000 }, |
|     "inventory":  { "DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000 } |
|   } |
| \] |

**depot.status**: {"OPEN", "CONSTRAINED"}.  404 {"detail":{"code":"NOT\_FOUND"}} if id is unknown.

## **4.6 GET /v1/stations and GET /v1/stations/{entity\_id}**

| \[ |
| :---- |
|   { |
|     "id": "station-mirpur", |
|     "name": "Mirpur Fuel Station", |
|     "region\_id": "region-dhaka", |
|     "status": "OPEN", |
|     "demand\_profile": "urban\_high", |
|     "demand\_multiplier": 1.0, |
|     "capacity":   { "DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000 }, |
|     "inventory":  { "DIESEL":  9000, "PETROL":  9000, "OCTANE": 5000 } |
|   } |
| \] |

**station.status :** {"OPEN", "OUTAGE"}. demand\_multiplier is mutated at runtime by demand\_spike events.

## **4.7 GET /v1/routes**

| \[ |
| :---- |
|   { |
|     "id": "route-gazipur-mirpur", |
|     "source\_depot\_id": "depot-gazipur", |
|     "destination\_station\_id": "station-mirpur", |
|     "transit\_ticks": 2, |
|     "max\_shipment": 7000, |
|     "status": "AVAILABLE" |
|   } |
| \] |

**route.status** : {"AVAILABLE", "DISRUPTED"}.

## **4.8 GET /v1/supply-arrivals**

Sorted by planned\_tick ascending.

| \[ |
| :---- |
|   { |
|     "id": "supply-001", |
|     "depot\_id": "depot-gazipur", |
|     "fuel\_type": "DIESEL", |
|     "quantity": 18000, |
|     "planned\_tick": 12, |
|     "actual\_tick": null, |
|     "status": "SCHEDULED" |
|   } |
| \] |

**status** : {"SCHEDULED", "DELAYED", "ARRIVED"}.

## 

## **4.9 GET /v1/events**

Domain events (injected by you or preloaded by the scenario file), id-desc.

| \[ |
| :---- |
|   { |
|     "id": 1, |
|     "type": "demand\_spike", |
|     "start\_tick": 8, |
|     "end\_tick": 20, |
|     "status": "RESOLVED", |
|     "parameters": { "region\_ids": \["region-dhaka"\], "multiplier": 1.8 } |
|   } |
| \] |

**status** : {"SCHEDULED", "ACTIVE", "RESOLVED"}.

## **4.10 GET /v1/allocations**

Sorted id-desc. Every allocation your app has created (and any preloaded ones).

| \[ |
| :---- |
|   { |
|     "id": 1, |
|     "idempotency\_key": "demo-001", |
|     "source\_depot\_id": "depot-gazipur", |
|     "destination\_station\_id": "station-mirpur", |
|     "route\_id": "route-gazipur-mirpur", |
|     "fuel\_type": "DIESEL", |
|     "quantity": 3000, |
|     "created\_tick": 5, |
|     "departure\_tick": 6, |
|     "expected\_arrival\_tick": 8, |
|     "actual\_arrival\_tick": 8, |
|     "status": "ARRIVED", |
|     "failure\_reason": null |
|   } |
| \] |

**status** : {"PENDING", "IN\_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"}.

## **4.11 GET /v1/demand-history?station\_id=\&limit=**

Time-series of demand observations. **limit is clamped to \[1, 2000\], default 200\.** The table grows unboundedly — always call with a limit and filter to recent N.

| \[ |
| :---- |
|   { |
|     "id": 100, |
|     "station\_id": "station-mirpur", |
|     "fuel\_type": "DIESEL", |
|     "tick": 12, |
|     "sim\_time": "2026-01-01T03:00:00+00:00", |
|     "demand\_liters": 95.123, |
|     "served\_liters": 95.123, |
|     "unmet\_liters": 0.0 |
|   } |
| \] |

A row is inserted per (station\_id, fuel\_type) per tick \-\> 4 stations × 3 fuels \= 12 rows per tick.

## **4.12 GET /v1/metrics**

Aggregated ground-truth metrics since simulation start.

| { |
| :---- |
|   "served\_demand\_liters": 12345.678, |
|   "unmet\_demand\_liters":    234.567, |
|   "service\_level":          0.981408, |
|   "allocation\_liters":     9800.000, |
|   "allocation\_failures":   2 |
| } |

* **service\_level** \= served / (served \+ unmet) — **1.0** means zero unmet demand.

* **allocation\_liters** counts only IN\_TRANSIT \+ ARRIVED shipments (not PENDING, FAILED, CANCELLED).

* **allocation\_failures** is the count of status="FAILED" allocations (route disruption at departure time).

# **5\. POST /v1/allocations — the only domain write**

| This is the only domain-mutating endpoint participants should call from /v1/\*. |
| :---- |

## **5.1 Request**

| POST /v1/allocations |
| :---- |
| Content-Type: application/json |

| { |
| :---- |
|   "idempotency\_key": "demo-001", |
|   "source\_depot\_id": "depot-gazipur", |
|   "destination\_station\_id": "station-mirpur", |
|   "route\_id": "route-gazipur-mirpur", |
|   "fuel\_type": "DIESEL", |
|   "quantity": 3000 |
| } |

| Field | Type | Required | Constraints |
| :---- | :---- | :---- | :---- |
| idempotency\_key | string | yes | length 1–150, unique unless replaying the exact same request |
| source\_depot\_id | string | yes | must exist in /v1/depots |
| destination\_station\_id | string | yes | must exist in /v1/stations |
| route\_id | string | yes | must exist in /v1/routes |
| fuel\_type | enum | yes | DIESEL | PETROL | OCTANE |
| quantity | float | yes | \> 0, ≤ route.max\_shipment |

## **5.2 Validation order (first failure wins)**

* **Idempotency check** — see 5.5.

* **NOT\_FOUND (404)** if depot, station, or route id is unknown.

* **ROUTE\_MISMATCH (409)** if the route's (source\_depot\_id, destination\_station\_id) ≠ the ones in your request.

* **DEPOT\_CLOSED (409)** if depot.status is not in {"OPEN", "CONSTRAINED"}.

* **STATION\_CLOSED (409)** if station.status \!= "OPEN".

* **ROUTE\_DISRUPTED (409)** if route.status \!= "AVAILABLE".

* **ROUTE\_CAPACITY\_EXCEEDED (409)** if quantity \> route.max\_shipment.

* **INSUFFICIENT\_INVENTORY (409)** if depot.inventory\[fuel\] \< quantity.

* **DISPATCH\_CAPACITY\_EXCEEDED (409)** if (sum of in-flight \+ pending quantities from this depot on this tick) \+ quantity \> depot.dispatch\_capacity\_per\_tick.

* **DESTINATION\_CAPACITY\_EXCEEDED (409)** if station.inventory\[fuel\] \+ quantity \> station.capacity\[fuel\].

## **5.3 Success response (201 Created)**

| { |
| :---- |
|   "id": 1, |
|   "idempotency\_key": "demo-001", |
|   "source\_depot\_id": "depot-gazipur", |
|   "destination\_station\_id": "station-mirpur", |
|   "route\_id": "route-gazipur-mirpur", |
|   "fuel\_type": "DIESEL", |
|   "quantity": 3000, |
|   "created\_tick": 5, |
|   "departure\_tick": null, |
|   "expected\_arrival\_tick": null, |
|   "actual\_arrival\_tick": null, |
|   "status": "PENDING", |
|   "failure\_reason": null |
| } |

## **5.4 Idempotency rules**

* **Same key, same body → return existing allocation** (HTTP 201, not 200). Safe retry.

* **Same key, different body → 409 IDEMPOTENCY\_KEY\_MISMATCH.** The first submission wins; you must use a new key.

* **Cancellation does NOT free the key.** Once used, an idempotency\_key is permanently occupied.

## **5.5 POST /v1/allocations/{allocation\_id}/cancel**

Refunds the depot inventory and marks the allocation CANCELLED. Only valid for status="PENDING".

* **200 OK** with the updated allocation.

* **404 ALLOCATION\_NOT\_FOUND** if id unknown.

* **409 CANNOT\_CANCEL** if status ≠ PENDING.

# **6\. SSE stream /v1/stream**

GET /v1/stream returns text/event-stream. It is the only way to receive push notifications from the simulator.

## **6.1 Wire protocol**

* **On connect:** server sends : connected\\n\\n (SSE comment, ignore).

* **Each event:** event: \<name\>\\ndata: \<json\>\\n\\n

* **After 15 seconds of silence:** : keepalive\\n\\n.

* **Buffer:** asyncio.Queue(maxsize=200) per subscriber. **If you fall behind by \>200 events, your queue is silently dropped** and the stream will keep flowing to other subscribers. You must reconnect.

## **6.2 Reconnection**

| There is no Last-Event-ID replay. On reconnect you get : connected\\n\\n and then only events from that moment forward. Always re-fetch your state via REST after a reconnect. |
| :---- |

## **6.3 Event names \+ payloads**

| Event name | When | Payload |
| :---- | :---- | :---- |
| simulation.tick | End of every tick | {"tick": \<int\>, "sim\_time": "\<ISO-8601\>"} |
| allocation.status\_changed | Allocation created / departed / arrived / failed / cancelled | serialize(allocation) — full allocation object |
| inventory.updated | Depot inventory changed | {"entity\_type":"depot","entity\_id":"\<id\>","inventory":{...}} |
| simulator.notice | POST /admin/reset, background-runner exception | {"message":"..."} or {"level":"error","message":"..."} |

Audit-log action values like event.started, event.resolved, supply.arrived, allocation.departed, allocation.arrived, simulation.tick are not SSE event names — read them via GET /admin/audit.

## **6.4 Fault interaction**

* If a stream\_disconnect fault is active, **GET /v1/stream returns 503 {"detail":{"code":"FAULT\_INJECTED"}}** instead of opening the stream.

* If a stale\_data fault is active, **non-stream /v1/\* GET responses include X-Simulator-Stale: true**. The SSE stream itself does not.

# **7\. Admin /admin/\* endpoints**

All /admin/\* paths **bypass fault injection**. Designed for organizer use, but participants will find them invaluable for self-testing (injecting events, advancing ticks deterministically, clearing faults).

## **7.1 GET /admin**

Server-rendered HTML console. Auto-refreshes every 2 seconds. Shows live instance state, metrics, row counts, recent audit log, and forms to inject events / faults and run/pause/step/reset the simulation. Useful for visual debugging.

## **7.2 POST /admin/run — start the simulation**

Sets SimulationInstance.status \= "RUNNING". The background runner begins ticking.

## **7.3 POST /admin/pause — pause the simulation**

Sets SimulationInstance.status \= "PAUSED". The background runner idles; existing PENDING / IN\_TRANSIT allocations stay frozen.

## **7.4 POST /admin/toggle — flip RUNNING ↔ PAUSED**

Convenience for UIs.

## **7.5 POST /admin/step — deterministic single-tick advance**

Calls tick\_once() exactly once. Works regardless of status (you can step while PAUSED). Returns:

|  { "tick": 6, "sim\_time": "2026-01-01T01:30:00+00:00" } |
| :---- |
| **This is the recommended way to drive deterministic tests.** Pause, run your allocations, then call /admin/step a known number of times for reproducible outcomes. |

## **7.6 POST /admin/reset — wipe everything and reload the active scenario**

| Hard reset. Deletes every row from demand\_observations, allocations, events, faults, audit\_logs, supply\_arrivals, routes, stations, depots, regions, and simulation\_instances, then reloads from the baked scenario YAML. Publishes simulator.notice {message: "Simulation reset"}. |
| :---- |

## **7.7 POST /admin/events — inject a crisis event**

| POST /admin/events |
| :---- |
| Content-Type: application/json |

| { |
| :---- |
|   "type": "demand\_spike", |
|   "start\_tick": 8, |
|   "duration\_ticks": 12, |
|   "parameters": { "region\_ids": \["region-dhaka"\], "multiplier": 1.8 } |
| } |

| Field | Type | Required | Constraints |
| :---- | :---- | :---- | :---- |
| type | enum | yes | one of six values (see §7.8) |
| start\_tick | int | yes | \>= 0 |
| duration\_ticks | int | yes | \> 0 |
| parameters | object | no | defaults to {} |

end\_tick \= start\_tick \+ duration\_ticks. Returns 201 Created with the persisted event.

## **7.8 Event-type parameter shapes**

| type | Parameters | Effect while ACTIVE | Reversed on RESOLVE |
| :---- | :---- | :---- | :---- |
| demand\_spike | multiplier (def 1.5), station\_ids\[\], region\_ids\[\] | multiplies each affected station's demand\_multiplier by multiplier | divides by multiplier (≥ 0.01) |
| route\_disruption | route\_ids\[\] | sets each route → DISRUPTED | routes → AVAILABLE |
| station\_outage | station\_ids\[\] | sets each station → OUTAGE (served drops to 0\) | stations → OPEN |
| depot\_constraint | depot\_ids\[\] | sets each depot → CONSTRAINED (still shippable, but signals reduced capacity) | depots → OPEN |
| shipment\_delay | delay\_ticks (def 2), depot\_ids\[\], fuel\_types\[\] | SupplyArrival.planned\_tick \+= delay\_ticks, status → DELAYED | one-shot — does not auto-undo |
| supply\_shortfall | factor (def 0.5), depot\_ids\[\], fuel\_types\[\] | SupplyArrival.quantity \*= factor | one-shot — does not auto-restore |

station\_ids / region\_ids / route\_ids / depot\_ids are filters: if the list is empty, the event applies to all entities of that type. fuel\_types works the same way.

## **7.9 POST /admin/faults — inject a fault**

| POST /admin/faults |
| :---- |
| Content-Type: application/json |

| { |
| :---- |
|   "type": "stale\_data", |
|   "duration\_seconds": 60, |
|   "parameters": {} |
| } |

| Field | Type | Required | Constraints |
| :---- | :---- | :---- | :---- |
| type | enum | yes | one of five values (see §7.10) |
| duration\_seconds | int | yes | 0 \< x ≤ 3600 |
| parameters | object | no | defaults to {} |

start\_wall\_time \= now(), end\_wall\_time \= now() \+ duration\_seconds, active=true. Auto-expires — no need to clear manually unless you want to.

## **7.10 Fault-type effects**

| type | parameters | Effect on /v1/\* (not /admin/\*, not /v1/health) |
| :---- | :---- | :---- |
| latency | { "delay\_ms": 500 } (default 500\) | sleeps delay\_ms / 1000 seconds before every non-admin, non-health request |
| unavailable | — | returns 503 {"error":{"code":"FAULT\_INJECTED","message":"Simulator API temporarily unavailable."}} |
| error\_rate | { "rate": 0.25 } (default 0.25) | with probability rate, returns 503 {"error":{"code":"FAULT\_INJECTED","message":"Injected transient API error."}} |
| stale\_data | — | does NOT block requests; adds X-Simulator-Stale: true to /v1/\* GET responses |
| stream\_disconnect | — | GET /v1/stream returns 503 {"detail":{"code":"FAULT\_INJECTED"}} (note: nested under detail, not error) |

## **7.11 POST /admin/faults/clear**

Sets active \= false on every active Fault. Idempotent. Returns {"status": "cleared"}.

## **7.12 GET /admin/audit?limit=**

limit clamped to \[1, 1000\], default 200\. Sorted id-desc. This is the ground-truth audit log of everything the simulator has done.

| \[ |
| :---- |
|   { |
|     "id": 142, |
|     "wall\_time": "2026-01-01T00:15:00.512345+00:00", |
|     "sim\_time": "2026-01-01T00:14:00+00:00", |
|     "tick": 0, |
|     "action": "allocation.created", |
|     "entity\_type": "allocation", |
|     "entity\_id": "1", |
|     "result": "OK", |
|     "metadata\_json": { "quantity": 3000, "fuel\_type": "DIESEL" } |
|   } |
| \] |

Useful action values: simulation.tick, allocation.created, allocation.departed, allocation.arrived, allocation.cancelled, event.created, event.started, event.resolved, supply.arrived, admin.run, admin.pause, fault.created, fault.clear\_all.

## **7.13 GET /admin/faults and GET /admin/events**

Last 50 rows each, id-desc. Useful for visualizing the fault/event timeline.

# **8\. The simulated world**

The active scenario is baked into the image. The shipped image runs baseline.yaml (no preloaded events). All scenarios share the same fixed world below; they differ only in seed and preloaded events.

## **8.1 Regions (2)**

| id | name | demand\_factor |
| :---- | :---- | :---- |
| region-dhaka | Dhaka Division | 1.00 |
| region-chattogram | Chattogram Division | 1.08 |

## **8.2 Depots (2)**

| id | region | dispatch / tick | capacity (D/P/O) | initial inventory (D/P/O) |
| :---- | :---- | :---- | :---- | :---- |
| depot-gazipur | region-dhaka | 12,000 | 90,000 / 70,000 / 45,000 | 60,000 / 45,000 / 26,000 |
| depot-patiya | region-chattogram | 11,000 | 85,000 / 65,000 / 40,000 | 55,000 / 42,000 / 24,000 |

## **8.3 Stations (4)**

| id | region | demand\_profile | capacity (D/P/O) | initial inventory (D/P/O) |
| :---- | :---- | :---- | :---- | :---- |
| station-mirpur | region-dhaka | urban\_high | 15,000 / 14,000 / 9,000 | 9,000 / 9,000 / 5,000 |
| station-tongi | region-dhaka | industrial | 18,000 / 9,000 / 6,000 | 11,000 / 6,000 / 3,500 |
| station-karnaphuli | region-chattogram | highway | 14,000 / 15,000 / 9,000 | 8,500 / 9,500 / 5,200 |
| station-coxsbazar | region-chattogram | regional | 12,000 / 12,000 / 7,000 | 7,500 / 7,500 / 4,200 |

## **8.4 Routes (6)**

| id | depot | station | transit\_ticks | max\_shipment |
| :---- | :---- | :---- | :---- | :---- |
| route-gazipur-mirpur | depot-gazipur | station-mirpur | 2 | 7,000 |
| route-gazipur-tongi | depot-gazipur | station-tongi | 2 | 6,500 |
| route-patiya-karnaphuli | depot-patiya | station-karnaphuli | 2 | 7,000 |
| route-patiya-coxsbazar | depot-patiya | station-coxsbazar | 3 | 6,000 |
| route-gazipur-karnaphuli | depot-gazipur | station-karnaphuli | 4 | 5,000 |
| route-patiya-mirpur | depot-patiya | station-mirpur | 4 | 5,000 |

## **8.5 Demand profiles (liters per simulated day)**

| profile | DIESEL | PETROL | OCTANE | noise |
| :---- | :---- | :---- | :---- | :---- |
| urban\_high | 8,500 | 10,500 | 5,600 | 0.10 |
| industrial | 14,000 | 4,500 | 2,200 | 0.08 |
| highway | 10,500 | 11,000 | 6,200 | 0.12 |
| regional | 7,200 | 7,600 | 3,600 | 0.10 |

## **8.6 Hour-of-day factors**

| profile | busy hours (factor) | off-peak hours (factor) |
| :---- | :---- | :---- |
| industrial | 06:00–17:59 → 1.55 | 18:00–05:59 → 0.45 |
| highway | 06–09 or 16–20 → 1.35 | else → 0.75 |
| urban\_high | 07–09 or 16–20 → 1.45 | else → 0.70 |
| regional | 07:00–20:59 → 1.25 | 21:00–06:59 → 0.65 |

## **8.7 Supply arrival pattern**

All scenarios share the same 22-arrival schedule:

* 4 "initial burst" arrivals at ticks 12–20 to cover day 1\.

* 18 "recurring resupply" arrivals spaced 64 ticks apart (\~16 simulated hours), top-up quantities sized to roughly one day's regional demand.

# **9\. Status-code cheat sheet**

All /v1/allocations errors come back as {"detail": {"code": "\<UPPER\_SNAKE\>", "message": "..."}}. Injected faults return {"error": {"code": "FAULT\_INJECTED", "message": "..."}} (note the difference: error vs detail). Pydantic validation errors use FastAPI's default {"detail":\[...\]}.

| HTTP | Code | Trigger | Recommended handling |
| :---- | :---- | :---- | :---- |
| 200 | — | Idempotent replay of an existing allocation | Continue normally. |
| 201 | — | New allocation accepted | Cache the returned id. |
| 404 | NOT\_FOUND | Depot, station, or route id unknown | Check your ids against /v1/depots / /v1/stations / /v1/routes. |
| 404 | ALLOCATION\_NOT\_FOUND | Cancel on unknown allocation id | Check /v1/allocations for the current id. |
| 409 | IDEMPOTENCY\_KEY\_MISMATCH | Same idempotency\_key, different body | Use a new key for the new request; the first wins. |
| 409 | ROUTE\_MISMATCH | Route connects different endpoints than your request | Use a route that matches your (depot, station) pair. |
| 409 | DEPOT\_CLOSED | Depot status not in {OPEN, CONSTRAINED} | Wait for depot\_constraint event to resolve. |
| 409 | STATION\_CLOSED | Station status ≠ OPEN | Wait for station\_outage event to resolve. |
| 409 | ROUTE\_DISRUPTED | Route status ≠ AVAILABLE | Wait for route\_disruption event to resolve, or pick another route. |
| 409 | ROUTE\_CAPACITY\_EXCEEDED | quantity \> route.max\_shipment | Split into multiple smaller allocations. |
| 409 | INSUFFICIENT\_INVENTORY | Depot doesn't have the fuel | Re-fetch depot inventory; wait for next supply arrival. |
| 409 | DISPATCH\_CAPACITY\_EXCEEDED | Sum of this depot's in-flight \+ pending on this tick \> dispatch\_capacity\_per\_tick | Wait for the next tick (PENDING allocations free up). |
| 409 | DESTINATION\_CAPACITY\_EXCEEDED | station.inventory\[fuel\] \+ quantity \> station.capacity\[fuel\] | Wait for station demand to consume fuel. |
| 409 | CANNOT\_CANCEL | Cancel on non-PENDING allocation | The shipment is already in motion; you cannot recover it. |
| 422 | (Pydantic) | Bad enum, missing field, duration\_ticks=0, quantity ≤ 0, etc. | Validate your payload client-side first. |
| 503 | FAULT\_INJECTED | Active unavailable / error\_rate / stream\_disconnect fault | Implement backoff \+ retry \+ degraded mode. See §10. |

# **10\. Defensive client checklist**

Every robust participant client should handle all of these gracefully:

| GET /v1/health        — liveness check (bypasses faults) |
| :---- |
| GET /v1/instance      — current tick, sim\_time, status, seed |
| GET /v1/depots        — capacity, inventory, dispatch\_capacity\_per\_tick |
| GET /v1/stations      — inventory, demand\_multiplier, capacity |
| GET /v1/routes        — availability, max\_shipment, transit\_ticks |
| GET /v1/supply-arrivals — upcoming deliveries |
| GET /v1/events        — past/active/scheduled crisis events |
| GET /v1/allocations   — your shipment ledger |
| GET /v1/demand-history — time-series for forecasting (limit 1-2000) |
| GET /v1/metrics       — service\_level \+ allocation\_failures |
| POST /v1/allocations  — submit shipment, returns 201/200/404/409/503 |
| POST /v1/allocations/{id}/cancel — refunds PENDING only |
| GET  /v1/stream       — SSE feed (queue size 200; 15s keepalive) |
|  |

**SSE rules:**

1. Treat SSE as advisory — re-GET REST state after every interesting event.

2. Watch X-Simulator-Stale: true on /v1/\* GETs and invalidate any local cache.

3. If a stream\_disconnect fault is active, GET /v1/stream returns 503 — back off and retry.

4. If your queue falls \>200 events behind, you will be silently dropped. Reconnect and refetch state.

5. **There is no Last-Event-ID replay.** On reconnect, you only get events from that moment forward.

6. A 15-second silence is normal (keepalive); do not treat it as a disconnect.

[image1]: <data:image/png;base64,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>

[image2]: <data:image/png;base64,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>

[image3]: <data:image/png;base64,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>
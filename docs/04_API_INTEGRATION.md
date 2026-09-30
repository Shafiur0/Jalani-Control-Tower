# API Integration Strategy

> **Project:** Jalani Control Tower  
> **Document:** Simulator API Client Design & Integration Patterns  
> **Version:** 1.0.0  

---

## 1. API Endpoint Map

### 1.1 Public API (`/v1/*`) — Subject to Fault Injection

| Method | Endpoint | Purpose | Poll Frequency | Critical? |
|--------|----------|---------|---------------|-----------|
| GET | `/v1/instance` | Sim state (tick, time, status, speed) | Every tick | ✅ Yes |
| GET | `/v1/depots` | All depots: inventory, capacity, rates | Every tick | ✅ Yes |
| GET | `/v1/depots/{id}` | Single depot detail | On demand | — |
| GET | `/v1/stations` | All stations: inventory, demand | Every tick | ✅ Yes |
| GET | `/v1/stations/{id}` | Single station detail | On demand | — |
| GET | `/v1/routes` | All routes: status, travel_time, capacity | Every tick | ✅ Yes |
| GET | `/v1/supply-arrivals` | Upcoming supply deliveries schedule | Every 10 ticks | ✅ Yes |
| GET | `/v1/demand-history` | Historical demand time-series | Every tick | ✅ Yes |
| GET | `/v1/events` | Active & scheduled crisis events | Every tick | ✅ Yes |
| GET | `/v1/allocations` | All allocations (filterable by status) | Every tick | ✅ Yes |
| GET | `/v1/allocations/{id}` | Single allocation detail | On demand | — |
| POST | `/v1/allocations` | Create fuel dispatch | On decision | ✅ Yes |
| POST | `/v1/allocations/{id}/cancel` | Cancel pending allocation | On need | ✅ Yes |
| GET | `/v1/metrics` | Service level, failure counts | Every 5 ticks | ✅ Yes |
| GET | `/v1/health` | Liveness check (**bypasses faults**) | Every 5s | ✅ Yes |
| GET | `/v1/stream` | SSE event stream | Continuous | 🟡 Advisory |

### 1.2 Admin API (`/admin/*`) — Not Fault-Injected

| Method | Endpoint | Purpose | When to Use |
|--------|----------|---------|-------------|
| POST | `/admin/start` | Start/resume simulation | Once at initialization |
| POST | `/admin/pause` | Pause simulation | Testing, debugging |
| POST | `/admin/reset` | Reset simulation state | New scenario |
| POST | `/admin/speed` | Set simulation speed | Performance tuning |
| GET | `/admin/scenarios` | List available scenarios | Scenario selection |
| POST | `/admin/scenarios/{id}/load` | Load specific scenario | Scenario setup |
| POST | `/admin/faults/inject` | Inject specific fault | Testing resilience |
| POST | `/admin/faults/clear` | Clear all faults | Recovery testing |
| GET | `/admin/debug/state` | Complete internal state dump | Debugging only |

---

## 2. Simulator Client Design

### 2.1 Base HTTP Client

```python
import httpx
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class SimulatorClient:
    """
    Defensive HTTP client for BUP Fuel Supply Simulator.
    Implements: retry, timeout, circuit breaking, validation.
    """
    
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=3.0),
            headers={"Accept": "application/json"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError))
    )
    async def get(self, endpoint: str, params: dict = None) -> dict:
        """GET with retry, circuit breaking, and validation."""
        if self.circuit_breaker.is_open:
            raise CircuitOpenError(f"Circuit breaker open for {endpoint}")
        
        try:
            response = await self.client.get(endpoint, params=params)
            
            if response.status_code == 200:
                self.circuit_breaker.record_success()
                return response.json()
            elif response.status_code == 404:
                return None
            elif response.status_code == 503:
                self.circuit_breaker.record_failure()
                raise SimulatorUnavailableError(f"Simulator unavailable: {endpoint}")
            else:
                self.circuit_breaker.record_failure()
                raise SimulatorError(f"Unexpected status {response.status_code}: {endpoint}")
        
        except httpx.TimeoutException:
            self.circuit_breaker.record_failure()
            raise
    
    async def post_allocation(self, allocation: dict, idempotency_key: str) -> dict:
        """
        POST /v1/allocations with idempotency key (Trap 5 defense).
        Does NOT retry with a different key — uses same key for retry safety.
        """
        headers = {"Idempotency-Key": idempotency_key}
        
        response = await self.client.post(
            "/v1/allocations",
            json=allocation,
            headers=headers
        )
        
        if response.status_code == 201:
            return response.json()
        elif response.status_code == 409:
            # Conflict: dispatch rate exceeded (Trap 6) or duplicate key
            error = response.json()
            raise AllocationConflictError(error.get('detail', 'Conflict'))
        elif response.status_code == 422:
            # Validation error: bad request
            error = response.json()
            raise AllocationValidationError(error.get('detail', 'Validation failed'))
        else:
            raise SimulatorError(f"Allocation failed: {response.status_code}")
    
    async def cancel_allocation(self, allocation_id: str) -> bool:
        """Cancel a pending allocation (Trap 3 defense)."""
        response = await self.client.post(f"/v1/allocations/{allocation_id}/cancel")
        return response.status_code == 200
    
    async def health_check(self) -> bool:
        """
        Liveness check. /v1/health BYPASSES fault injection.
        Use this to determine if simulator is truly down vs. fault-injected.
        """
        try:
            response = await self.client.get("/v1/health", timeout=2.0)
            return response.status_code == 200
        except Exception:
            return False
```

### 2.2 SSE Stream Consumer

```python
import aiohttp

class SSEListener:
    """
    Consumes /v1/stream for real-time notifications.
    SSE is ADVISORY ONLY — always validate with REST poll.
    """
    
    def __init__(self, base_url: str, event_handler: callable):
        self.stream_url = f"{base_url}/v1/stream"
        self.event_handler = event_handler
        self.connected = False
        self.reconnect_delay = 1.0  # Start at 1s, max 30s
    
    async def listen(self):
        """Main listener loop with auto-reconnect."""
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.stream_url) as response:
                        self.connected = True
                        self.reconnect_delay = 1.0  # Reset on successful connect
                        
                        async for line in response.content:
                            decoded = line.decode('utf-8').strip()
                            if decoded.startswith('data:'):
                                event_data = json.loads(decoded[5:])
                                # Handle event but DO NOT trust it blindly
                                await self.event_handler(event_data)
            
            except (aiohttp.ClientError, ConnectionError) as e:
                self.connected = False
                logger.warning(f"SSE disconnected (Trap 7): {e}")
                logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)
    
    async def on_event(self, event_data):
        """
        RULE: SSE events are signals, not state.
        Always re-GET the affected resource after receiving an event.
        """
        event_type = event_data.get('type')
        
        if event_type == 'tick':
            # Trigger state refresh cycle
            await self.state_manager.refresh()
        
        elif event_type == 'supply_arrived':
            # Re-GET depot inventory (verify supply was accepted)
            await self.state_manager.refresh_depots()
        
        elif event_type == 'road_closure':
            # URGENT: Check in-transit shipments (Trap 3)
            await self.state_manager.refresh_routes()
            await self.shipment_guard.check_in_transit_safety()
        
        elif event_type == 'demand_change':
            # Re-GET station demand multipliers (Trap 10)
            await self.state_manager.refresh_stations()
            await self.intelligence.trigger_reforecast()
```

---

## 3. Request/Response Contracts

### 3.1 POST /v1/allocations — Create Dispatch

**Request:**
```json
{
  "depot_id": "depot-gazipur",
  "station_id": "station-mirpur",
  "fuel_type": "diesel",
  "amount": 5000,
  "route_id": "route-dhaka-01"
}
```

**Headers:**
```
Idempotency-Key: a1b2c3d4e5f6...
Content-Type: application/json
```

**Success Response (201):**
```json
{
  "id": "alloc-uuid-1234",
  "status": "pending",
  "depot_id": "depot-gazipur",
  "station_id": "station-mirpur",
  "fuel_type": "diesel",
  "amount": 5000,
  "route_id": "route-dhaka-01",
  "created_at_tick": 145,
  "estimated_arrival_tick": 147,
  "idempotency_key": "a1b2c3d4e5f6..."
}
```

**Error Responses:**

| Status | Meaning | Trap |
|--------|---------|------|
| 409 | Dispatch rate exceeded or duplicate key | Trap 5, 6 |
| 422 | Invalid input (bad depot/station/route/fuel) | — |
| 503 | Simulator unavailable | Fault injection |

### 3.2 Allocation Lifecycle

```
POST /v1/allocations  →  status: "pending"
                            │
                     [Fuel deducted from depot]
                            │
                     ┌──────┴──────┐
                     │  departure  │  (fuel leaves depot)
                     │  tick       │
                     └──────┬──────┘
                            │
                     status: "in_transit"
                            │
                     ┌──────┴──────┐
                     │  arrival    │  (fuel arrives at station)
                     │  tick       │
                     └──────┬──────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
       status: "delivered"         [If station full]
       [fuel added to station]      excess is DISCARDED
                                   (Trap 2)
```

---

## 4. Error Handling Matrix

| Error Scenario | HTTP Code | Detection | Response |
|---------------|-----------|-----------|----------|
| Simulator down | Connection error | `httpx.ConnectError` | Retry 3x → circuit open → degraded mode |
| Timeout | — | `httpx.TimeoutException` | Retry 3x → use cached state |
| Fault: latency | 200 (slow) | Response time > 3s | Log, proceed with data |
| Fault: unavailable | 503 | Status code | Retry 3x → circuit open |
| Fault: error_rate | 500 | Status code | Retry 3x → skip this tick |
| Fault: stale_data | 200 (stale) | Tick mismatch | Re-read, use fresher data |
| Fault: stream_disconnect | Connection drop | SSE disconnect | Auto-reconnect, rely on polling |
| Rate exceeded | 409 | Status code | Queue for next tick |
| Invalid allocation | 422 | Status code | Log error, do not retry |
| Unknown error | Other | Status code | Log, alert operator |

---

## 5. Polling Schedule

### 5.1 Tick-Aligned Polling Loop

```python
class PollingOrchestrator:
    """
    Synchronizes data fetching with simulator ticks.
    Runs the observe→decide→act loop every tick.
    """
    
    async def run(self):
        last_processed_tick = -1
        
        while True:
            # Get current tick
            instance = await self.sim_client.get("/v1/instance")
            current_tick = instance['tick']
            
            if current_tick == last_processed_tick:
                await asyncio.sleep(0.05)  # Wait for next tick
                continue
            
            # New tick detected — run pipeline
            last_processed_tick = current_tick
            
            # Phase 1: OBSERVE (parallel fetch)
            state = await self.observe(current_tick)
            
            # Phase 2: DETECT anomalies
            anomalies = await self.detect(state)
            
            # Phase 3: PREDICT future state
            forecasts = await self.predict(state)
            
            # Phase 4: DECIDE allocations
            decisions = await self.decide(state, forecasts, anomalies)
            
            # Phase 5: ACT on decisions
            results = await self.act(decisions)
            
            # Phase 6: RECORD everything
            await self.record(decisions, results)
    
    async def observe(self, tick):
        """Fetch all data in parallel for maximum speed."""
        results = await asyncio.gather(
            self.sim_client.get("/v1/depots"),
            self.sim_client.get("/v1/stations"),
            self.sim_client.get("/v1/routes"),
            self.sim_client.get("/v1/events"),
            self.sim_client.get("/v1/allocations", {"status": "in_transit"}),
            self.sim_client.get("/v1/demand-history"),
            self.sim_client.get("/v1/supply-arrivals"),
            return_exceptions=True  # Don't fail if one endpoint is down
        )
        
        # Build state from successful responses (skip failures)
        return self.state_manager.update(results, tick)
```

### 5.2 Poll Frequency Summary

| Endpoint | Frequency | Batch? | Notes |
|----------|-----------|--------|-------|
| `/v1/instance` | Every 50ms wall-clock | No | Tick detection |
| Core state (depots/stations/routes) | Every tick | Yes (parallel) | Primary data |
| `/v1/events` | Every tick | Yes | Crisis detection |
| `/v1/supply-arrivals` | Every 10 ticks | No | Supply planning |
| `/v1/metrics` | Every 5 ticks | No | Service level |
| `/v1/health` | Every 5s wall-clock | No | Liveness |

---

## 6. Integration Testing Checklist

- [ ] POST allocation with valid data → 201
- [ ] POST allocation with invalid depot → 422
- [ ] POST allocation exceeding dispatch rate → 409
- [ ] POST allocation with duplicate idempotency key → returns original
- [ ] POST allocation on closed route → 422/409
- [ ] POST cancel on pending allocation → 200
- [ ] POST cancel on in-transit allocation → verify behavior
- [ ] GET all endpoints during fault injection → verify retry behavior
- [ ] SSE stream disconnect → verify auto-reconnect
- [ ] Full depot + supply arrival → verify waste detection
- [ ] Full station + delivery → verify overflow detection
- [ ] Route closure during transit → verify cancellation

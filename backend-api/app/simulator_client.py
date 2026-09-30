"""
Jalani Control Tower — Simulator HTTP Client
Defensive client for the BUP Fuel Supply Simulator /v1/* endpoints.
Implements: retry with backoff, circuit breaker, timeout, validation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models import (
    AllocationRequest,
    AllocationState,
    CrisisEvent,
    DemandPoint,
    DepotState,
    FuelInventory,
    RouteState,
    SimulatorInstance,
    StationState,
    SupplyArrival,
)

logger = logging.getLogger("jalani.simulator_client")


# ──────────────────────────────────────────────
# Circuit Breaker
# ──────────────────────────────────────────────

class CircuitBreaker:
    """Prevents cascading failures by short-circuiting calls to failing services."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 3,
    ):
        self.state = self.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.last_failure_time: float = 0.0

    @property
    def is_open(self) -> bool:
        if self.state == self.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = self.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit breaker → HALF_OPEN (testing recovery)")
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
                logger.info("Circuit breaker → CLOSED (recovered)")
        elif self.state == self.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold and self.state != self.OPEN:
            self.state = self.OPEN
            self.success_count = 0
            logger.warning(
                f"Circuit breaker → OPEN (failures={self.failure_count})"
            )


class CircuitOpenError(Exception):
    pass


class SimulatorError(Exception):
    pass


class AllocationConflictError(SimulatorError):
    pass


class AllocationValidationError(SimulatorError):
    pass


# ──────────────────────────────────────────────
# Simulator Client
# ──────────────────────────────────────────────

class SimulatorClient:
    """
    Defensive HTTP client for the BUP Fuel Supply Simulator.
    All /v1/* calls go through retry + circuit breaker logic.
    /v1/health bypasses fault injection and is used for liveness.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.simulator_url).rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(settings.request_timeout_s, connect=3.0),
            headers={"Accept": "application/json"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_s,
        )
        self._request_count = 0
        self._error_count = 0

    # ── Raw HTTP helpers ──────────────────────

    async def _get(self, endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
        """GET with circuit breaker check. Raises on circuit open."""
        if self.circuit_breaker.is_open:
            raise CircuitOpenError(f"Circuit open — skipping {endpoint}")

        self._request_count += 1
        try:
            response = await self.client.get(endpoint, params=params)

            if response.status_code == 200:
                self.circuit_breaker.record_success()
                return response.json()
            elif response.status_code == 404:
                self.circuit_breaker.record_success()
                return None
            elif response.status_code == 503:
                self.circuit_breaker.record_failure()
                self._error_count += 1
                raise SimulatorError(f"Simulator 503: {endpoint}")
            else:
                self.circuit_breaker.record_failure()
                self._error_count += 1
                raise SimulatorError(
                    f"Unexpected {response.status_code}: {endpoint}"
                )
        except httpx.TimeoutException:
            self.circuit_breaker.record_failure()
            self._error_count += 1
            raise
        except httpx.ConnectError:
            self.circuit_breaker.record_failure()
            self._error_count += 1
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.ConnectError, SimulatorError)
        ),
        reraise=True,
    )
    async def _safe_get(self, endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
        """GET with retry + circuit breaker."""
        return await self._get(endpoint, params)

    # ── Public API Methods ────────────────────

    async def get_instance(self) -> SimulatorInstance:
        """GET /v1/instance — tick counter, sim_time, status."""
        data = await self._safe_get("/v1/instance")
        if data is None:
            return SimulatorInstance()
        return SimulatorInstance(
            tick=data.get("tick", 0),
            sim_time=data.get("sim_time"),
            status=data.get("status", "unknown"),
            speed=data.get("speed", 8),
            scenario=data.get("scenario", "default"),
        )

    async def get_depots(self) -> Dict[str, DepotState]:
        """GET /v1/depots — all depot states."""
        data = await self._safe_get("/v1/depots")
        if not data:
            return {}

        depots_list = data if isinstance(data, list) else data.get("depots", data.get("data", []))
        if isinstance(depots_list, dict):
            depots_list = list(depots_list.values()) if not any(k in depots_list for k in ("id",)) else [depots_list]

        result = {}
        for d in depots_list:
            if not isinstance(d, dict):
                continue
            inv = d.get("inventory", {})
            cap = d.get("capacity", {})
            depot = DepotState(
                id=d.get("id", ""),
                name=d.get("name", ""),
                region=d.get("region", ""),
                inventory=FuelInventory(
                    diesel=inv.get("DIESEL", inv.get("diesel", 0)),
                    petrol=inv.get("PETROL", inv.get("petrol", 0)),
                    octane=inv.get("OCTANE", inv.get("octane", 0)),
                ),
                capacity=FuelInventory(
                    diesel=cap.get("DIESEL", cap.get("diesel", 0)),
                    petrol=cap.get("PETROL", cap.get("petrol", 0)),
                    octane=cap.get("OCTANE", cap.get("octane", 0)),
                ),
                dispatch_rate=d.get("dispatch_capacity_per_tick", d.get("dispatch_rate", 0)),
                status=d.get("status", "active"),
            )
            result[depot.id] = depot
        return result

    async def get_stations(self) -> Dict[str, StationState]:
        """GET /v1/stations — all station states."""
        data = await self._safe_get("/v1/stations")
        if not data:
            return {}

        stations_list = data if isinstance(data, list) else data.get("stations", data.get("data", []))
        if isinstance(stations_list, dict):
            stations_list = list(stations_list.values()) if "id" not in stations_list else [stations_list]

        result = {}
        for s in stations_list:
            if not isinstance(s, dict):
                continue
            inv = s.get("inventory", {})
            cap = s.get("capacity", {})
            station = StationState(
                id=s.get("id", ""),
                name=s.get("name", ""),
                region=s.get("region", ""),
                profile=s.get("profile", s.get("demand_profile", "")),
                inventory=FuelInventory(
                    diesel=inv.get("DIESEL", inv.get("diesel", 0)),
                    petrol=inv.get("PETROL", inv.get("petrol", 0)),
                    octane=inv.get("OCTANE", inv.get("octane", 0)),
                ),
                capacity=FuelInventory(
                    diesel=cap.get("DIESEL", cap.get("diesel", 0)),
                    petrol=cap.get("PETROL", cap.get("petrol", 0)),
                    octane=cap.get("OCTANE", cap.get("octane", 0)),
                ),
                demand_multiplier=s.get("demand_multiplier", 1.0),
                status=s.get("status", "active"),
            )
            result[station.id] = station
        return result

    async def get_routes(self) -> Dict[str, RouteState]:
        """GET /v1/routes — all route states."""
        data = await self._safe_get("/v1/routes")
        if not data:
            return {}

        routes_list = data if isinstance(data, list) else data.get("routes", data.get("data", []))
        if isinstance(routes_list, dict):
            routes_list = list(routes_list.values()) if "id" not in routes_list else [routes_list]

        result = {}
        for r in routes_list:
            if not isinstance(r, dict):
                continue
            route = RouteState(
                id=r.get("id", ""),
                from_depot=r.get("source_depot_id", r.get("from_depot", r.get("from", r.get("depot_id", "")))),
                to_station=r.get("destination_station_id", r.get("to_station", r.get("to", r.get("station_id", "")))),
                travel_ticks=r.get("transit_ticks", r.get("travel_ticks", r.get("travel_time", 0))),
                max_load=r.get("max_shipment", r.get("max_load", r.get("capacity", 0))),
                status=r.get("status", "available").lower(),
                is_cross_region=r.get("is_cross_region", r.get("cross_region", False)),
            )
            result[route.id] = route
        return result

    async def get_supply_arrivals(self) -> List[SupplyArrival]:
        """GET /v1/supply-arrivals — upcoming supply schedule."""
        data = await self._safe_get("/v1/supply-arrivals")
        if not data:
            return []

        arrivals_list = data if isinstance(data, list) else data.get("arrivals", data.get("data", []))
        result = []
        for a in arrivals_list:
            if not isinstance(a, dict):
                continue
            result.append(SupplyArrival(
                id=a.get("id", ""),
                depot_id=a.get("depot_id", a.get("depot", "")),
                arrival_tick=a.get("arrival_tick", a.get("tick", 0)),
                diesel=a.get("diesel", a.get("amount", {}).get("diesel", 0) if isinstance(a.get("amount"), dict) else 0),
                petrol=a.get("petrol", a.get("amount", {}).get("petrol", 0) if isinstance(a.get("amount"), dict) else 0),
                octane=a.get("octane", a.get("amount", {}).get("octane", 0) if isinstance(a.get("amount"), dict) else 0),
                status=a.get("status", "scheduled"),
            ))
        return result

    async def get_events(self) -> List[CrisisEvent]:
        """GET /v1/events — active and scheduled crisis events."""
        data = await self._safe_get("/v1/events")
        if not data:
            return []

        events_list = data if isinstance(data, list) else data.get("events", data.get("data", []))
        result = []
        for e in events_list:
            if not isinstance(e, dict):
                continue
            result.append(CrisisEvent(
                id=str(e.get("id", "")),
                type=e.get("type", ""),
                target=e.get("target", e.get("target_id", "")),
                start_tick=e.get("start_tick", 0),
                end_tick=e.get("end_tick"),
                severity=e.get("severity", "medium"),
                parameters=e.get("parameters"),
                description=e.get("description", ""),
            ))
        return result

    async def get_allocations(
        self, status_filter: Optional[str] = None
    ) -> List[AllocationState]:
        """GET /v1/allocations — all allocations, optionally filtered by status."""
        params = {}
        if status_filter:
            params["status"] = status_filter
        data = await self._safe_get("/v1/allocations", params=params)
        if not data:
            return []

        allocs_list = data if isinstance(data, list) else data.get("allocations", data.get("data", []))
        result = []
        for a in allocs_list:
            if not isinstance(a, dict):
                continue
            result.append(AllocationState(
                id=str(a.get("id", "")),
                depot_id=a.get("source_depot_id", a.get("depot_id", "")),
                station_id=a.get("destination_station_id", a.get("station_id", "")),
                fuel_type=a.get("fuel_type", "").lower(),
                amount=a.get("quantity", a.get("amount", 0)),
                route_id=a.get("route_id", ""),
                status="delivered" if a.get("status", "pending").lower() == "arrived" else a.get("status", "pending").lower(),
                created_at_tick=a.get("created_at_tick", 0),
                estimated_arrival_tick=a.get("estimated_arrival_tick"),
                idempotency_key=a.get("idempotency_key"),
            ))
        return result

    async def get_demand_history(self) -> List[DemandPoint]:
        """GET /v1/demand-history — historical demand time-series."""
        data = await self._safe_get("/v1/demand-history")
        if not data:
            return []

        points_list = data if isinstance(data, list) else data.get("history", data.get("data", []))
        result = []
        for p in points_list:
            if not isinstance(p, dict):
                continue
            result.append(DemandPoint(
                tick=p.get("tick", 0),
                station_id=p.get("station_id", ""),
                fuel_type=p.get("fuel_type", ""),
                demand=p.get("demand", p.get("amount", 0)),
                hour=p.get("hour", 0),
            ))
        return result

    async def get_metrics(self) -> dict:
        """GET /v1/metrics — service level, failure counts."""
        data = await self._safe_get("/v1/metrics")
        return data or {}

    async def health_check(self) -> bool:
        """
        GET /v1/health — liveness probe.
        This endpoint BYPASSES fault injection.
        """
        try:
            response = await self.client.get("/v1/health", timeout=2.0)
            return response.status_code == 200
        except Exception:
            return False

    # ── Allocation Actions ────────────────────

    async def create_allocation(
        self, request: AllocationRequest, idempotency_key: str
    ) -> AllocationState:
        """
        POST /v1/allocations — create a fuel dispatch.
        Uses idempotency key for retry safety (Trap 5 defense).
        """
        if self.circuit_breaker.is_open:
            raise CircuitOpenError("Circuit open — cannot dispatch")

        headers = {"Idempotency-Key": idempotency_key}
        payload = {
            "idempotency_key": idempotency_key,
            "source_depot_id": request.depot_id,
            "destination_station_id": request.station_id,
            "fuel_type": request.fuel_type.upper(),
            "quantity": request.amount,
            "route_id": request.route_id,
        }

        self._request_count += 1
        try:
            response = await self.client.post(
                "/v1/allocations", json=payload, headers=headers
            )

            if response.status_code in (200, 201):
                self.circuit_breaker.record_success()
                data = response.json()
                return AllocationState(
                    id=str(data.get("id", "")),
                    depot_id=data.get("source_depot_id", data.get("depot_id", request.depot_id)),
                    station_id=data.get("destination_station_id", data.get("station_id", request.station_id)),
                    fuel_type=data.get("fuel_type", request.fuel_type).lower(),
                    amount=data.get("quantity", data.get("amount", request.amount)),
                    route_id=data.get("route_id", request.route_id),
                    status="delivered" if data.get("status", "pending").lower() == "arrived" else data.get("status", "pending").lower(),
                    created_at_tick=data.get("created_at_tick", 0),
                    estimated_arrival_tick=data.get("estimated_arrival_tick"),
                    idempotency_key=idempotency_key,
                )
            elif response.status_code == 409:
                self.circuit_breaker.record_success()
                detail = response.json().get("detail", "Conflict")
                raise AllocationConflictError(detail)
            elif response.status_code == 422:
                self.circuit_breaker.record_success()
                detail = response.json().get("detail", "Validation failed")
                raise AllocationValidationError(detail)
            else:
                self.circuit_breaker.record_failure()
                self._error_count += 1
                raise SimulatorError(
                    f"Allocation failed: {response.status_code}"
                )
        except (httpx.TimeoutException, httpx.ConnectError):
            self.circuit_breaker.record_failure()
            self._error_count += 1
            raise

    async def cancel_allocation(self, allocation_id: str) -> bool:
        """POST /v1/allocations/{id}/cancel — cancel pending allocation (Trap 3 defense)."""
        try:
            response = await self.client.post(
                f"/v1/allocations/{allocation_id}/cancel"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to cancel allocation {allocation_id}: {e}")
            return False

    # ── Admin API (not fault-injected) ────────

    async def admin_start(self) -> bool:
        """POST /admin/run — start simulation."""
        try:
            r = await self.client.post("/admin/run")
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Failed to start simulation: {e}")
            return False

    async def admin_pause(self) -> bool:
        """POST /admin/pause — pause simulation."""
        try:
            r = await self.client.post("/admin/pause")
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Failed to pause simulation: {e}")
            return False

    async def admin_set_speed(self, speed: int) -> bool:
        """POST /admin/speed — set simulation speed."""
        try:
            r = await self.client.post("/admin/speed", json={"speed": speed})
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Failed to set speed: {e}")
            return False

    # ── Metrics ───────────────────────────────

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def error_rate(self) -> float:
        if self._request_count == 0:
            return 0.0
        return self._error_count / self._request_count

    async def close(self):
        await self.client.aclose()

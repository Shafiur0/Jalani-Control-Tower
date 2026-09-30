"""
Jalani Control Tower — Shipment Guard
Pre-flight validation for every allocation before it is sent.
Defends against all 10 hidden traps identified in the analysis.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from app.models import (
    AllocationRequest,
    CrisisEvent,
    DepotState,
    RouteState,
    StationState,
    WorldState,
    tick_to_sim_hour,
)

logger = logging.getLogger("jalani.shipment_guard")


class ValidationResult:
    """Result of a pre-flight validation check."""

    def __init__(self):
        self.passed = True
        self.checks: List[dict] = []

    def add_pass(self, check_name: str, detail: str = ""):
        self.checks.append({"name": check_name, "passed": True, "detail": detail})

    def add_fail(self, check_name: str, detail: str):
        self.passed = False
        self.checks.append({"name": check_name, "passed": False, "detail": detail})
        logger.warning(f"Shipment guard BLOCKED: {check_name} — {detail}")

    def add_warn(self, check_name: str, detail: str):
        self.checks.append({"name": check_name, "passed": True, "detail": f"⚠️ {detail}"})
        logger.info(f"Shipment guard WARNING: {check_name} — {detail}")

    @property
    def summary(self) -> str:
        failed = [c for c in self.checks if not c["passed"]]
        if not failed:
            return "All checks passed"
        return "; ".join(f"{c['name']}: {c['detail']}" for c in failed)


# ──────────────────────────────────────────────
# Demand estimation helpers
# ──────────────────────────────────────────────

# Base daily demand by profile (liters/day)
DEMAND_PROFILES = {
    "urban_high": {"diesel": 8500, "petrol": 10500, "octane": 5600},
    "industrial": {"diesel": 14000, "petrol": 4500, "octane": 2200},
    "highway": {"diesel": 10500, "petrol": 11000, "octane": 6200},
    "regional": {"diesel": 7200, "petrol": 7600, "octane": 3600},
}

# Hourly demand factors by profile
HOURLY_FACTORS = {
    "industrial": lambda h: 1.55 if 6 <= h <= 17 else 0.45,
    "highway": lambda h: 1.35 if (6 <= h <= 9 or 16 <= h <= 20) else 0.75,
    "urban_high": lambda h: 1.45 if (7 <= h <= 9 or 16 <= h <= 20) else 0.70,
    "regional": lambda h: 1.25 if 7 <= h <= 20 else 0.65,
}


def estimate_hourly_demand(
    station: StationState, fuel_type: str, tick: int
) -> float:
    """Estimate demand per hour for a station/fuel at a given tick."""
    profile = station.profile or "regional"
    daily = DEMAND_PROFILES.get(profile, DEMAND_PROFILES["regional"]).get(fuel_type, 5000)
    hour = tick_to_sim_hour(tick)
    factor_fn = HOURLY_FACTORS.get(profile, lambda h: 1.0)
    hourly_factor = factor_fn(hour)
    return (daily / 24) * hourly_factor * station.demand_multiplier


def estimate_consumption_over_ticks(
    station: StationState, fuel_type: str, start_tick: int, num_ticks: int
) -> float:
    """Estimate total consumption over a range of ticks."""
    total = 0.0
    for t in range(num_ticks):
        tick = start_tick + t
        hourly = estimate_hourly_demand(station, fuel_type, tick)
        total += hourly * (15 / 60)  # Each tick = 15 simulated minutes
    return total


# ──────────────────────────────────────────────
# Shipment Guard
# ──────────────────────────────────────────────

class ShipmentGuard:
    """
    Pre-flight validation for fuel allocations.
    Every allocation must pass ALL checks before POST /v1/allocations.
    """

    def validate(
        self, request: AllocationRequest, state: WorldState
    ) -> ValidationResult:
        """
        Run all pre-flight checks on an allocation request.
        Returns ValidationResult with pass/fail and details.
        """
        result = ValidationResult()

        depot = state.depots.get(request.depot_id)
        station = state.stations.get(request.station_id)
        route = state.routes.get(request.route_id)

        # Check 0: Entities exist
        if not depot:
            result.add_fail("entity_exists", f"Depot {request.depot_id} not found")
            return result
        if not station:
            result.add_fail("entity_exists", f"Station {request.station_id} not found")
            return result
        if not route:
            result.add_fail("entity_exists", f"Route {request.route_id} not found")
            return result

        # Check 1: Route available (Trap 3 defense)
        self._check_route_available(result, route, state)

        # Check 2: Route connects depot to station
        self._check_route_connects(result, route, request)

        # Check 3: Depot has enough inventory
        self._check_depot_inventory(result, depot, request)

        # Check 4: Depot dispatch rate limit (Trap 6 defense)
        self._check_dispatch_rate(result, depot, request)

        # Check 5: Route capacity limit
        self._check_route_capacity(result, route, request)

        # Check 6: Station overflow prevention (Trap 2 defense)
        self._check_station_overflow(result, station, route, request, state)

        # Check 7: Amount is positive and meaningful
        self._check_amount_valid(result, request)

        # Check 8: No road closure scheduled within buffer window (Trap 3 defense)
        self._check_no_imminent_closure(result, route, state)

        return result

    # ── Individual Checks ─────────────────────

    def _check_route_available(
        self, result: ValidationResult, route: RouteState, state: WorldState
    ):
        """TRAP 3: Ensure route is currently available."""
        if route.status != "available":
            result.add_fail(
                "route_available",
                f"Route {route.id} status is '{route.status}' — shipment would be lost!"
            )
        else:
            result.add_pass("route_available")

    def _check_route_connects(
        self, result: ValidationResult, route: RouteState, request: AllocationRequest
    ):
        """Ensure route connects the specified depot to the specified station."""
        if route.from_depot != request.depot_id or route.to_station != request.station_id:
            result.add_fail(
                "route_connects",
                f"Route {route.id} goes from {route.from_depot} to {route.to_station}, "
                f"not from {request.depot_id} to {request.station_id}"
            )
        else:
            result.add_pass("route_connects")

    def _check_depot_inventory(
        self, result: ValidationResult, depot: DepotState, request: AllocationRequest
    ):
        """Ensure depot has enough fuel to dispatch."""
        available = depot.inventory.get(request.fuel_type)
        if request.amount > available:
            result.add_fail(
                "depot_inventory",
                f"Depot {depot.id} has {available:.0f}L {request.fuel_type}, "
                f"but {request.amount:.0f}L requested"
            )
        else:
            result.add_pass("depot_inventory", f"{available:.0f}L available")

    def _check_dispatch_rate(
        self, result: ValidationResult, depot: DepotState, request: AllocationRequest
    ):
        """TRAP 6: Ensure dispatch doesn't exceed per-tick rate limit."""
        remaining = depot.remaining_dispatch_budget()
        if request.amount > remaining:
            result.add_fail(
                "dispatch_rate",
                f"Depot {depot.id} has {remaining:.0f}L dispatch budget remaining this tick, "
                f"but {request.amount:.0f}L requested"
            )
        else:
            result.add_pass("dispatch_rate", f"{remaining:.0f}L budget remaining")

    def _check_route_capacity(
        self, result: ValidationResult, route: RouteState, request: AllocationRequest
    ):
        """Ensure shipment doesn't exceed route max_load."""
        if request.amount > route.max_load:
            result.add_fail(
                "route_capacity",
                f"Route {route.id} max load is {route.max_load:.0f}L, "
                f"but {request.amount:.0f}L requested"
            )
        else:
            result.add_pass("route_capacity")

    def _check_station_overflow(
        self,
        result: ValidationResult,
        station: StationState,
        route: RouteState,
        request: AllocationRequest,
        state: WorldState,
    ):
        """
        TRAP 2: Ensure station can absorb the delivery when it arrives.
        Projects station inventory at time of arrival (current - consumption during transit).
        """
        transit_ticks = route.travel_ticks
        current_inv = station.inventory.get(request.fuel_type)
        capacity = station.capacity.get(request.fuel_type)

        # Estimate consumption during transit
        consumption = estimate_consumption_over_ticks(
            station, request.fuel_type, state.tick, transit_ticks
        )

        projected_at_arrival = max(0, current_inv - consumption)
        projected_after_delivery = projected_at_arrival + request.amount
        overflow = projected_after_delivery - capacity

        if overflow > 100:  # Allow small rounding margin
            result.add_fail(
                "station_overflow",
                f"Station {station.id} projected overflow of {overflow:.0f}L {request.fuel_type}! "
                f"Current: {current_inv:.0f}L, Consumption during transit: {consumption:.0f}L, "
                f"Projected at arrival: {projected_at_arrival:.0f}L, + {request.amount:.0f}L delivery "
                f"= {projected_after_delivery:.0f}L vs capacity {capacity:.0f}L"
            )
        else:
            safe_amount = capacity - projected_at_arrival
            result.add_pass(
                "station_overflow",
                f"Safe to deliver. Headroom at arrival: {safe_amount:.0f}L"
            )

    def _check_amount_valid(
        self, result: ValidationResult, request: AllocationRequest
    ):
        """Ensure amount is positive and meaningful."""
        if request.amount <= 0:
            result.add_fail("amount_valid", "Amount must be positive")
        elif request.amount < 50:
            result.add_warn("amount_valid", f"Very small shipment: {request.amount:.0f}L")
        else:
            result.add_pass("amount_valid")

    def _check_no_imminent_closure(
        self,
        result: ValidationResult,
        route: RouteState,
        state: WorldState,
    ):
        """
        TRAP 3 (extended): Check if a road closure is scheduled within 2 ticks.
        If a closure starts at tick T and we dispatch at tick T, fuel is lost.
        """
        buffer_ticks = 2
        for event in state.active_events:
            if event.type in ("road_closure", "route_closure") and (
                event.target == route.id
                or route.id in str(event.target)
            ):
                # Check if closure starts within buffer window
                if (
                    event.start_tick <= state.tick + buffer_ticks
                    and (event.end_tick is None or event.end_tick > state.tick)
                ):
                    result.add_fail(
                        "imminent_closure",
                        f"Route {route.id} has closure event starting at tick {event.start_tick} "
                        f"(current tick: {state.tick}) — DO NOT DISPATCH!"
                    )
                    return

        result.add_pass("imminent_closure")

    # ── Utility ───────────────────────────────

    def calculate_safe_amount(
        self,
        depot: DepotState,
        station: StationState,
        route: RouteState,
        fuel_type: str,
        state: WorldState,
    ) -> float:
        """
        Calculate the maximum safe amount to dispatch, respecting all constraints.
        Returns 0 if dispatch is not possible.
        """
        if route.status != "available":
            return 0.0

        limits = [
            depot.inventory.get(fuel_type),  # Depot stock
            depot.remaining_dispatch_budget(),  # Dispatch rate
            route.max_load,  # Route capacity
        ]

        # Station headroom at arrival (Trap 2)
        transit_ticks = route.travel_ticks
        consumption = estimate_consumption_over_ticks(
            station, fuel_type, state.tick, transit_ticks
        )
        current_inv = station.inventory.get(fuel_type)
        capacity = station.capacity.get(fuel_type)
        projected_at_arrival = max(0, current_inv - consumption)
        headroom = capacity - projected_at_arrival
        limits.append(headroom)

        safe = min(limits)
        return max(0, safe)

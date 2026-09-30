"""
Jalani Control Tower — State Manager
Maintains a single source of truth WorldState by polling the simulator.
Handles data freshness validation and state reconciliation.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Optional

from app.models import WorldState
from app.simulator_client import SimulatorClient, CircuitOpenError

logger = logging.getLogger("jalani.state_manager")


class StateManager:
    """
    Orchestrates all simulator polling and maintains the canonical world state.
    All downstream consumers (optimizer, risk scorer, dashboard) read from here.
    """

    def __init__(self, sim_client: SimulatorClient):
        self.sim_client = sim_client
        self.current_state = WorldState()
        self.previous_state: Optional[WorldState] = None
        self._last_poll_time: float = 0.0
        self._poll_errors: int = 0
        self._total_polls: int = 0

    async def full_refresh(self) -> WorldState:
        """
        Fetch all data from the simulator in parallel.
        Returns the updated world state.
        Gracefully handles partial failures — uses whatever data succeeds.
        """
        self._total_polls += 1
        start_time = time.time()

        # Save previous state for drift detection
        self.previous_state = copy.deepcopy(self.current_state)

        try:
            # Phase 1: Get current tick first
            instance = await self.sim_client.get_instance()
            current_tick = instance.tick

            # Phase 2: Parallel fetch of all data
            results = await asyncio.gather(
                self.sim_client.get_depots(),
                self.sim_client.get_stations(),
                self.sim_client.get_routes(),
                self.sim_client.get_events(),
                self.sim_client.get_allocations("in_transit"),
                self.sim_client.get_supply_arrivals(),
                self.sim_client.get_metrics(),
                return_exceptions=True,
            )

            depots, stations, routes, events, allocations, supply_arrivals, metrics = results

            # Update state with successful results
            self.current_state.tick = current_tick
            self.current_state.sim_time = instance.sim_time
            self.current_state.status = instance.status
            self.current_state.speed = instance.speed

            if isinstance(depots, dict) and depots:
                self.current_state.depots = depots
                self.current_state.data_freshness["depots"] = current_tick

            if isinstance(stations, dict) and stations:
                self.current_state.stations = stations
                self.current_state.data_freshness["stations"] = current_tick

            if isinstance(routes, dict) and routes:
                self.current_state.routes = routes
                self.current_state.data_freshness["routes"] = current_tick

            if isinstance(events, list):
                self.current_state.active_events = events
                self.current_state.data_freshness["events"] = current_tick

            if isinstance(allocations, list):
                self.current_state.allocations = allocations
                self.current_state.data_freshness["allocations"] = current_tick

            if isinstance(supply_arrivals, list):
                self.current_state.supply_arrivals = supply_arrivals
                self.current_state.data_freshness["supply_arrivals"] = current_tick

            if isinstance(metrics, dict):
                self.current_state.service_level = metrics.get(
                    "service_level", metrics.get("service_level_pct", self.current_state.service_level)
                )

            self.current_state.last_updated_tick = current_tick
            self._last_poll_time = time.time()
            self._poll_errors = 0

            # Log any partial failures
            for i, name in enumerate(
                ["depots", "stations", "routes", "events", "allocations", "supply_arrivals", "metrics"]
            ):
                if isinstance(results[i], Exception):
                    logger.warning(f"Partial poll failure for {name}: {results[i]}")

            elapsed_ms = (time.time() - start_time) * 1000
            logger.debug(f"State refresh complete: tick={current_tick}, took={elapsed_ms:.0f}ms")

            return self.current_state

        except CircuitOpenError:
            self._poll_errors += 1
            logger.warning("State refresh skipped: circuit breaker open")
            return self.current_state

        except Exception as e:
            self._poll_errors += 1
            logger.error(f"State refresh failed: {e}")
            return self.current_state

    async def refresh_instance_only(self) -> int:
        """Quick poll of just the tick counter. Used for tick detection."""
        try:
            instance = await self.sim_client.get_instance()
            self.current_state.tick = instance.tick
            self.current_state.status = instance.status
            return instance.tick
        except Exception:
            return self.current_state.tick

    def get_state(self) -> WorldState:
        """Return the current world state."""
        return self.current_state

    def get_state_snapshot(self) -> dict:
        """Return a JSON-serializable snapshot for the dashboard."""
        state = self.current_state
        return {
            "tick": state.tick,
            "sim_time": state.sim_time,
            "status": state.status,
            "speed": state.speed,
            "service_level": state.service_level,
            "depots": {
                did: {
                    "id": d.id,
                    "name": d.name,
                    "region": d.region,
                    "inventory": d.inventory.model_dump(),
                    "capacity": d.capacity.model_dump(),
                    "dispatch_rate": d.dispatch_rate,
                    "headroom": {
                        ft: d.headroom(ft)
                        for ft in ["diesel", "petrol", "octane"]
                    },
                }
                for did, d in state.depots.items()
            },
            "stations": {
                sid: {
                    "id": s.id,
                    "name": s.name,
                    "region": s.region,
                    "profile": s.profile,
                    "inventory": s.inventory.model_dump(),
                    "capacity": s.capacity.model_dump(),
                    "demand_multiplier": s.demand_multiplier,
                    "fill_pct": {
                        ft: round(s.fill_pct(ft) * 100, 1)
                        for ft in ["diesel", "petrol", "octane"]
                    },
                }
                for sid, s in state.stations.items()
            },
            "routes": {
                rid: {
                    "id": r.id,
                    "from_depot": r.from_depot,
                    "to_station": r.to_station,
                    "travel_ticks": r.travel_ticks,
                    "max_load": r.max_load,
                    "status": r.status,
                    "is_cross_region": r.is_cross_region,
                }
                for rid, r in state.routes.items()
            },
            "allocations": [
                a.model_dump() for a in state.allocations
            ],
            "supply_arrivals": [
                sa.model_dump() for sa in state.supply_arrivals
            ],
            "active_events": [
                e.model_dump() for e in state.active_events
            ],
            "last_updated_tick": state.last_updated_tick,
        }

    def detect_state_drift(self) -> list:
        """Compare current state with previous to detect changes during outage."""
        if not self.previous_state:
            return []

        drifts = []
        prev = self.previous_state
        curr = self.current_state

        # Check station inventory changes
        for sid in curr.stations:
            if sid in prev.stations:
                for ft in ["diesel", "petrol", "octane"]:
                    prev_inv = prev.stations[sid].inventory.get(ft)
                    curr_inv = curr.stations[sid].inventory.get(ft)
                    diff = curr_inv - prev_inv
                    if abs(diff) > 100:
                        drifts.append({
                            "type": "inventory_change",
                            "entity": sid,
                            "fuel_type": ft,
                            "previous": prev_inv,
                            "current": curr_inv,
                            "change": diff,
                        })

        # Check route status changes
        for rid in curr.routes:
            if rid in prev.routes:
                if prev.routes[rid].status != curr.routes[rid].status:
                    drifts.append({
                        "type": "route_status_change",
                        "entity": rid,
                        "previous": prev.routes[rid].status,
                        "current": curr.routes[rid].status,
                    })

        return drifts

    @property
    def poll_stats(self) -> dict:
        return {
            "total_polls": self._total_polls,
            "consecutive_errors": self._poll_errors,
            "last_poll_time": self._last_poll_time,
            "current_tick": self.current_state.tick,
            "data_freshness": dict(self.current_state.data_freshness),
        }

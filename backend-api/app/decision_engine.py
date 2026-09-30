"""
Jalani Control Tower — Decision Engine
Orchestrates the observe → decide → act loop.
Uses intelligence service for forecasting and optimization,
falls back to greedy heuristic when intelligence is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import List, Optional

import httpx

from app.config import settings
from app.database import Database
from app.models import (
    AllocationDecision,
    AllocationRequest,
    ApprovalMode,
    DecisionStatus,
    DecisionType,
    OperatingMode,
    RiskLevel,
    RiskScore,
    WorldState,
    generate_idempotency_key,
    risk_level_from_score,
    tick_to_sim_hour,
)
from app.shipment_guard import ShipmentGuard, estimate_hourly_demand
from app.simulator_client import (
    AllocationConflictError,
    AllocationValidationError,
    CircuitOpenError,
    SimulatorClient,
    SimulatorError,
)
from app.state_manager import StateManager

logger = logging.getLogger("jalani.decision_engine")


class DecisionEngine:
    """
    Core decision-making loop:
    1. Compute risk scores for every station/fuel
    2. Request optimization from intelligence service (or use fallback)
    3. Validate all proposals through the Shipment Guard
    4. Auto-approve or queue for human approval
    5. Execute approved decisions
    """

    def __init__(
        self,
        sim_client: SimulatorClient,
        state_manager: StateManager,
        db: Database,
    ):
        self.sim_client = sim_client
        self.state_manager = state_manager
        self.db = db
        self.guard = ShipmentGuard()
        self.operating_mode = OperatingMode.NORMAL
        self._pending_decisions: List[AllocationDecision] = []
        self._risk_scores: dict = {}
        self._consecutive_failures = 0
        self._last_successful_tick = -1
        self._intelligence_available = True

    # ── Risk Scoring ──────────────────────────

    def compute_risk_scores(self, state: WorldState) -> dict:
        """Compute risk scores for every station/fuel combination."""
        scores = {}
        for sid, station in state.stations.items():
            for ft in ["diesel", "petrol", "octane"]:
                hourly = estimate_hourly_demand(station, ft, state.tick)
                hours_left = station.hours_until_empty(ft, hourly)

                # Component 1: Inventory urgency (0-0.4)
                if hours_left < 3:
                    urgency = 0.40
                elif hours_left < 6:
                    urgency = 0.30
                elif hours_left < 12:
                    urgency = 0.15
                else:
                    urgency = 0.0

                # Component 2: Supply availability (0-0.2)
                supply_risk = 0.0
                serving_routes = [
                    r for r in state.routes.values()
                    if r.to_station == sid and r.status == "available"
                ]
                if not serving_routes:
                    supply_risk = 0.20  # Completely isolated (Trap 9)
                else:
                    serving_depots = set(r.from_depot for r in serving_routes)
                    for did in serving_depots:
                        depot = state.depots.get(did)
                        if depot:
                            inv_pct = depot.inventory.get(ft) / max(depot.capacity.get(ft), 1)
                            if inv_pct < 0.1:
                                supply_risk = max(supply_risk, 0.20)
                            elif inv_pct < 0.3:
                                supply_risk = max(supply_risk, 0.10)

                # Component 3: Route status (0-0.2)
                all_routes = [r for r in state.routes.values() if r.to_station == sid]
                if all_routes and all(r.status != "available" for r in all_routes):
                    route_risk = 0.20
                elif any(r.status != "available" for r in all_routes):
                    route_risk = 0.10
                else:
                    route_risk = 0.0

                # Component 4: Demand multiplier spike (0-0.2)
                demand_risk = 0.0
                if station.demand_multiplier > 1.5:
                    demand_risk = 0.15
                elif station.demand_multiplier > 1.2:
                    demand_risk = 0.05

                score = min(1.0, urgency + supply_risk + route_risk + demand_risk)
                level = risk_level_from_score(score)

                key = f"{sid}:{ft}"
                scores[key] = RiskScore(
                    station_id=sid,
                    fuel_type=ft,
                    score=score,
                    level=level,
                    hours_until_empty=hours_left,
                    contributing_factors=[
                        f for f in [
                            f"urgency={urgency:.2f}" if urgency > 0 else None,
                            f"supply_risk={supply_risk:.2f}" if supply_risk > 0 else None,
                            f"route_risk={route_risk:.2f}" if route_risk > 0 else None,
                            f"demand_risk={demand_risk:.2f}" if demand_risk > 0 else None,
                        ] if f
                    ],
                )

        self._risk_scores = scores
        return scores

    # ── Decision Generation ───────────────────

    async def generate_decisions(self, state: WorldState) -> List[AllocationDecision]:
        """
        Generate allocation decisions based on risk scores.
        Tries intelligence service first, falls back to greedy heuristic.
        """
        decisions = []

        try:
            if self._intelligence_available and self.operating_mode == OperatingMode.NORMAL:
                decisions = await self._request_intelligence_optimization(state)
                if decisions:
                    return decisions
        except Exception as e:
            logger.warning(f"Intelligence service unavailable: {e}")
            self._intelligence_available = False

        # Fallback: greedy heuristic
        decisions = self._greedy_allocations(state)
        return decisions

    async def _request_intelligence_optimization(
        self, state: WorldState
    ) -> List[AllocationDecision]:
        """Request optimized allocations from the intelligence service."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                payload = {
                    "tick": state.tick,
                    "depots": {did: d.model_dump() for did, d in state.depots.items()},
                    "stations": {sid: s.model_dump() for sid, s in state.stations.items()},
                    "routes": {rid: r.model_dump() for rid, r in state.routes.items()},
                    "risk_scores": {k: v.model_dump() for k, v in self._risk_scores.items()},
                    "events": [e.model_dump() for e in state.active_events],
                    "supply_arrivals": [sa.model_dump() for sa in state.supply_arrivals],
                }
                response = await client.post(
                    f"{settings.intelligence_url}/optimize",
                    json=payload,
                )
                if response.status_code == 200:
                    self._intelligence_available = True
                    data = response.json()
                    allocations = data.get("allocations", [])
                    decisions = []
                    for alloc in allocations:
                        decisions.append(AllocationDecision(
                            id=str(uuid.uuid4())[:8],
                            tick=state.tick,
                            type=DecisionType.ALLOCATION,
                            request=AllocationRequest(**alloc),
                            risk_score_before=alloc.get("risk_before", 0),
                            risk_score_after=alloc.get("risk_after", 0),
                            hours_until_empty=alloc.get("hours_left", 0),
                            trigger_reason="optimizer",
                            explanation=alloc.get("explanation", ""),
                            confidence=alloc.get("confidence", 0.8),
                            idempotency_key=generate_idempotency_key(
                                alloc["depot_id"], alloc["station_id"],
                                alloc["fuel_type"], state.tick
                            ),
                        ))
                    return decisions
        except Exception as e:
            logger.warning(f"Intelligence optimization failed: {e}")
            self._intelligence_available = False
        return []

    def _greedy_allocations(self, state: WorldState) -> List[AllocationDecision]:
        """
        Fallback greedy heuristic: sort stations by urgency, dispatch to most urgent first.
        Used when intelligence service is unavailable.
        """
        decisions = []

        # Sort risk scores by urgency (highest first)
        sorted_risks = sorted(
            self._risk_scores.values(),
            key=lambda r: r.score,
            reverse=True,
        )

        for risk in sorted_risks:
            if risk.score < 0.2:
                continue  # Only dispatch if there's meaningful risk

            if risk.hours_until_empty > 12:
                continue  # Station is fine for now

            station = state.stations.get(risk.station_id)
            if not station:
                continue

            # Find best route to this station
            best_route = None
            best_depot = None
            for route in state.routes.values():
                if route.to_station != risk.station_id:
                    continue
                if route.status != "available":
                    continue
                depot = state.depots.get(route.from_depot)
                if not depot:
                    continue
                if depot.inventory.get(risk.fuel_type) < 500:
                    continue
                # Prefer shorter routes, intra-region over cross-region
                if best_route is None or (
                    route.travel_ticks < best_route.travel_ticks
                    or (route.travel_ticks == best_route.travel_ticks and not route.is_cross_region)
                ):
                    best_route = route
                    best_depot = depot

            if not best_route or not best_depot:
                continue

            # Calculate safe amount
            safe_amount = self.guard.calculate_safe_amount(
                best_depot, station, best_route, risk.fuel_type, state
            )

            if safe_amount < 100:
                continue

            # Cap at 30% of depot stock for fairness
            amount = min(
                safe_amount,
                best_depot.inventory.get(risk.fuel_type) * 0.3,
            )
            amount = round(amount, -1)  # Round to nearest 10

            if amount < 100:
                continue

            request = AllocationRequest(
                depot_id=best_depot.id,
                station_id=risk.station_id,
                fuel_type=risk.fuel_type,
                amount=amount,
                route_id=best_route.id,
            )

            decision = AllocationDecision(
                id=str(uuid.uuid4())[:8],
                tick=state.tick,
                type=DecisionType.ALLOCATION,
                request=request,
                risk_score_before=risk.score,
                hours_until_empty=risk.hours_until_empty,
                trigger_reason="fallback_greedy",
                explanation=f"Greedy: {station.id} {risk.fuel_type} at {risk.hours_until_empty:.1f}h remaining",
                confidence=0.6,
                idempotency_key=generate_idempotency_key(
                    best_depot.id, risk.station_id, risk.fuel_type, state.tick
                ),
            )
            decisions.append(decision)

        return decisions

    # ── Decision Validation & Execution ───────

    async def process_decisions(
        self, decisions: List[AllocationDecision], state: WorldState
    ) -> List[dict]:
        """Validate, classify, and execute decisions."""
        results = []

        for decision in decisions:
            # Step 1: Validate through shipment guard
            validation = self.guard.validate(decision.request, state)
            if not validation.passed:
                decision.status = DecisionStatus.REJECTED
                self.db.log_decision({
                    "decision_id": decision.id,
                    "tick": decision.tick,
                    "type": decision.type.value,
                    "depot_id": decision.request.depot_id,
                    "station_id": decision.request.station_id,
                    "fuel_type": decision.request.fuel_type,
                    "amount_liters": decision.request.amount,
                    "route_id": decision.request.route_id,
                    "status": "rejected",
                    "trigger_reason": decision.trigger_reason,
                    "explanation": f"Blocked by guard: {validation.summary}",
                })
                results.append({
                    "decision_id": decision.id,
                    "status": "rejected",
                    "reason": validation.summary,
                })
                continue

            # Step 2: Classify approval mode
            decision.approval_mode = self._classify_approval(decision, state)

            # Step 3: Auto-approve or queue for human
            if decision.approval_mode == ApprovalMode.AUTO:
                result = await self._execute_decision(decision)
                results.append(result)
            else:
                decision.status = DecisionStatus.PROPOSED
                self._pending_decisions.append(decision)
                self.db.log_decision({
                    "decision_id": decision.id,
                    "tick": decision.tick,
                    "type": decision.type.value,
                    "depot_id": decision.request.depot_id,
                    "station_id": decision.request.station_id,
                    "fuel_type": decision.request.fuel_type,
                    "amount_liters": decision.request.amount,
                    "route_id": decision.request.route_id,
                    "status": "proposed",
                    "approval_mode": "human",
                    "risk_score_before": decision.risk_score_before,
                    "hours_until_empty": decision.hours_until_empty,
                    "trigger_reason": decision.trigger_reason,
                    "explanation": decision.explanation,
                    "confidence": decision.confidence,
                    "idempotency_key": decision.idempotency_key,
                })
                results.append({
                    "decision_id": decision.id,
                    "status": "pending_approval",
                    "approval_mode": "human",
                })

        return results

    def _classify_approval(
        self, decision: AllocationDecision, state: WorldState
    ) -> ApprovalMode:
        """Determine if a decision needs human approval."""
        # In degraded mode, everything needs human review
        if self.operating_mode in (OperatingMode.DEGRADED, OperatingMode.FALLBACK):
            return ApprovalMode.HUMAN

        # Critical risk: human review
        if decision.risk_score_before > 0.8:
            return ApprovalMode.HUMAN

        # Large shipment: human review
        if decision.request.amount > 10000:
            return ApprovalMode.HUMAN

        # Cross-region: human review
        route = state.routes.get(decision.request.route_id)
        if route and route.is_cross_region:
            return ApprovalMode.HUMAN

        # Low confidence: human review
        if decision.confidence < 0.5:
            return ApprovalMode.HUMAN

        return ApprovalMode.AUTO

    async def _execute_decision(self, decision: AllocationDecision) -> dict:
        """Execute an approved decision by POSTing to the simulator."""
        try:
            allocation = await self.sim_client.create_allocation(
                decision.request, decision.idempotency_key
            )
            decision.status = DecisionStatus.EXECUTED
            decision.allocation_id = allocation.id

            self.db.log_decision({
                "decision_id": decision.id,
                "tick": decision.tick,
                "type": decision.type.value,
                "depot_id": decision.request.depot_id,
                "station_id": decision.request.station_id,
                "fuel_type": decision.request.fuel_type,
                "amount_liters": decision.request.amount,
                "route_id": decision.request.route_id,
                "status": "executed",
                "approval_mode": decision.approval_mode.value,
                "risk_score_before": decision.risk_score_before,
                "hours_until_empty": decision.hours_until_empty,
                "trigger_reason": decision.trigger_reason,
                "explanation": decision.explanation,
                "confidence": decision.confidence,
                "allocation_id": allocation.id,
                "idempotency_key": decision.idempotency_key,
                "expected_arrival": allocation.estimated_arrival_tick,
            })

            self._consecutive_failures = 0
            return {
                "decision_id": decision.id,
                "status": "executed",
                "allocation_id": allocation.id,
            }

        except AllocationConflictError as e:
            logger.warning(f"Allocation conflict: {e}")
            return {"decision_id": decision.id, "status": "conflict", "reason": str(e)}

        except AllocationValidationError as e:
            logger.warning(f"Allocation validation error: {e}")
            return {"decision_id": decision.id, "status": "validation_error", "reason": str(e)}

        except (SimulatorError, CircuitOpenError) as e:
            self._consecutive_failures += 1
            logger.error(f"Allocation execution failed: {e}")
            return {"decision_id": decision.id, "status": "failed", "reason": str(e)}

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"Unexpected error executing allocation: {e}")
            return {"decision_id": decision.id, "status": "error", "reason": str(e)}

    # ── Human Approval ────────────────────────

    async def approve_decision(self, decision_id: str, by: str = "operator") -> dict:
        """Approve a pending decision and execute it."""
        decision = None
        for d in self._pending_decisions:
            if d.id == decision_id:
                decision = d
                break

        if not decision:
            return {"error": f"Decision {decision_id} not found in pending queue"}

        # Re-validate against fresh state before executing
        state = self.state_manager.get_state()
        validation = self.guard.validate(decision.request, state)
        if not validation.passed:
            self._pending_decisions.remove(decision)
            self.db.update_decision_status(
                decision_id, "rejected",
                rejection_reason=f"Re-validation failed: {validation.summary}"
            )
            return {
                "decision_id": decision_id,
                "status": "rejected",
                "reason": f"State changed since proposal: {validation.summary}",
            }

        decision.approved_by = by
        self._pending_decisions.remove(decision)

        result = await self._execute_decision(decision)
        self.db.update_decision_status(
            decision_id, result["status"],
            approved_by=by,
            approved_at=str(time.time()),
        )
        return result

    def reject_decision(self, decision_id: str, reason: str = "", by: str = "operator") -> dict:
        """Reject a pending decision."""
        for d in self._pending_decisions:
            if d.id == decision_id:
                self._pending_decisions.remove(d)
                self.db.update_decision_status(
                    decision_id, "rejected",
                    rejection_reason=reason,
                    approved_by=by,
                )
                return {"decision_id": decision_id, "status": "rejected", "reason": reason}
        return {"error": f"Decision {decision_id} not found"}

    def get_pending_decisions(self) -> List[dict]:
        """Get all decisions awaiting human approval."""
        return [
            {
                "id": d.id,
                "tick": d.tick,
                "type": d.type.value,
                "depot_id": d.request.depot_id,
                "station_id": d.request.station_id,
                "fuel_type": d.request.fuel_type,
                "amount": d.request.amount,
                "route_id": d.request.route_id,
                "risk_score": d.risk_score_before,
                "hours_until_empty": d.hours_until_empty,
                "trigger_reason": d.trigger_reason,
                "explanation": d.explanation,
                "confidence": d.confidence,
                "created_at": d.created_at,
            }
            for d in self._pending_decisions
        ]

    # ── Mode Management ───────────────────────

    def update_mode(self, sim_healthy: bool, intel_healthy: bool):
        """Update operating mode based on health signals."""
        old_mode = self.operating_mode

        if sim_healthy and intel_healthy and self._consecutive_failures == 0:
            self.operating_mode = OperatingMode.NORMAL
        elif sim_healthy and not intel_healthy:
            self.operating_mode = OperatingMode.DEGRADED
        elif not sim_healthy and self._consecutive_failures < 10:
            self.operating_mode = OperatingMode.SAFE_HOLD
        else:
            self.operating_mode = OperatingMode.FALLBACK

        if old_mode != self.operating_mode:
            logger.warning(f"MODE TRANSITION: {old_mode.value} → {self.operating_mode.value}")

    @property
    def risk_scores(self) -> dict:
        return {k: v.model_dump() for k, v in self._risk_scores.items()}

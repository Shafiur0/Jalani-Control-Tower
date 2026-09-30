"""
Jalani Control Tower — Domain Models
Pydantic models representing the simulated fuel network state.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class FuelType(str, Enum):
    DIESEL = "diesel"
    PETROL = "petrol"
    OCTANE = "octane"


class RouteStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class AllocationStatus(str, Enum):
    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RiskLevel(str, Enum):
    OK = "ok"
    WATCH = "watch"
    HIGH = "high"
    CRITICAL = "critical"


class OperatingMode(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    SAFE_HOLD = "safe_hold"
    FALLBACK = "fallback"


class ApprovalMode(str, Enum):
    AUTO = "auto"
    HUMAN = "human"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class DecisionType(str, Enum):
    ALLOCATION = "allocation"
    CANCELLATION = "cancellation"
    RATIONING = "rationing"
    REBALANCE = "rebalance"


# ──────────────────────────────────────────────
# Simulator Data Models
# ──────────────────────────────────────────────

class FuelInventory(BaseModel):
    """Fuel levels for all three types."""
    diesel: float = 0.0
    petrol: float = 0.0
    octane: float = 0.0

    def get(self, fuel_type: str) -> float:
        return getattr(self, fuel_type, 0.0)

    def set(self, fuel_type: str, value: float):
        setattr(self, fuel_type, value)


class DepotState(BaseModel):
    """State of a fuel depot."""
    id: str
    name: str = ""
    region: str = ""
    inventory: FuelInventory = Field(default_factory=FuelInventory)
    capacity: FuelInventory = Field(default_factory=FuelInventory)
    dispatch_rate: float = 0.0
    dispatched_this_tick: float = 0.0
    status: str = "active"

    def headroom(self, fuel_type: str) -> float:
        """Available capacity for incoming supply (Trap 1 defense)."""
        return self.capacity.get(fuel_type) - self.inventory.get(fuel_type)

    def remaining_dispatch_budget(self) -> float:
        """How many more liters can be dispatched this tick (Trap 6 defense)."""
        return max(0, self.dispatch_rate - self.dispatched_this_tick)


class StationState(BaseModel):
    """State of a fuel station."""
    id: str
    name: str = ""
    region: str = ""
    profile: str = ""  # urban_high, industrial, highway, regional
    inventory: FuelInventory = Field(default_factory=FuelInventory)
    capacity: FuelInventory = Field(default_factory=FuelInventory)
    demand_multiplier: float = 1.0
    status: str = "active"

    def fill_pct(self, fuel_type: str) -> float:
        """Current fill percentage for a fuel type."""
        cap = self.capacity.get(fuel_type)
        if cap <= 0:
            return 0.0
        return self.inventory.get(fuel_type) / cap

    def headroom(self, fuel_type: str) -> float:
        """Remaining capacity at this station (Trap 2 defense)."""
        return self.capacity.get(fuel_type) - self.inventory.get(fuel_type)

    def hours_until_empty(self, fuel_type: str, hourly_demand: float) -> float:
        """Estimated hours until stockout for a fuel type."""
        if hourly_demand <= 0:
            return float("inf")
        return self.inventory.get(fuel_type) / hourly_demand


class RouteState(BaseModel):
    """State of a transport route."""
    id: str
    from_depot: str = ""
    to_station: str = ""
    travel_ticks: int = 0
    max_load: float = 0.0
    status: RouteStatus = RouteStatus.AVAILABLE
    is_cross_region: bool = False


class AllocationState(BaseModel):
    """State of a fuel allocation/shipment."""
    id: str
    depot_id: str = ""
    station_id: str = ""
    fuel_type: str = ""
    amount: float = 0.0
    route_id: str = ""
    status: AllocationStatus = AllocationStatus.PENDING
    created_at_tick: int = 0
    estimated_arrival_tick: Optional[int] = None
    idempotency_key: Optional[str] = None


class SupplyArrival(BaseModel):
    """Scheduled supply delivery to a depot."""
    id: str = ""
    depot_id: str = ""
    arrival_tick: int = 0
    diesel: float = 0.0
    petrol: float = 0.0
    octane: float = 0.0
    status: str = "scheduled"


class CrisisEvent(BaseModel):
    """Active or scheduled crisis event."""
    id: str = ""
    type: str = ""
    target: str = ""
    start_tick: int = 0
    end_tick: Optional[int] = None
    severity: str = "medium"
    parameters: Optional[dict] = None
    description: str = ""


class DemandPoint(BaseModel):
    """A single demand data point."""
    tick: int
    station_id: str = ""
    fuel_type: str = ""
    demand: float = 0.0
    hour: int = 0


class SimulatorInstance(BaseModel):
    """Simulator instance metadata."""
    tick: int = 0
    sim_time: Optional[str] = None
    status: str = "paused"
    speed: int = 8
    scenario: str = "default"


# ──────────────────────────────────────────────
# World State (Single Source of Truth)
# ──────────────────────────────────────────────

class WorldState(BaseModel):
    """Complete snapshot of the simulated world."""
    # Core state
    tick: int = 0
    sim_time: Optional[str] = None
    status: str = "paused"
    speed: int = 8

    # Network entities
    depots: Dict[str, DepotState] = Field(default_factory=dict)
    stations: Dict[str, StationState] = Field(default_factory=dict)
    routes: Dict[str, RouteState] = Field(default_factory=dict)

    # Dynamic data
    allocations: List[AllocationState] = Field(default_factory=list)
    supply_arrivals: List[SupplyArrival] = Field(default_factory=list)
    active_events: List[CrisisEvent] = Field(default_factory=list)

    # Metrics
    service_level: float = 100.0

    # Metadata
    last_updated_tick: int = -1
    data_freshness: Dict[str, int] = Field(default_factory=dict)

    def is_stale(self, endpoint: str, max_age_ticks: int = 2) -> bool:
        """Check if cached data for an endpoint is stale (Trap 4 defense)."""
        last_tick = self.data_freshness.get(endpoint, 0)
        return (self.tick - last_tick) > max_age_ticks


# ──────────────────────────────────────────────
# Decision Models
# ──────────────────────────────────────────────

class AllocationRequest(BaseModel):
    """Request to create a fuel allocation."""
    depot_id: str
    station_id: str
    fuel_type: str
    amount: float
    route_id: str


class AllocationDecision(BaseModel):
    """A proposed or executed allocation decision."""
    id: str = ""
    tick: int = 0
    type: DecisionType = DecisionType.ALLOCATION
    request: AllocationRequest
    risk_score_before: float = 0.0
    risk_score_after: float = 0.0
    hours_until_empty: float = 0.0
    trigger_reason: str = ""
    explanation: str = ""
    confidence: float = 0.0
    status: DecisionStatus = DecisionStatus.PROPOSED
    approval_mode: ApprovalMode = ApprovalMode.AUTO
    approved_by: Optional[str] = None
    idempotency_key: str = ""
    allocation_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RiskScore(BaseModel):
    """Risk assessment for a station/fuel combination."""
    station_id: str
    fuel_type: str
    score: float = 0.0
    level: RiskLevel = RiskLevel.OK
    hours_until_empty: float = float("inf")
    contributing_factors: List[str] = Field(default_factory=list)


class Alert(BaseModel):
    """System alert for operator attention."""
    id: str = ""
    severity: str = "info"  # info, warning, critical, emergency
    title: str = ""
    message: str = ""
    source: str = ""
    tick: int = 0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    acknowledged: bool = False


# ──────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────

def generate_idempotency_key(depot_id: str, station_id: str, fuel_type: str, tick: int) -> str:
    """
    Deterministic idempotency key from allocation parameters.
    Same inputs always produce the same key → safe retries (Trap 5 defense).
    """
    raw = f"{depot_id}:{station_id}:{fuel_type}:{tick}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def tick_to_sim_hour(tick: int, tick_minutes: int = 15) -> int:
    """Convert a tick number to the hour of the simulated day (0-23)."""
    total_minutes = tick * tick_minutes
    return (total_minutes // 60) % 24


def risk_level_from_score(score: float) -> RiskLevel:
    """Map a 0.0-1.0 risk score to a risk level."""
    if score >= 0.7:
        return RiskLevel.CRITICAL
    elif score >= 0.4:
        return RiskLevel.HIGH
    elif score >= 0.2:
        return RiskLevel.WATCH
    return RiskLevel.OK

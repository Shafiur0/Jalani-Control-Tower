"""
Jalani Control Tower — Intelligence Service
Demand forecasting, LP optimization, and anomaly detection.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from scipy.optimize import linprog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("jalani.intelligence")

app = FastAPI(title="Jalani Intelligence Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ──────────────────────────────────────────────
# Demand Profiles (from simulator spec)
# ──────────────────────────────────────────────

DEMAND_PROFILES = {
    "urban_high": {"diesel": 8500, "petrol": 10500, "octane": 5600},
    "industrial": {"diesel": 14000, "petrol": 4500, "octane": 2200},
    "highway":    {"diesel": 10500, "petrol": 11000, "octane": 6200},
    "regional":   {"diesel": 7200, "petrol": 7600, "octane": 3600},
}

HOURLY_FACTORS = {
    "industrial": lambda h: 1.55 if 6 <= h <= 17 else 0.45,
    "highway":    lambda h: 1.35 if (6 <= h <= 9 or 16 <= h <= 20) else 0.75,
    "urban_high": lambda h: 1.45 if (7 <= h <= 9 or 16 <= h <= 20) else 0.70,
    "regional":   lambda h: 1.25 if 7 <= h <= 20 else 0.65,
}

FUEL_TYPES = ["diesel", "petrol", "octane"]


def tick_to_hour(tick: int, tick_min: int = 15) -> int:
    return (tick * tick_min // 60) % 24


def estimate_demand_per_tick(profile: str, fuel_type: str, tick: int, multiplier: float = 1.0) -> float:
    daily = DEMAND_PROFILES.get(profile, DEMAND_PROFILES["regional"]).get(fuel_type, 5000)
    hour = tick_to_hour(tick)
    hf = HOURLY_FACTORS.get(profile, lambda h: 1.0)(hour)
    return (daily / 96) * hf * multiplier  # 96 ticks per day


# ──────────────────────────────────────────────
# Demand Forecaster
# ──────────────────────────────────────────────

class DemandForecaster:
    """EWMA + seasonal pattern forecaster."""

    def __init__(self, alpha: float = 0.3):
        self.alpha = alpha
        self.history: Dict[str, List[float]] = {}

    def update(self, key: str, value: float):
        if key not in self.history:
            self.history[key] = []
        self.history[key].append(value)
        if len(self.history[key]) > 960:  # Keep 10 days max
            self.history[key] = self.history[key][-960:]

    def forecast(self, station_id: str, profile: str, fuel_type: str,
                 tick: int, multiplier: float, horizon: int = 12) -> List[dict]:
        key = f"{station_id}:{fuel_type}"
        predictions = []
        for t in range(1, horizon + 1):
            future_tick = tick + t
            base = estimate_demand_per_tick(profile, fuel_type, future_tick, multiplier)

            # Adjust with EWMA if we have history
            hist = self.history.get(key, [])
            if len(hist) >= 4:
                ewma = hist[-1]
                for v in hist[-4:]:
                    ewma = self.alpha * v + (1 - self.alpha) * ewma
                ratio = ewma / max(base, 1)
                if 0.5 < ratio < 2.0:
                    base *= ratio

            predictions.append({
                "tick": future_tick,
                "predicted_demand": round(base, 1),
                "confidence": 0.85 if len(hist) > 48 else 0.6,
            })
        return predictions

    def get_wape(self, station_id: str, fuel_type: str) -> float:
        key = f"{station_id}:{fuel_type}"
        hist = self.history.get(key, [])
        if len(hist) < 10:
            return 0.0
        actuals = hist[-10:]
        mean_val = np.mean(actuals)
        if mean_val == 0:
            return 0.0
        errors = [abs(a - mean_val) for a in actuals]
        return float(np.sum(errors) / np.sum(np.abs(actuals)))


forecaster = DemandForecaster(alpha=float(os.environ.get("FORECAST_ALPHA", "0.3")))


# ──────────────────────────────────────────────
# LP Optimizer
# ──────────────────────────────────────────────

class LPOptimizer:
    """
    Linear programming optimizer using SciPy/HiGHS.
    Minimizes unmet demand while respecting all simulator constraints.
    """

    def optimize(self, state: dict) -> List[dict]:
        """
        Solve the allocation LP.
        Returns list of recommended allocations.
        """
        tick = state.get("tick", 0)
        depots = state.get("depots", {})
        stations = state.get("stations", {})
        routes = state.get("routes", {})
        risk_scores = state.get("risk_scores", {})

        # Build decision variables: one per (route, fuel_type) combination
        available_routes = {
            rid: r for rid, r in routes.items()
            if r.get("status", "available") == "available"
        }

        if not available_routes or not depots or not stations:
            return []

        # Index mapping
        var_keys = []  # (route_id, fuel_type)
        for rid, route in available_routes.items():
            for ft in FUEL_TYPES:
                var_keys.append((rid, ft))

        n_vars = len(var_keys)
        if n_vars == 0:
            return []

        # Objective: minimize negative priority (i.e., maximize priority dispatch)
        c = np.zeros(n_vars)
        for i, (rid, ft) in enumerate(var_keys):
            route = available_routes[rid]
            station_id = route.get("to_station", "")
            risk_key = f"{station_id}:{ft}"
            risk = risk_scores.get(risk_key, {})
            score = risk.get("score", 0) if isinstance(risk, dict) else 0

            # Higher risk = more negative cost = higher priority
            transit_penalty = route.get("travel_ticks", 2) * 0.1
            cross_penalty = 0.3 if route.get("is_cross_region", False) else 0
            c[i] = -(score * 10) + transit_penalty + cross_penalty

        # Bounds: 0 <= x <= route max_load
        bounds = []
        for rid, ft in var_keys:
            route = available_routes[rid]
            max_load = route.get("max_load", 7000)
            bounds.append((0, max_load))

        # Constraints (A_ub @ x <= b_ub)
        A_ub = []
        b_ub = []

        # C1: Depot inventory constraint per fuel type
        for did, depot in depots.items():
            inv = depot.get("inventory", {})
            for ft in FUEL_TYPES:
                row = np.zeros(n_vars)
                for i, (rid, fuel) in enumerate(var_keys):
                    route = available_routes[rid]
                    if route.get("from_depot", "") == did and fuel == ft:
                        row[i] = 1.0
                if np.any(row > 0):
                    A_ub.append(row)
                    b_ub.append(inv.get(ft, 0) * 0.7)  # Keep 30% reserve

        # C2: Depot dispatch rate limit
        for did, depot in depots.items():
            row = np.zeros(n_vars)
            dispatch_rate = depot.get("dispatch_rate", 12000)
            for i, (rid, ft) in enumerate(var_keys):
                route = available_routes[rid]
                if route.get("from_depot", "") == did:
                    row[i] = 1.0
            if np.any(row > 0):
                A_ub.append(row)
                b_ub.append(dispatch_rate)

        # C3: Station overflow prevention (Trap 2 defense)
        for sid, station in stations.items():
            inv = station.get("inventory", {})
            cap = station.get("capacity", {})
            profile = station.get("profile", "regional")
            multiplier = station.get("demand_multiplier", 1.0)

            for ft in FUEL_TYPES:
                row = np.zeros(n_vars)
                for i, (rid, fuel) in enumerate(var_keys):
                    route = available_routes[rid]
                    if route.get("to_station", "") == sid and fuel == ft:
                        row[i] = 1.0

                if np.any(row > 0):
                    current = inv.get(ft, 0)
                    capacity = cap.get(ft, 15000)
                    # Estimate consumption during avg transit (2 ticks)
                    consumption = estimate_demand_per_tick(profile, ft, tick, multiplier) * 2
                    headroom = capacity - max(0, current - consumption)
                    A_ub.append(row)
                    b_ub.append(max(0, headroom))

        if not A_ub:
            return []

        A_ub = np.array(A_ub)
        b_ub = np.array(b_ub)

        try:
            result = linprog(
                c, A_ub=A_ub, b_ub=b_ub, bounds=bounds,
                method="highs", options={"time_limit": 2.0}
            )

            if result.success and result.x is not None:
                allocations = []
                for i, (rid, ft) in enumerate(var_keys):
                    amount = result.x[i]
                    if amount < 100:  # Skip tiny amounts
                        continue

                    route = available_routes[rid]
                    station_id = route.get("to_station", "")
                    risk_key = f"{station_id}:{ft}"
                    risk = risk_scores.get(risk_key, {})

                    allocations.append({
                        "depot_id": route.get("from_depot", ""),
                        "station_id": station_id,
                        "fuel_type": ft,
                        "amount": round(amount, -1),
                        "route_id": rid,
                        "risk_before": risk.get("score", 0) if isinstance(risk, dict) else 0,
                        "hours_left": risk.get("hours_until_empty", 99) if isinstance(risk, dict) else 99,
                        "confidence": 0.8,
                        "explanation": f"LP optimized: {round(amount, -1)}L {ft} via {rid}",
                    })

                # Sort by risk (highest first)
                allocations.sort(key=lambda a: a.get("risk_before", 0), reverse=True)
                return allocations[:6]  # Limit to 6 allocations per tick cycle

            else:
                logger.warning(f"LP solver status: {result.message}")
                return []

        except Exception as e:
            logger.error(f"LP solver error: {e}")
            return []


optimizer = LPOptimizer()


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "intelligence"}


class OptimizeRequest(BaseModel):
    tick: int = 0
    depots: dict = {}
    stations: dict = {}
    routes: dict = {}
    risk_scores: dict = {}
    events: list = []
    supply_arrivals: list = []


@app.post("/optimize")
async def optimize(req: OptimizeRequest):
    """Run LP optimization and return recommended allocations."""
    start = time.time()

    state = {
        "tick": req.tick,
        "depots": req.depots,
        "stations": req.stations,
        "routes": req.routes,
        "risk_scores": req.risk_scores,
        "events": req.events,
        "supply_arrivals": req.supply_arrivals,
    }

    allocations = optimizer.optimize(state)
    elapsed_ms = (time.time() - start) * 1000

    logger.info(f"Optimization complete: {len(allocations)} allocations in {elapsed_ms:.0f}ms")

    return {
        "allocations": allocations,
        "solver": "highs",
        "duration_ms": round(elapsed_ms),
        "tick": req.tick,
    }


class ForecastRequest(BaseModel):
    station_id: str
    profile: str = "regional"
    fuel_type: str = "diesel"
    tick: int = 0
    demand_multiplier: float = 1.0
    horizon: int = 12


@app.post("/forecast")
async def forecast(req: ForecastRequest):
    """Generate demand forecast for a station/fuel."""
    predictions = forecaster.forecast(
        req.station_id, req.profile, req.fuel_type,
        req.tick, req.demand_multiplier, req.horizon
    )
    wape = forecaster.get_wape(req.station_id, req.fuel_type)
    return {
        "station_id": req.station_id,
        "fuel_type": req.fuel_type,
        "predictions": predictions,
        "wape": round(wape, 4),
    }


class DemandUpdateRequest(BaseModel):
    station_id: str
    fuel_type: str
    demand: float


@app.post("/demand-update")
async def update_demand(req: DemandUpdateRequest):
    """Update demand history for EWMA learning."""
    key = f"{req.station_id}:{req.fuel_type}"
    forecaster.update(key, req.demand)
    return {"status": "ok"}

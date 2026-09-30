"""
Jalani Control Tower — Main FastAPI Application
Entry point for the backend API service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.database import Database
from app.decision_engine import DecisionEngine
from app.models import OperatingMode
from app.simulator_client import SimulatorClient
from app.state_manager import StateManager

# ──────────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jalani.main")

# ──────────────────────────────────────────────
# Global Services
# ──────────────────────────────────────────────

sim_client: SimulatorClient = None  # type: ignore
state_manager: StateManager = None  # type: ignore
decision_engine: DecisionEngine = None  # type: ignore
db: Database = None  # type: ignore
ws_connections: List[WebSocket] = []
tick_loop_task: asyncio.Task = None  # type: ignore


# ──────────────────────────────────────────────
# Main Tick Loop
# ──────────────────────────────────────────────

async def tick_loop():
    """
    Core event loop: polls simulator for new ticks,
    runs the observe→decide→act pipeline on each new tick.
    """
    last_processed_tick = -1
    logger.info("Tick loop started — waiting for simulator...")

    while True:
        try:
            # Quick tick check
            current_tick = await state_manager.refresh_instance_only()

            # Skip if same tick or simulator is paused
            state = state_manager.get_state()
            if current_tick == last_processed_tick or state.status == "paused":
                await asyncio.sleep(settings.poll_interval_ms / 1000)
                continue

            # New tick — run full pipeline
            start = time.time()

            # Phase 1: OBSERVE — full state refresh
            await state_manager.full_refresh()
            state = state_manager.get_state()

            # Phase 2: Health check and mode update
            sim_healthy = await sim_client.health_check()
            intel_healthy = decision_engine._intelligence_available
            decision_engine.update_mode(sim_healthy, intel_healthy)

            # Phase 3: DETECT — compute risk scores
            risk_scores = decision_engine.compute_risk_scores(state)

            # Phase 4: DECIDE — generate allocation proposals
            # Only generate decisions every 2 ticks to avoid over-dispatching
            decisions = []
            if current_tick % 2 == 0 and decision_engine.operating_mode != OperatingMode.SAFE_HOLD:
                decisions = await decision_engine.generate_decisions(state)

            # Phase 5: ACT — validate and execute
            results = []
            if decisions:
                results = await decision_engine.process_decisions(decisions, state)

            # Phase 6: Log events
            for event in state.active_events:
                db.log_event(event.model_dump())

            last_processed_tick = current_tick
            elapsed = (time.time() - start) * 1000

            logger.info(
                f"Tick {current_tick} processed in {elapsed:.0f}ms | "
                f"Mode={decision_engine.operating_mode.value} | "
                f"SL={state.service_level:.1f}% | "
                f"Decisions={len(decisions)} | "
                f"Executed={sum(1 for r in results if r.get('status') == 'executed')}"
            )

            # Push update to WebSocket clients
            await broadcast_state_update()

        except asyncio.CancelledError:
            logger.info("Tick loop cancelled")
            break
        except Exception as e:
            logger.error(f"Tick loop error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def broadcast_state_update():
    """Push state snapshot to all connected WebSocket clients."""
    if not ws_connections:
        return

    try:
        snapshot = state_manager.get_state_snapshot()
        snapshot["risk_scores"] = decision_engine.risk_scores
        snapshot["pending_decisions"] = decision_engine.get_pending_decisions()
        snapshot["operating_mode"] = decision_engine.operating_mode.value

        message = json.dumps(snapshot, default=str)
        disconnected = []
        for ws in ws_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            ws_connections.remove(ws)
    except Exception as e:
        logger.error(f"Broadcast error: {e}")


# ──────────────────────────────────────────────
# Application Lifecycle
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    global sim_client, state_manager, decision_engine, db, tick_loop_task

    logger.info("=" * 60)
    logger.info("  JALANI CONTROL TOWER — Starting Up")
    logger.info("=" * 60)

    # Initialize services
    sim_client = SimulatorClient(settings.simulator_url)
    db = Database(settings.database_path)
    state_manager = StateManager(sim_client)
    decision_engine = DecisionEngine(sim_client, state_manager, db)

    # Initial state fetch
    try:
        await state_manager.full_refresh()
        logger.info(f"Initial state: tick={state_manager.get_state().tick}")
    except Exception as e:
        logger.warning(f"Initial state fetch failed (simulator may not be ready): {e}")

    # Start tick loop
    tick_loop_task = asyncio.create_task(tick_loop())
    logger.info("Tick loop started")

    yield

    # Shutdown
    logger.info("Shutting down...")
    if tick_loop_task:
        tick_loop_task.cancel()
        try:
            await tick_loop_task
        except asyncio.CancelledError:
            pass
    await sim_client.close()
    db.close()
    logger.info("Shutdown complete")


# ──────────────────────────────────────────────
# FastAPI Application
# ──────────────────────────────────────────────

app = FastAPI(
    title="Jalani Control Tower",
    description="Intelligent Fuel Supply Operations Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Health & Status Endpoints
# ──────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Aggregated health check for all components."""
    sim_healthy = await sim_client.health_check() if sim_client else False
    db_stats = db.get_stats() if db else {}

    components = {
        "simulator": {
            "status": "healthy" if sim_healthy else "unhealthy",
            "circuit_breaker": sim_client.circuit_breaker.state if sim_client else "unknown",
            "request_count": sim_client.request_count if sim_client else 0,
            "error_rate": f"{sim_client.error_rate:.1%}" if sim_client else "N/A",
        },
        "intelligence": {
            "status": "healthy" if decision_engine and decision_engine._intelligence_available else "degraded",
        },
        "database": {
            "status": "healthy" if db else "unhealthy",
            **db_stats,
        },
    }

    statuses = [c["status"] for c in components.values()]
    if all(s == "healthy" for s in statuses):
        overall = "healthy"
    elif any(s == "unhealthy" for s in statuses):
        overall = "unhealthy"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "mode": decision_engine.operating_mode.value if decision_engine else "unknown",
        "tick": state_manager.get_state().tick if state_manager else 0,
        "service_level": state_manager.get_state().service_level if state_manager else 0,
        "components": components,
        "ws_connections": len(ws_connections),
    }


# ──────────────────────────────────────────────
# State Endpoints (for Dashboard)
# ──────────────────────────────────────────────

@app.get("/api/state")
async def get_state():
    """Full world state snapshot for the dashboard."""
    snapshot = state_manager.get_state_snapshot()
    snapshot["risk_scores"] = decision_engine.risk_scores
    snapshot["pending_decisions"] = decision_engine.get_pending_decisions()
    snapshot["operating_mode"] = decision_engine.operating_mode.value
    return snapshot


@app.get("/api/risk-scores")
async def get_risk_scores():
    """Current risk scores for all station/fuel combinations."""
    return {"risk_scores": decision_engine.risk_scores}


# ──────────────────────────────────────────────
# Decision Endpoints
# ──────────────────────────────────────────────

@app.get("/api/decisions/pending")
async def get_pending_decisions():
    """Get all decisions waiting for human approval."""
    return {"decisions": decision_engine.get_pending_decisions()}


@app.get("/api/decisions/history")
async def get_decision_history(limit: int = 50):
    """Get decision history from audit log."""
    return {"decisions": db.get_decisions(limit=limit)}


class ApproveRequest(BaseModel):
    by: str = "operator"


@app.post("/api/decisions/{decision_id}/approve")
async def approve_decision(decision_id: str, req: ApproveRequest):
    """Approve a pending decision."""
    result = await decision_engine.approve_decision(decision_id, req.by)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class RejectRequest(BaseModel):
    reason: str = ""
    by: str = "operator"


@app.post("/api/decisions/{decision_id}/reject")
async def reject_decision(decision_id: str, req: RejectRequest):
    """Reject a pending decision."""
    result = decision_engine.reject_decision(decision_id, req.reason, req.by)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ──────────────────────────────────────────────
# Alert Endpoints
# ──────────────────────────────────────────────

@app.get("/api/alerts")
async def get_alerts(limit: int = 20, unacknowledged_only: bool = False):
    """Get alerts."""
    return {"alerts": db.get_alerts(limit=limit, unacknowledged_only=unacknowledged_only)}


@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    db.acknowledge_alert(alert_id)
    return {"status": "acknowledged"}


# ──────────────────────────────────────────────
# Event History
# ──────────────────────────────────────────────

@app.get("/api/events")
async def get_events(limit: int = 20):
    """Get crisis event history."""
    return {"events": db.get_events(limit=limit)}


# ──────────────────────────────────────────────
# Simulator Control Endpoints
# ──────────────────────────────────────────────

@app.post("/api/simulator/start")
async def start_simulator():
    """Start/resume the simulation."""
    success = await sim_client.admin_start()
    return {"success": success}


@app.post("/api/simulator/pause")
async def pause_simulator():
    """Pause the simulation."""
    success = await sim_client.admin_pause()
    return {"success": success}


class SpeedRequest(BaseModel):
    speed: int = 8


@app.post("/api/simulator/speed")
async def set_speed(req: SpeedRequest):
    """Set simulation speed."""
    success = await sim_client.admin_set_speed(req.speed)
    return {"success": success}


# ──────────────────────────────────────────────
# WebSocket (Real-time Push)
# ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await websocket.accept()
    ws_connections.append(websocket)
    logger.info(f"WebSocket client connected (total: {len(ws_connections)})")

    try:
        # Send initial state
        snapshot = state_manager.get_state_snapshot()
        snapshot["risk_scores"] = decision_engine.risk_scores
        snapshot["pending_decisions"] = decision_engine.get_pending_decisions()
        snapshot["operating_mode"] = decision_engine.operating_mode.value
        await websocket.send_text(json.dumps(snapshot, default=str))

        # Keep alive and listen for client messages
        while True:
            data = await websocket.receive_text()
            # Client can send commands via WebSocket if needed
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected (total: {len(ws_connections)})")


# ──────────────────────────────────────────────
# Prometheus Metrics Endpoint
# ──────────────────────────────────────────────

@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    state = state_manager.get_state() if state_manager else None
    tick = state.tick if state else 0
    sl = state.service_level if state else 0
    mode = decision_engine.operating_mode.value if decision_engine else "unknown"
    cb = sim_client.circuit_breaker.state if sim_client else "unknown"
    errors = sim_client.error_count if sim_client else 0
    requests = sim_client.request_count if sim_client else 0
    pending = len(decision_engine._pending_decisions) if decision_engine else 0
    decisions = db.get_decision_count() if db else 0

    # Build Prometheus exposition format
    lines = [
        f'# HELP jalani_tick_current Current simulator tick',
        f'# TYPE jalani_tick_current gauge',
        f'jalani_tick_current {tick}',
        f'# HELP jalani_service_level_pct Simulator service level percentage',
        f'# TYPE jalani_service_level_pct gauge',
        f'jalani_service_level_pct {sl}',
        f'# HELP jalani_decisions_total Total decisions made',
        f'# TYPE jalani_decisions_total counter',
        f'jalani_decisions_total {decisions}',
        f'# HELP jalani_decisions_pending Decisions awaiting approval',
        f'# TYPE jalani_decisions_pending gauge',
        f'jalani_decisions_pending {pending}',
        f'# HELP jalani_sim_requests_total Total simulator API requests',
        f'# TYPE jalani_sim_requests_total counter',
        f'jalani_sim_requests_total {requests}',
        f'# HELP jalani_sim_errors_total Total simulator API errors',
        f'# TYPE jalani_sim_errors_total counter',
        f'jalani_sim_errors_total {errors}',
    ]

    # Risk scores
    if decision_engine and decision_engine._risk_scores:
        lines.append('# HELP jalani_risk_score Station fuel risk score')
        lines.append('# TYPE jalani_risk_score gauge')
        for key, rs in decision_engine._risk_scores.items():
            lines.append(
                f'jalani_risk_score{{station="{rs.station_id}",fuel_type="{rs.fuel_type}"}} {rs.score}'
            )

    # Operating mode (encode as gauge)
    mode_map = {"normal": 0, "degraded": 1, "safe_hold": 2, "fallback": 3}
    lines.append('# HELP jalani_operating_mode Current operating mode (0=normal,1=degraded,2=safe_hold,3=fallback)')
    lines.append('# TYPE jalani_operating_mode gauge')
    lines.append(f'jalani_operating_mode {mode_map.get(mode, -1)}')

    return JSONResponse(
        content="\n".join(lines) + "\n",
        media_type="text/plain",
    )

"""
Jalani Control Tower — Database Module
SQLite-based audit log, decision history, and state snapshots.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("jalani.database")


class Database:
    """SQLite database for audit logging and decision history."""

    def __init__(self, db_path: str = "./data/jalani.db"):
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decision_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id         TEXT UNIQUE NOT NULL,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                tick                INTEGER NOT NULL,
                sim_time            TEXT,
                type                TEXT NOT NULL,
                depot_id            TEXT,
                station_id          TEXT,
                fuel_type           TEXT,
                amount_liters       REAL,
                route_id            TEXT,
                risk_score_before   REAL,
                risk_score_after    REAL,
                hours_until_empty   REAL,
                trigger_reason      TEXT,
                explanation         TEXT,
                confidence          REAL,
                status              TEXT NOT NULL,
                approval_mode       TEXT,
                approved_by         TEXT,
                approved_at         TEXT,
                rejection_reason    TEXT,
                allocation_id       TEXT,
                idempotency_key     TEXT,
                simulator_response  TEXT,
                executed_at         TEXT,
                expected_arrival    INTEGER,
                actual_arrival      INTEGER,
                delivery_status     TEXT,
                waste_liters        REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_decision_tick ON decision_log(tick);
            CREATE INDEX IF NOT EXISTS idx_decision_station ON decision_log(station_id);
            CREATE INDEX IF NOT EXISTS idx_decision_status ON decision_log(status);

            CREATE TABLE IF NOT EXISTS event_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id            TEXT UNIQUE NOT NULL,
                detected_at         TEXT NOT NULL DEFAULT (datetime('now')),
                type                TEXT NOT NULL,
                target_id           TEXT NOT NULL,
                target_type         TEXT NOT NULL,
                start_tick          INTEGER NOT NULL,
                end_tick            INTEGER,
                duration_ticks      INTEGER,
                severity            TEXT,
                description         TEXT,
                parameters          TEXT,
                system_response     TEXT,
                response_tick       INTEGER,
                response_latency    INTEGER,
                impact_description  TEXT,
                service_level_before REAL,
                service_level_after  REAL,
                incident_summary    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_event_tick ON event_history(start_tick);

            CREATE TABLE IF NOT EXISTS state_snapshot (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                tick                INTEGER NOT NULL,
                captured_at         TEXT NOT NULL DEFAULT (datetime('now')),
                operating_mode      TEXT NOT NULL,
                service_level       REAL,
                depots_state        TEXT NOT NULL,
                stations_state      TEXT NOT NULL,
                routes_state        TEXT NOT NULL,
                active_events       TEXT,
                total_dispatched    REAL,
                total_waste         REAL,
                total_delivered     REAL,
                decisions_count     INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_tick ON state_snapshot(tick);

            CREATE TABLE IF NOT EXISTS alerts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id            TEXT UNIQUE NOT NULL,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                severity            TEXT NOT NULL,
                title               TEXT NOT NULL,
                message             TEXT NOT NULL,
                source              TEXT,
                tick                INTEGER,
                acknowledged        INTEGER DEFAULT 0,
                acknowledged_at     TEXT,
                acknowledged_by     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
        """)
        self.conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    # ── Decision Log ──────────────────────────

    def log_decision(self, decision: dict):
        """Insert a decision into the audit log."""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO decision_log (
                    decision_id, tick, sim_time, type,
                    depot_id, station_id, fuel_type, amount_liters, route_id,
                    risk_score_before, risk_score_after, hours_until_empty,
                    trigger_reason, explanation, confidence,
                    status, approval_mode, approved_by,
                    allocation_id, idempotency_key, simulator_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision.get("decision_id", ""),
                decision.get("tick", 0),
                decision.get("sim_time"),
                decision.get("type", "allocation"),
                decision.get("depot_id"),
                decision.get("station_id"),
                decision.get("fuel_type"),
                decision.get("amount_liters"),
                decision.get("route_id"),
                decision.get("risk_score_before"),
                decision.get("risk_score_after"),
                decision.get("hours_until_empty"),
                decision.get("trigger_reason"),
                decision.get("explanation"),
                decision.get("confidence"),
                decision.get("status", "proposed"),
                decision.get("approval_mode"),
                decision.get("approved_by"),
                decision.get("allocation_id"),
                decision.get("idempotency_key"),
                decision.get("simulator_response"),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")

    def update_decision_status(self, decision_id: str, status: str, **kwargs):
        """Update the status of a decision."""
        try:
            fields = ["status = ?"]
            values = [status]
            for key, value in kwargs.items():
                fields.append(f"{key} = ?")
                values.append(value)
            values.append(decision_id)
            self.conn.execute(
                f"UPDATE decision_log SET {', '.join(fields)} WHERE decision_id = ?",
                values
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to update decision {decision_id}: {e}")

    def get_decisions(self, limit: int = 50, status: Optional[str] = None) -> List[dict]:
        """Fetch recent decisions."""
        try:
            if status:
                rows = self.conn.execute(
                    "SELECT * FROM decision_log WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM decision_log ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get decisions: {e}")
            return []

    def get_decision_count(self) -> int:
        """Get total number of decisions."""
        try:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM decision_log").fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0

    # ── Alerts ────────────────────────────────

    def log_alert(self, alert: dict):
        """Insert an alert."""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO alerts (alert_id, severity, title, message, source, tick)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                alert.get("id", ""),
                alert.get("severity", "info"),
                alert.get("title", ""),
                alert.get("message", ""),
                alert.get("source", ""),
                alert.get("tick", 0),
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to log alert: {e}")

    def get_alerts(self, limit: int = 20, unacknowledged_only: bool = False) -> List[dict]:
        """Fetch recent alerts."""
        try:
            if unacknowledged_only:
                rows = self.conn.execute(
                    "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get alerts: {e}")
            return []

    def acknowledge_alert(self, alert_id: str, by: str = "operator"):
        """Acknowledge an alert."""
        try:
            self.conn.execute(
                "UPDATE alerts SET acknowledged = 1, acknowledged_at = datetime('now'), acknowledged_by = ? WHERE alert_id = ?",
                (by, alert_id)
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to acknowledge alert: {e}")

    # ── Event History ─────────────────────────

    def log_event(self, event: dict):
        """Insert a crisis event into history."""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO event_history (
                    event_id, type, target_id, target_type,
                    start_tick, end_tick, severity, description, parameters
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get("id", ""),
                event.get("type", ""),
                event.get("target", ""),
                event.get("target_type", "unknown"),
                event.get("start_tick", 0),
                event.get("end_tick"),
                event.get("severity", "medium"),
                event.get("description", ""),
                json.dumps(event.get("parameters")) if event.get("parameters") else None,
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to log event: {e}")

    def get_events(self, limit: int = 20) -> List[dict]:
        """Fetch recent events."""
        try:
            rows = self.conn.execute(
                "SELECT * FROM event_history ORDER BY start_tick DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get events: {e}")
            return []

    # ── Stats ─────────────────────────────────

    def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            decisions = self.conn.execute("SELECT COUNT(*) as cnt FROM decision_log").fetchone()
            events = self.conn.execute("SELECT COUNT(*) as cnt FROM event_history").fetchone()
            alerts = self.conn.execute("SELECT COUNT(*) as cnt FROM alerts").fetchone()
            return {
                "total_decisions": decisions["cnt"] if decisions else 0,
                "total_events": events["cnt"] if events else 0,
                "total_alerts": alerts["cnt"] if alerts else 0,
            }
        except Exception:
            return {"total_decisions": 0, "total_events": 0, "total_alerts": 0}

    def close(self):
        if self.conn:
            self.conn.close()

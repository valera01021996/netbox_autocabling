"""State database for tracking MAC observations and stability."""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MACStatus(Enum):
    """Status of MAC address processing."""

    CREATED = "created"  # Cable was created
    EXISTS = "exists"  # Cable already exists
    SKIP_NON_ACCESS = "skip_non_access"  # Port is not access
    AMBIGUOUS = "ambiguous"  # Multiple endpoints found
    NOT_FOUND = "not_found"  # MAC not found in FDB
    ERROR = "error"  # Error during processing
    PENDING = "pending"  # Waiting for stability
    MISMATCH = "mismatch"  # MAC on cable port differs from expected


@dataclass
class MACObservation:
    """Observation of a MAC address."""

    mac: str
    switch_name: str
    port_name: str
    vlan: Optional[int]
    seen_at: datetime
    stability_count: int = 0


@dataclass
class MACState:
    """Full state of a MAC address."""

    mac: str
    last_switch: Optional[str] = None
    last_port: Optional[str] = None
    last_vlan: Optional[int] = None
    last_seen: Optional[datetime] = None
    stability_count: int = 0
    last_status: Optional[MACStatus] = None
    last_action_at: Optional[datetime] = None
    cable_created: bool = False
    cable_id: Optional[int] = None


class StateDB:
    """SQLite-based state database."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS mac_observations (
                mac TEXT PRIMARY KEY,
                switch_name TEXT,
                port_name TEXT,
                vlan INTEGER,
                seen_at TEXT,
                stability_count INTEGER DEFAULT 0,
                last_status TEXT,
                last_action_at TEXT,
                cable_created INTEGER DEFAULT 0,
                cable_id INTEGER
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                total_macs INTEGER,
                cnt_created INTEGER,
                cnt_exists INTEGER,
                cnt_skipped INTEGER,
                cnt_ambiguous INTEGER,
                cnt_not_found INTEGER,
                cnt_errors INTEGER
            )
        """)

        self._conn.commit()
        logger.debug(f"State database initialized at {self.db_path}")

    def get_state(self, mac: str) -> Optional[MACState]:
        """Get current state for a MAC address."""
        cursor = self._conn.execute(
            "SELECT * FROM mac_observations WHERE mac = ?", (mac,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        return MACState(
            mac=row["mac"],
            last_switch=row["switch_name"],
            last_port=row["port_name"],
            last_vlan=row["vlan"],
            last_seen=datetime.fromisoformat(row["seen_at"]) if row["seen_at"] else None,
            stability_count=row["stability_count"],
            last_status=MACStatus(row["last_status"]) if row["last_status"] else None,
            last_action_at=datetime.fromisoformat(row["last_action_at"]) if row["last_action_at"] else None,
            cable_created=bool(row["cable_created"]),
            cable_id=row["cable_id"],
        )

    def update_observation(
        self,
        mac: str,
        switch_name: str,
        port_name: str,
        vlan: Optional[int],
        stability_threshold: int,
    ) -> tuple[int, bool]:
        """
        Update MAC observation and return stability info.

        Returns:
            (stability_count, is_stable) tuple
        """
        now = datetime.now(timezone.utc).isoformat()
        state = self.get_state(mac)

        if state is None:
            self._conn.execute("""
                INSERT INTO mac_observations (mac, switch_name, port_name, vlan, seen_at, stability_count)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (mac, switch_name, port_name, vlan, now))
            self._conn.commit()
            return (1, 1 >= stability_threshold)

        if state.last_switch == switch_name and state.last_port == port_name:
            new_count = state.stability_count + 1
        else:
            new_count = 1

        self._conn.execute("""
            UPDATE mac_observations
            SET switch_name = ?, port_name = ?, vlan = ?, seen_at = ?, stability_count = ?
            WHERE mac = ?
        """, (switch_name, port_name, vlan, now, new_count, mac))
        self._conn.commit()

        return (new_count, new_count >= stability_threshold)

    def update_status(
        self,
        mac: str,
        status: MACStatus,
        cable_id: Optional[int] = None,
    ):
        """Update MAC status after processing."""
        now = datetime.now(timezone.utc).isoformat()

        if status == MACStatus.CREATED:
            self._conn.execute("""
                UPDATE mac_observations
                SET last_status = ?, last_action_at = ?, cable_created = 1, cable_id = ?
                WHERE mac = ?
            """, (status.value, now, cable_id, mac))
        else:
            self._conn.execute("""
                UPDATE mac_observations
                SET last_status = ?, last_action_at = ?
                WHERE mac = ?
            """, (status.value, now, mac))

        self._conn.commit()

    def mark_not_found(self, mac: str):
        """Mark MAC as not found (reset stability)."""
        now = datetime.now(timezone.utc).isoformat()
        state = self.get_state(mac)

        if state:
            self._conn.execute("""
                UPDATE mac_observations
                SET stability_count = 0, last_status = ?, last_action_at = ?
                WHERE mac = ?
            """, (MACStatus.NOT_FOUND.value, now, mac))
        else:
            self._conn.execute("""
                INSERT INTO mac_observations (mac, stability_count, last_status, last_action_at)
                VALUES (?, 0, ?, ?)
            """, (mac, MACStatus.NOT_FOUND.value, now))

        self._conn.commit()

    def record_run(
        self,
        total_macs: int,
        created: int,
        exists: int,
        skipped: int,
        ambiguous: int,
        not_found: int,
        errors: int,
    ):
        """Record run statistics."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT INTO run_history
            (run_at, total_macs, cnt_created, cnt_exists, cnt_skipped, cnt_ambiguous, cnt_not_found, cnt_errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, total_macs, created, exists, skipped, ambiguous, not_found, errors))
        self._conn.commit()

    def get_all_with_cables(self) -> list[MACState]:
        """Get all MACs where cables were created."""
        cursor = self._conn.execute(
            "SELECT * FROM mac_observations WHERE cable_created = 1"
        )
        return [
            MACState(
                mac=row["mac"],
                last_switch=row["switch_name"],
                last_port=row["port_name"],
                last_vlan=row["vlan"],
                last_seen=datetime.fromisoformat(row["seen_at"]) if row["seen_at"] else None,
                stability_count=row["stability_count"],
                last_status=MACStatus(row["last_status"]) if row["last_status"] else None,
                last_action_at=datetime.fromisoformat(row["last_action_at"]) if row["last_action_at"] else None,
                cable_created=bool(row["cable_created"]),
                cable_id=row["cable_id"],
            )
            for row in cursor.fetchall()
        ]

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

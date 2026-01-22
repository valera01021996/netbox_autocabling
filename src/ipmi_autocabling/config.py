"""Configuration management."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    """Application configuration."""

    # NetBox
    netbox_url: str = ""
    netbox_token: str = ""
    netbox_verify_ssl: bool = False

    # Selectors (site определяется автоматически по серверам)
    switches_role: Optional[str] = None  # Role коммутаторов для фильтрации

    # IPMI interface detection
    ipmi_interface_names: list[str] = field(
        default_factory=lambda: ["IPMI", "BMC", "MGMT", "iLO", "iDRAC", "CIMC"]
    )

    # SNMP settings
    snmp_community: str = "public"
    snmp_version: str = "2c"
    snmp_timeout: int = 5
    snmp_retries: int = 2

    # Port classification
    uplink_ports: list[str] = field(default_factory=list)
    uplink_patterns: list[str] = field(
        default_factory=lambda: [
            r"uplink",
            r"to[-_]?spine",
            r"trunk",
            r"peer",
            r"mlag",
            r"lag",
            r"^po\d+",
            r"port[-_]?channel",
        ]
    )

    # Stability
    stability_runs: int = 2

    # State DB
    state_db_path: str = "state.db"

    # Operation mode
    poll_interval: int = 300  # seconds, 0 = one-shot mode
    dry_run: bool = False
    cable_status: str = "planned"  # planned or connected

    # MLAG groups: list of tuples (switch1_name, switch2_name)
    mlag_groups: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        config = cls()

        config.netbox_url = os.getenv("NETBOX_URL", "")
        config.netbox_token = os.getenv("NETBOX_TOKEN", "")
        config.netbox_verify_ssl = os.getenv("NETBOX_VERIFY_SSL", "false").lower() == "true"

        config.switches_role = os.getenv("SWITCHES_ROLE")

        if ipmi_names := os.getenv("IPMI_INTERFACE_NAMES"):
            config.ipmi_interface_names = [n.strip() for n in ipmi_names.split(",")]

        config.snmp_community = os.getenv("SNMP_COMMUNITY", "public")
        config.snmp_version = os.getenv("SNMP_VERSION", "2c")
        config.snmp_timeout = int(os.getenv("SNMP_TIMEOUT", "5"))
        config.snmp_retries = int(os.getenv("SNMP_RETRIES", "2"))

        if uplink_ports := os.getenv("UPLINK_PORTS"):
            config.uplink_ports = [p.strip() for p in uplink_ports.split(",")]

        if uplink_patterns := os.getenv("UPLINK_PATTERNS"):
            config.uplink_patterns = [p.strip() for p in uplink_patterns.split(",")]

        config.stability_runs = int(os.getenv("STABILITY_RUNS", "2"))
        config.state_db_path = os.getenv("STATE_DB_PATH", "state.db")
        config.poll_interval = int(os.getenv("POLL_INTERVAL", "0"))
        config.dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
        config.cable_status = os.getenv("CABLE_STATUS", "connected")

        if mlag_groups := os.getenv("MLAG_GROUPS"):
            # Format: "switch1:switch2,switch3:switch4"
            config.mlag_groups = []
            for group in mlag_groups.split(","):
                parts = group.strip().split(":")
                if len(parts) == 2:
                    config.mlag_groups.append((parts[0].strip(), parts[1].strip()))

        return config

    def get_uplink_pattern(self) -> re.Pattern:
        """Compile uplink patterns into a single regex."""
        combined = "|".join(f"({p})" for p in self.uplink_patterns)
        return re.compile(combined, re.IGNORECASE)

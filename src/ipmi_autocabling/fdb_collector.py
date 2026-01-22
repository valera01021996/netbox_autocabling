"""FDB/MAC table collector via SNMP."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .mac_utils import normalize_mac, oid_suffix_to_mac

logger = logging.getLogger(__name__)

# SNMP OIDs for FDB collection
# dot1dTpFdbAddress - MAC address table (bridge MIB)
DOT1D_TP_FDB_ADDRESS = "1.3.6.1.2.1.17.4.3.1.1"
DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"

# dot1qTpFdbPort - VLAN-aware FDB (Q-Bridge MIB)
DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"

# Huawei hwMacFwdPort - Huawei CE switches
# OID structure: hwMacFwdPort.MAC(6 octets).VLAN.0 = ifIndex
HW_MAC_FWD_PORT = "1.3.6.1.4.1.2011.5.25.42.2.1.3.1.4"

# ifName - interface names by ifIndex
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"


@dataclass
class FDBEntry:
    """Single FDB entry."""

    mac: str  # normalized MAC
    switch_name: str
    switch_ip: str
    port_name: str
    port_index: int
    vlan: Optional[int] = None
    seen_at: datetime = None

    def __post_init__(self):
        if self.seen_at is None:
            self.seen_at = datetime.now(timezone.utc)


class FDBCollector:
    """Collect FDB/MAC tables from switches via SNMP."""

    def __init__(self, config: Config):
        self.config = config
        self._pysnmp_available = False
        self._snmp_engine = None
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from pysnmp.hlapi import SnmpEngine
            self._pysnmp_available = True
            self._snmp_engine = SnmpEngine()
        except ImportError:
            logger.warning(
                "pysnmp not installed. FDB collection will use mock data. "
                "Install with: pip install pysnmp"
            )

    def collect_fdb(
        self, switch_name: str, switch_ip: str
    ) -> list[FDBEntry]:
        """
        Collect FDB entries from a switch.

        Returns list of FDBEntry objects.
        """
        if not self._pysnmp_available:
            logger.warning(f"Skipping FDB collection for {switch_name}: pysnmp not available")
            return []

        if not switch_ip:
            logger.warning(f"Skipping FDB collection for {switch_name}: no IP address")
            return []

        logger.info(f"Collecting FDB from {switch_name} ({switch_ip})")

        try:
            if_names = self._get_interface_names(switch_ip)

            # Try Huawei MIB first (most accurate for Huawei switches)
            entries = self._collect_huawei_fdb(switch_name, switch_ip, if_names)

            # Fallback to Q-Bridge MIB
            if not entries:
                entries = self._collect_q_bridge_fdb(switch_name, switch_ip, if_names)

            # Fallback to standard Bridge MIB
            if not entries:
                entries = self._collect_bridge_fdb(switch_name, switch_ip, if_names)

            logger.info(f"Collected {len(entries)} FDB entries from {switch_name}")
            return entries

        except Exception as e:
            logger.error(f"Failed to collect FDB from {switch_name}: {e}")
            return []

    def _get_interface_names(self, switch_ip: str) -> dict[int, str]:
        """Get interface names by ifIndex."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pysnmp.hlapi import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                UdpTransportTarget,
                nextCmd,
            )

        if_names = {}

        for error_indication, error_status, error_index, var_binds in nextCmd(
            self._snmp_engine,
            CommunityData(self.config.snmp_community),
            UdpTransportTarget(
                (switch_ip, 161),
                timeout=self.config.snmp_timeout,
                retries=self.config.snmp_retries,
            ),
            ContextData(),
            ObjectType(ObjectIdentity(IF_NAME)),
            lexicographicMode=False,
        ):
            if error_indication or error_status:
                break

            for var_bind in var_binds:
                oid = str(var_bind[0])
                value = str(var_bind[1])
                if_index = int(oid.split(".")[-1])
                if_names[if_index] = value

        return if_names

    def _collect_huawei_fdb(
        self, switch_name: str, switch_ip: str, if_names: dict[int, str]
    ) -> list[FDBEntry]:
        """Collect FDB using Huawei hwMacFwdPort MIB."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pysnmp.hlapi import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                UdpTransportTarget,
                nextCmd,
            )

        entries = []

        for error_indication, error_status, error_index, var_binds in nextCmd(
            self._snmp_engine,
            CommunityData(self.config.snmp_community),
            UdpTransportTarget(
                (switch_ip, 161),
                timeout=self.config.snmp_timeout,
                retries=self.config.snmp_retries,
            ),
            ContextData(),
            ObjectType(ObjectIdentity(HW_MAC_FWD_PORT)),
            lexicographicMode=False,
        ):
            if error_indication:
                logger.debug(f"SNMP error: {error_indication}")
                break

            if error_status:
                logger.debug(f"SNMP error status: {error_status}")
                break

            for var_bind in var_binds:
                oid = str(var_bind[0])
                port_index = int(var_bind[1])

                # OID format: HW_MAC_FWD_PORT.mac(6).vlan.0
                # Example: 1.3.6.1.4.1.2011.5.25.42.2.1.3.1.4.0.224.237.219.143.82.10.0
                oid_parts = oid.split(".")
                base_len = len(HW_MAC_FWD_PORT.split("."))

                # Need at least 8 more parts: 6 for MAC + 1 for VLAN + 1 trailing 0
                if len(oid_parts) < base_len + 8:
                    continue

                mac_octets = oid_parts[base_len : base_len + 6]
                vlan = int(oid_parts[base_len + 6])

                try:
                    mac = ":".join(f"{int(o):02x}" for o in mac_octets)
                    port_name = if_names.get(port_index, f"port{port_index}")

                    entries.append(FDBEntry(
                        mac=mac,
                        switch_name=switch_name,
                        switch_ip=switch_ip,
                        port_name=port_name,
                        port_index=port_index,
                        vlan=vlan,
                    ))
                except ValueError as e:
                    logger.debug(f"Failed to parse MAC from OID: {e}")

        if entries:
            logger.debug(f"Collected {len(entries)} entries using Huawei MIB")

        return entries

    def _collect_q_bridge_fdb(
        self, switch_name: str, switch_ip: str, if_names: dict[int, str]
    ) -> list[FDBEntry]:
        """Collect FDB using Q-Bridge MIB (VLAN-aware)."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pysnmp.hlapi import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                UdpTransportTarget,
                nextCmd,
            )

        entries = []

        for error_indication, error_status, error_index, var_binds in nextCmd(
            self._snmp_engine,
            CommunityData(self.config.snmp_community),
            UdpTransportTarget(
                (switch_ip, 161),
                timeout=self.config.snmp_timeout,
                retries=self.config.snmp_retries,
            ),
            ContextData(),
            ObjectType(ObjectIdentity(DOT1Q_TP_FDB_PORT)),
            lexicographicMode=False,
        ):
            if error_indication:
                logger.debug(f"SNMP error: {error_indication}")
                break

            if error_status:
                logger.debug(f"SNMP error status: {error_status}")
                break

            for var_bind in var_binds:
                oid = str(var_bind[0])
                port_index = int(var_bind[1])

                # OID format: DOT1Q_TP_FDB_PORT.vlan.mac_octets
                oid_parts = oid.split(".")
                base_len = len(DOT1Q_TP_FDB_PORT.split("."))

                if len(oid_parts) < base_len + 7:
                    continue

                vlan = int(oid_parts[base_len])
                mac_suffix = ".".join(oid_parts[base_len + 1 : base_len + 7])

                try:
                    mac = oid_suffix_to_mac(mac_suffix)
                    port_name = if_names.get(port_index, f"port{port_index}")

                    entries.append(FDBEntry(
                        mac=mac,
                        switch_name=switch_name,
                        switch_ip=switch_ip,
                        port_name=port_name,
                        port_index=port_index,
                        vlan=vlan,
                    ))
                except ValueError as e:
                    logger.debug(f"Failed to parse MAC from OID: {e}")

        return entries

    def _collect_bridge_fdb(
        self, switch_name: str, switch_ip: str, if_names: dict[int, str]
    ) -> list[FDBEntry]:
        """Collect FDB using Bridge MIB (non-VLAN aware)."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pysnmp.hlapi import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                UdpTransportTarget,
                nextCmd,
            )

        mac_to_port = {}

        for error_indication, error_status, error_index, var_binds in nextCmd(
            self._snmp_engine,
            CommunityData(self.config.snmp_community),
            UdpTransportTarget(
                (switch_ip, 161),
                timeout=self.config.snmp_timeout,
                retries=self.config.snmp_retries,
            ),
            ContextData(),
            ObjectType(ObjectIdentity(DOT1D_TP_FDB_PORT)),
            lexicographicMode=False,
        ):
            if error_indication or error_status:
                break

            for var_bind in var_binds:
                oid = str(var_bind[0])
                port_index = int(var_bind[1])

                oid_parts = oid.split(".")
                base_len = len(DOT1D_TP_FDB_PORT.split("."))

                if len(oid_parts) < base_len + 6:
                    continue

                mac_suffix = ".".join(oid_parts[base_len : base_len + 6])

                try:
                    mac = oid_suffix_to_mac(mac_suffix)
                    mac_to_port[mac] = port_index
                except ValueError:
                    continue

        entries = []
        for mac, port_index in mac_to_port.items():
            port_name = if_names.get(port_index, f"port{port_index}")
            entries.append(FDBEntry(
                mac=mac,
                switch_name=switch_name,
                switch_ip=switch_ip,
                port_name=port_name,
                port_index=port_index,
            ))

        return entries


class MockFDBCollector:
    """Mock FDB collector for testing."""

    def __init__(self, fdb_data: dict[str, list[FDBEntry]]):
        """
        Initialize with mock data.

        Args:
            fdb_data: dict mapping switch_name to list of FDBEntry
        """
        self.fdb_data = fdb_data

    def collect_fdb(self, switch_name: str, switch_ip: str) -> list[FDBEntry]:
        """Return mock FDB entries for switch."""
        return self.fdb_data.get(switch_name, [])


def load_fdb_snapshot(path: str) -> dict[str, list[FDBEntry]]:
    """
    Load FDB snapshot from JSON file for testing.

    Expected format:
    {
        "switch1": [
            {"mac": "aa:bb:cc:dd:ee:ff", "port": "Ethernet1", "vlan": 100},
            ...
        ],
        ...
    }
    """
    import json

    with open(path) as f:
        data = json.load(f)

    result = {}
    for switch_name, entries in data.items():
        result[switch_name] = []
        for entry in entries:
            result[switch_name].append(FDBEntry(
                mac=normalize_mac(entry["mac"]),
                switch_name=switch_name,
                switch_ip="",
                port_name=entry.get("port", "unknown"),
                port_index=entry.get("port_index", 0),
                vlan=entry.get("vlan"),
            ))

    return result

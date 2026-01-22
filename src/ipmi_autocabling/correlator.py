"""MAC to endpoint correlation logic."""

import logging
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .fdb_collector import FDBEntry
from .mac_utils import normalize_mac
from .netbox_client import IPMIInterface, NetBoxClient, SwitchInfo
from .port_classifier import PortClassifier, PortClassification
from .state_db import MACStatus, StateDB

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Result of MAC correlation."""

    mac: str
    ipmi_interface: IPMIInterface
    status: MACStatus
    reason: str

    # Populated if MAC was found
    switch_name: Optional[str] = None
    switch_id: Optional[int] = None
    port_name: Optional[str] = None
    port_id: Optional[int] = None
    vlan: Optional[int] = None

    # Port classification
    port_classification: Optional[PortClassification] = None

    # Stability info
    stability_count: int = 0
    is_stable: bool = False

    # Mismatch info (when cable exists but MAC differs)
    expected_mac: Optional[str] = None  # MAC that should be on port (from NetBox cable)
    actual_mac: Optional[str] = None  # MAC actually seen in FDB


class Correlator:
    """Correlate IPMI MACs with FDB entries."""

    def __init__(
        self,
        config: Config,
        netbox: NetBoxClient,
        state_db: StateDB,
        port_classifier: PortClassifier,
    ):
        self.config = config
        self.netbox = netbox
        self.state_db = state_db
        self.port_classifier = port_classifier

        self._mlag_peer_map: dict[str, str] = {}
        for sw1, sw2 in config.mlag_groups:
            self._mlag_peer_map[sw1] = sw2
            self._mlag_peer_map[sw2] = sw1

    def correlate(
        self,
        ipmi_interfaces: list[IPMIInterface],
        fdb_entries: list[FDBEntry],
        switches: list[SwitchInfo],
    ) -> list[CorrelationResult]:
        """
        Correlate IPMI interfaces with FDB entries.

        Returns list of CorrelationResult for each IPMI interface.
        """
        switch_map = {sw.name: sw for sw in switches}

        mac_to_fdb: dict[str, list[FDBEntry]] = {}
        for entry in fdb_entries:
            mac = normalize_mac(entry.mac)
            if mac not in mac_to_fdb:
                mac_to_fdb[mac] = []
            mac_to_fdb[mac].append(entry)

        # Build port->MAC map for mismatch detection
        port_to_mac: dict[tuple[str, str], str] = {}
        for entry in fdb_entries:
            key = (entry.switch_name, entry.port_name)
            # Store the MAC seen on each port (last one wins if multiple)
            port_to_mac[key] = normalize_mac(entry.mac)

        results = []
        for ipmi in ipmi_interfaces:
            result = self._correlate_one(ipmi, mac_to_fdb, switch_map, port_to_mac)
            results.append(result)

        return results

    def _correlate_one(
        self,
        ipmi: IPMIInterface,
        mac_to_fdb: dict[str, list[FDBEntry]],
        switch_map: dict[str, SwitchInfo],
        port_to_mac: dict[tuple[str, str], str],
    ) -> CorrelationResult:
        """Correlate single IPMI interface."""
        mac = normalize_mac(ipmi.mac_address)

        # If cable exists, check for MAC mismatch
        if ipmi.has_cable:
            mismatch = self._check_mismatch(ipmi, port_to_mac)
            if mismatch:
                return mismatch
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.EXISTS,
                reason="IPMI interface already has cable",
            )

        fdb_entries = mac_to_fdb.get(mac, [])

        if not fdb_entries:
            self.state_db.mark_not_found(mac)
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.NOT_FOUND,
                reason="MAC not found in any FDB",
            )

        best_entry = self._resolve_ambiguity(fdb_entries)

        if best_entry is None:
            locations = [f"{e.switch_name}:{e.port_name}" for e in fdb_entries]
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.AMBIGUOUS,
                reason=f"MAC found on multiple endpoints: {', '.join(locations)}",
            )

        switch_info = switch_map.get(best_entry.switch_name)
        if not switch_info:
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.ERROR,
                reason=f"Switch {best_entry.switch_name} not found in NetBox",
            )

        port_classification = self.port_classifier.classify(
            port_name=best_entry.port_name,
        )

        if not port_classification.is_allowed:
            self.state_db.update_status(mac, MACStatus.SKIP_NON_ACCESS)
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.SKIP_NON_ACCESS,
                reason=port_classification.reason,
                switch_name=best_entry.switch_name,
                switch_id=switch_info.id,
                port_name=best_entry.port_name,
                vlan=best_entry.vlan,
                port_classification=port_classification,
            )

        stability_count, is_stable = self.state_db.update_observation(
            mac=mac,
            switch_name=best_entry.switch_name,
            port_name=best_entry.port_name,
            vlan=best_entry.vlan,
            stability_threshold=self.config.stability_runs,
        )

        switch_iface = self.netbox.get_switch_interface_by_name(
            switch_info.id, best_entry.port_name
        )

        if not switch_iface:
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.ERROR,
                reason=f"Interface {best_entry.port_name} not found on {best_entry.switch_name}",
                switch_name=best_entry.switch_name,
                switch_id=switch_info.id,
                port_name=best_entry.port_name,
                vlan=best_entry.vlan,
                port_classification=port_classification,
                stability_count=stability_count,
                is_stable=is_stable,
            )

        if switch_iface.has_cable:
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.SKIP_NON_ACCESS,
                reason=f"Switch port {best_entry.port_name} already has cable",
                switch_name=best_entry.switch_name,
                switch_id=switch_info.id,
                port_name=best_entry.port_name,
                port_id=switch_iface.id,
                vlan=best_entry.vlan,
                port_classification=port_classification,
                stability_count=stability_count,
                is_stable=is_stable,
            )

        if not is_stable:
            return CorrelationResult(
                mac=mac,
                ipmi_interface=ipmi,
                status=MACStatus.PENDING,
                reason=f"Waiting for stability ({stability_count}/{self.config.stability_runs})",
                switch_name=best_entry.switch_name,
                switch_id=switch_info.id,
                port_name=best_entry.port_name,
                port_id=switch_iface.id,
                vlan=best_entry.vlan,
                port_classification=port_classification,
                stability_count=stability_count,
                is_stable=is_stable,
            )

        return CorrelationResult(
            mac=mac,
            ipmi_interface=ipmi,
            status=MACStatus.PENDING,  # Will be updated to CREATED after cable creation
            reason="Ready for cable creation",
            switch_name=best_entry.switch_name,
            switch_id=switch_info.id,
            port_name=best_entry.port_name,
            port_id=switch_iface.id,
            vlan=best_entry.vlan,
            port_classification=port_classification,
            stability_count=stability_count,
            is_stable=is_stable,
        )

    def _resolve_ambiguity(self, entries: list[FDBEntry]) -> Optional[FDBEntry]:
        """
        Resolve ambiguity when MAC is seen on multiple endpoints.

        Returns single best entry or None if ambiguous.
        """
        if len(entries) == 1:
            return entries[0]

        unique_endpoints = set()
        for entry in entries:
            unique_endpoints.add((entry.switch_name, entry.port_name))

        if len(unique_endpoints) == 1:
            return entries[0]

        if len(unique_endpoints) == 2:
            endpoints = list(unique_endpoints)
            sw1, port1 = endpoints[0]
            sw2, port2 = endpoints[1]

            if port1 == port2 and self._are_mlag_peers(sw1, sw2):
                return next(e for e in entries if e.switch_name == sw1)

        return None

    def _are_mlag_peers(self, switch1: str, switch2: str) -> bool:
        """Check if two switches are MLAG peers."""
        return self._mlag_peer_map.get(switch1) == switch2

    def _check_mismatch(
        self,
        ipmi: IPMIInterface,
        port_to_mac: dict[tuple[str, str], str],
    ) -> Optional[CorrelationResult]:
        """
        Check if MAC on cable's port differs from expected.

        Returns CorrelationResult with MISMATCH status if mismatch detected,
        None otherwise.
        """
        if not ipmi.cable_peer_switch or not ipmi.cable_peer_port:
            return None

        expected_mac = normalize_mac(ipmi.mac_address)
        port_key = (ipmi.cable_peer_switch, ipmi.cable_peer_port)
        actual_mac = port_to_mac.get(port_key)

        if actual_mac is None:
            # MAC not seen on port - could be device offline
            return None

        if actual_mac != expected_mac:
            logger.warning(
                f"MAC MISMATCH: {ipmi.device_name}:{ipmi.interface_name} "
                f"expected {expected_mac} on {ipmi.cable_peer_switch}:{ipmi.cable_peer_port}, "
                f"but found {actual_mac}"
            )
            return CorrelationResult(
                mac=expected_mac,
                ipmi_interface=ipmi,
                status=MACStatus.MISMATCH,
                reason=f"MAC mismatch: expected {expected_mac}, found {actual_mac} on port",
                switch_name=ipmi.cable_peer_switch,
                port_name=ipmi.cable_peer_port,
                expected_mac=expected_mac,
                actual_mac=actual_mac,
            )

        return None

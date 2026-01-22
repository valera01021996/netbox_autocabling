"""NetBox API client for IPMI Auto-Cabling."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class IPMIInterface:
    """IPMI interface data from NetBox."""

    device_id: int
    device_name: str
    interface_id: int
    interface_name: str
    mac_address: str
    has_cable: bool
    site: Optional[str] = None
    rack: Optional[str] = None
    # Cable termination info (if has_cable=True)
    cable_peer_switch: Optional[str] = None
    cable_peer_port: Optional[str] = None


@dataclass
class SwitchInfo:
    """Switch information from NetBox."""

    id: int
    name: str
    primary_ip: Optional[str] = None
    site: Optional[str] = None


@dataclass
class SwitchInterface:
    """Switch interface from NetBox."""

    id: int
    name: str
    device_id: int
    device_name: str
    description: Optional[str] = None
    has_cable: bool = False
    mgmt_only: bool = False


class NetBoxClient:
    """NetBox API client."""

    def __init__(self, config: Config):
        self.config = config
        self.url = config.netbox_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {config.netbox_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.session.verify = config.netbox_verify_ssl

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Execute GET request to API."""
        response = self.session.get(f"{self.url}/api/{endpoint}", params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, data: dict) -> dict:
        """Execute POST request to API."""
        response = self.session.post(f"{self.url}/api/{endpoint}", json=data)
        response.raise_for_status()
        return response.json()

    def _get_all(self, endpoint: str, params: Optional[dict] = None) -> list:
        """Get all results with pagination."""
        params = params or {}
        results = []
        url = f"{self.url}/api/{endpoint}"

        while url:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            params = {}

        return results

    def get_devices_with_oob(self) -> list[IPMIInterface]:
        """
        Get all devices with OOB IP and MAC address on the OOB interface.

        Returns list of IPMIInterface objects with MAC addresses.
        """
        # Получаем все устройства у которых есть oob_ip
        devices = self._get_all("dcim/devices/", params={"has_oob_ip": "true"})

        result = []
        for device in devices:
            oob_ip = device.get("oob_ip")
            if not oob_ip:
                continue

            oob_ip_id = oob_ip.get("id")
            if not oob_ip_id:
                continue

            # Получаем IP адрес чтобы найти интерфейс
            ip_data = self._get(f"ipam/ip-addresses/{oob_ip_id}/")
            assigned_object = ip_data.get("assigned_object")

            if not assigned_object or not assigned_object.get("id"):
                logger.warning(
                    f"Device {device.get('name')}: OOB IP not assigned to interface"
                )
                continue

            # Получаем интерфейс
            interface_id = assigned_object.get("id")
            iface = self._get(f"dcim/interfaces/{interface_id}/")

            mac = iface.get("mac_address")
            if not mac:
                logger.warning(
                    f"Device {device.get('name')}: OOB interface {iface.get('name')} has no MAC"
                )
                continue

            cable = iface.get("cable")
            site_slug = None
            if device.get("site"):
                site_slug = device["site"].get("slug")

            # Get cable peer info if cable exists
            cable_peer_switch = None
            cable_peer_port = None
            if cable:
                link_peers = iface.get("link_peers", [])
                if link_peers:
                    peer = link_peers[0]
                    if peer.get("device"):
                        cable_peer_switch = peer["device"].get("name")
                    cable_peer_port = peer.get("name")

            result.append(IPMIInterface(
                device_id=device.get("id"),
                device_name=device.get("name"),
                interface_id=interface_id,
                interface_name=iface.get("name"),
                mac_address=mac,
                has_cable=cable is not None,
                site=site_slug,
                rack=device.get("rack", {}).get("display") if device.get("rack") else None,
                cable_peer_switch=cable_peer_switch,
                cable_peer_port=cable_peer_port,
            ))
            logger.debug(
                "Found OOB interface",
                extra={
                    "device": device.get("name"),
                    "interface": iface.get("name"),
                    "mac": mac,
                    "oob_ip": oob_ip.get("display"),
                    "site": site_slug,
                    "has_cable": cable is not None,
                }
            )

        logger.info(f"Found {len(result)} devices with OOB IP and MAC addresses")
        return result

    def get_switches_by_site(self, site_slug: str) -> list[SwitchInfo]:
        """Get switches for a specific site."""
        params = {"site__slug": site_slug}
        if self.config.switches_role:
            params["role"] = self.config.switches_role

        devices = self._get_all("dcim/devices/", params=params)
        result = []

        for device in devices:
            primary_ip = device.get("primary_ip")
            ip_address = None
            if primary_ip:
                ip_address = primary_ip.get("address", "").split("/")[0]

            result.append(SwitchInfo(
                id=device.get("id"),
                name=device.get("name"),
                primary_ip=ip_address,
                site=site_slug,
            ))

        return result

    def get_switches(self, sites: Optional[set[str]] = None) -> list[SwitchInfo]:
        """
        Get list of switches to poll for FDB.

        Args:
            sites: Optional set of site slugs to filter by.
                   If None, uses config filters.
        """
        if sites:
            # Получаем коммутаторы для каждого сайта
            result = []
            for site_slug in sites:
                switches = self.get_switches_by_site(site_slug)
                result.extend(switches)
            logger.info(f"Found {len(result)} switches for sites: {sites}")
            return result

        # Fallback на старую логику если sites не указаны
        params = {}
        if self.config.switches_role:
            params["role"] = self.config.switches_role

        if not params:
            logger.warning("No switch filters configured, fetching all devices")

        devices = self._get_all("dcim/devices/", params=params)
        result = []

        for device in devices:
            primary_ip = device.get("primary_ip")
            ip_address = None
            if primary_ip:
                ip_address = primary_ip.get("address", "").split("/")[0]

            result.append(SwitchInfo(
                id=device.get("id"),
                name=device.get("name"),
                primary_ip=ip_address,
                site=device.get("site", {}).get("slug") if device.get("site") else None,
            ))

        logger.info(f"Found {len(result)} switches to poll")
        return result

    def get_switch_interface_by_name(
        self, switch_id: int, interface_name: str
    ) -> Optional[SwitchInterface]:
        """Get switch interface by name."""
        interfaces = self._get_all(
            "dcim/interfaces/",
            params={"device_id": switch_id, "name": interface_name}
        )

        if not interfaces:
            return None

        iface = interfaces[0]
        cable = iface.get("cable")

        return SwitchInterface(
            id=iface.get("id"),
            name=iface.get("name"),
            device_id=switch_id,
            device_name=iface.get("device", {}).get("display") if iface.get("device") else "",
            description=iface.get("description"),
            has_cable=cable is not None,
            mgmt_only=iface.get("mgmt_only", False),
        )

    def get_switch_interface_by_index(
        self, switch_id: int, if_index: int
    ) -> Optional[SwitchInterface]:
        """
        Get switch interface by SNMP ifIndex.

        Note: NetBox stores custom fields, check if ifIndex is available.
        Fallback: get all interfaces and try to match by description or other means.
        """
        interfaces = self._get_all(
            "dcim/interfaces/",
            params={"device_id": switch_id}
        )

        for iface in interfaces:
            custom_fields = iface.get("custom_fields", {})
            if custom_fields.get("if_index") == if_index:
                cable = iface.get("cable")
                return SwitchInterface(
                    id=iface.get("id"),
                    name=iface.get("name"),
                    device_id=switch_id,
                    device_name=iface.get("device", {}).get("display") if iface.get("device") else "",
                    description=iface.get("description"),
                    has_cable=cable is not None,
                    mgmt_only=iface.get("mgmt_only", False),
                )

        return None

    def interface_has_cable(self, interface_id: int) -> bool:
        """Check if interface already has a cable attached."""
        iface = self._get(f"dcim/interfaces/{interface_id}/")
        return iface.get("cable") is not None

    def create_cable(
        self,
        server_interface_id: int,
        switch_interface_id: int,
        vlan: Optional[int] = None,
        label: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Create a cable between server IPMI interface and switch port.

        Returns created cable data or None if dry_run.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        description_parts = [
            "autocabling:ipmi",
            f"source=fdb",
            f"created={timestamp}",
        ]
        if vlan:
            description_parts.append(f"vlan={vlan}")

        description = " | ".join(description_parts)

        cable_data = {
            "a_terminations": [
                {
                    "object_type": "dcim.interface",
                    "object_id": server_interface_id,
                }
            ],
            "b_terminations": [
                {
                    "object_type": "dcim.interface",
                    "object_id": switch_interface_id,
                }
            ],
            "status": self.config.cable_status,
            "description": description,
        }

        if label:
            cable_data["label"] = label

        if self.config.dry_run:
            logger.info(
                "DRY RUN: Would create cable",
                extra={
                    "server_interface_id": server_interface_id,
                    "switch_interface_id": switch_interface_id,
                    "description": description,
                }
            )
            return None

        try:
            result = self._post("dcim/cables/", cable_data)
            logger.info(
                "Created cable",
                extra={
                    "cable_id": result.get("id"),
                    "server_interface_id": server_interface_id,
                    "switch_interface_id": switch_interface_id,
                }
            )
            return result
        except requests.HTTPError as e:
            logger.error(
                "Failed to create cable",
                extra={
                    "error": str(e),
                    "server_interface_id": server_interface_id,
                    "switch_interface_id": switch_interface_id,
                }
            )
            raise

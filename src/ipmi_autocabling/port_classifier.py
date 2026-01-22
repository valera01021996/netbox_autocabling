"""Port classification (access vs trunk/uplink)."""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


class PortType(Enum):
    """Port classification type."""

    ACCESS = "access"
    TRUNK = "trunk"
    UPLINK = "uplink"
    LAG_MEMBER = "lag_member"
    UNKNOWN = "unknown"


@dataclass
class PortClassification:
    """Result of port classification."""

    port_name: str
    port_type: PortType
    reason: str
    is_allowed: bool  # True if cable can be created on this port


class PortClassifier:
    """Classify switch ports as access or trunk/uplink."""

    def __init__(self, config: Config):
        self.config = config
        self._uplink_pattern = config.get_uplink_pattern()

    def classify(
        self,
        port_name: str,
        port_description: Optional[str] = None,
        is_lag_member: bool = False,
        lldp_neighbor_is_switch: bool = False,
    ) -> PortClassification:
        """
        Classify a port.

        Args:
            port_name: Interface name (e.g., "Ethernet1", "Gi0/1")
            port_description: Interface description/alias
            is_lag_member: True if port is member of LAG/Port-Channel
            lldp_neighbor_is_switch: True if LLDP neighbor is a switch

        Returns:
            PortClassification with type and reason
        """
        if port_name in self.config.uplink_ports:
            return PortClassification(
                port_name=port_name,
                port_type=PortType.UPLINK,
                reason=f"Port in uplink list: {port_name}",
                is_allowed=False,
            )

        if port_description and self._uplink_pattern.search(port_description):
            match = self._uplink_pattern.search(port_description)
            return PortClassification(
                port_name=port_name,
                port_type=PortType.UPLINK,
                reason=f"Description matches uplink pattern: '{match.group()}'",
                is_allowed=False,
            )

        if self._uplink_pattern.search(port_name):
            match = self._uplink_pattern.search(port_name)
            return PortClassification(
                port_name=port_name,
                port_type=PortType.UPLINK,
                reason=f"Port name matches uplink pattern: '{match.group()}'",
                is_allowed=False,
            )

        if is_lag_member:
            return PortClassification(
                port_name=port_name,
                port_type=PortType.LAG_MEMBER,
                reason="Port is LAG member",
                is_allowed=False,
            )

        if lldp_neighbor_is_switch:
            return PortClassification(
                port_name=port_name,
                port_type=PortType.UPLINK,
                reason="LLDP neighbor is a switch",
                is_allowed=False,
            )

        return PortClassification(
            port_name=port_name,
            port_type=PortType.ACCESS,
            reason="No uplink/trunk indicators found",
            is_allowed=True,
        )

    def is_access_port(
        self,
        port_name: str,
        port_description: Optional[str] = None,
        is_lag_member: bool = False,
        lldp_neighbor_is_switch: bool = False,
    ) -> bool:
        """Quick check if port is classified as access."""
        classification = self.classify(
            port_name, port_description, is_lag_member, lldp_neighbor_is_switch
        )
        return classification.is_allowed

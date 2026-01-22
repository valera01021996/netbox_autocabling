"""Tests for port classifier."""

import pytest

from src.ipmi_autocabling.config import Config
from src.ipmi_autocabling.port_classifier import PortClassifier, PortType


@pytest.fixture
def config():
    cfg = Config()
    cfg.uplink_ports = ["Ethernet49", "Ethernet50", "Ethernet51", "Ethernet52"]
    cfg.uplink_patterns = [
        r"uplink",
        r"to[-_]?spine",
        r"trunk",
        r"peer",
        r"mlag",
        r"lag",
        r"^po\d+",
        r"port[-_]?channel",
    ]
    return cfg


@pytest.fixture
def classifier(config):
    return PortClassifier(config)


class TestPortClassifier:
    def test_access_port(self, classifier):
        result = classifier.classify("Ethernet1")
        assert result.port_type == PortType.ACCESS
        assert result.is_allowed is True

    def test_uplink_by_name(self, classifier):
        result = classifier.classify("Ethernet49")
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False
        assert "uplink list" in result.reason

    def test_uplink_by_description(self, classifier):
        result = classifier.classify("Ethernet10", port_description="uplink to spine01")
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False
        assert "uplink" in result.reason.lower()

    def test_trunk_by_description(self, classifier):
        result = classifier.classify("Ethernet10", port_description="trunk port")
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False

    def test_lag_member(self, classifier):
        result = classifier.classify("Ethernet10", is_lag_member=True)
        assert result.port_type == PortType.LAG_MEMBER
        assert result.is_allowed is False

    def test_lldp_neighbor_switch(self, classifier):
        result = classifier.classify("Ethernet10", lldp_neighbor_is_switch=True)
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False
        assert "LLDP" in result.reason

    def test_port_channel_name(self, classifier):
        result = classifier.classify("Po1")
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False

    def test_mlag_description(self, classifier):
        result = classifier.classify("Ethernet10", port_description="mlag peer-link")
        assert result.port_type == PortType.UPLINK
        assert result.is_allowed is False

    def test_is_access_port_helper(self, classifier):
        assert classifier.is_access_port("Ethernet1") is True
        assert classifier.is_access_port("Ethernet49") is False
        assert classifier.is_access_port("Ethernet10", port_description="uplink") is False

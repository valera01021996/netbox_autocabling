"""Tests for MAC utilities."""

import pytest

from src.ipmi_autocabling.mac_utils import (
    normalize_mac,
    mac_to_oid_suffix,
    oid_suffix_to_mac,
)


class TestNormalizeMac:
    def test_colon_format(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_dash_format(self):
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_cisco_format(self):
        assert normalize_mac("AABB.CCDD.EEFF") == "aa:bb:cc:dd:ee:ff"

    def test_no_separator(self):
        assert normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"

    def test_with_whitespace(self):
        assert normalize_mac("  AA:BB:CC:DD:EE:FF  ") == "aa:bb:cc:dd:ee:ff"

    def test_empty_string(self):
        assert normalize_mac("") == ""

    def test_invalid_length(self):
        with pytest.raises(ValueError):
            normalize_mac("AA:BB:CC")

    def test_invalid_characters(self):
        with pytest.raises(ValueError):
            normalize_mac("GG:HH:II:JJ:KK:LL")


class TestMacToOidSuffix:
    def test_conversion(self):
        assert mac_to_oid_suffix("aa:bb:cc:dd:ee:ff") == "170.187.204.221.238.255"
        assert mac_to_oid_suffix("00:00:00:00:00:00") == "0.0.0.0.0.0"
        assert mac_to_oid_suffix("ff:ff:ff:ff:ff:ff") == "255.255.255.255.255.255"


class TestOidSuffixToMac:
    def test_conversion(self):
        assert oid_suffix_to_mac("170.187.204.221.238.255") == "aa:bb:cc:dd:ee:ff"
        assert oid_suffix_to_mac("0.0.0.0.0.0") == "00:00:00:00:00:00"
        assert oid_suffix_to_mac("255.255.255.255.255.255") == "ff:ff:ff:ff:ff:ff"

    def test_invalid_suffix(self):
        with pytest.raises(ValueError):
            oid_suffix_to_mac("1.2.3")

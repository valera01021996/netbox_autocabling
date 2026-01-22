"""MAC address utilities."""

import re


def normalize_mac(mac: str) -> str:
    """
    Normalize MAC address to lowercase colon-separated format.

    Supported input formats:
    - AA:BB:CC:DD:EE:FF
    - AA-BB-CC-DD-EE-FF
    - AABB.CCDD.EEFF (Cisco)
    - AABBCCDDEEFF

    Returns:
        Normalized MAC in format aa:bb:cc:dd:ee:ff
    """
    if not mac:
        return ""

    mac_clean = re.sub(r"[:\-\.]", "", mac.strip().lower())

    if len(mac_clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")

    if not re.match(r"^[0-9a-f]{12}$", mac_clean):
        raise ValueError(f"Invalid MAC address: {mac}")

    return ":".join(mac_clean[i : i + 2] for i in range(0, 12, 2))


def mac_to_oid_suffix(mac: str) -> str:
    """
    Convert MAC address to SNMP OID suffix.

    Example: aa:bb:cc:dd:ee:ff -> 170.187.204.221.238.255
    """
    normalized = normalize_mac(mac)
    octets = normalized.split(":")
    return ".".join(str(int(octet, 16)) for octet in octets)


def oid_suffix_to_mac(oid_suffix: str) -> str:
    """
    Convert SNMP OID suffix to MAC address.

    Example: 170.187.204.221.238.255 -> aa:bb:cc:dd:ee:ff
    """
    parts = oid_suffix.split(".")
    if len(parts) != 6:
        raise ValueError(f"Invalid OID suffix: {oid_suffix}")

    octets = [f"{int(p):02x}" for p in parts]
    return ":".join(octets)

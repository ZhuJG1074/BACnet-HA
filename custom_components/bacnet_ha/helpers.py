"""Helper functions for the BACnet-HA integration."""

from __future__ import annotations

import re


def mask_address(address: str | None) -> str:
    """Return a displayable form of the BACnet address."""
    if address is None:
        return "(none)"
    # For display/logging only; never used as a network target
    return str(address)


def is_numeric(value: Any) -> bool:
    """Return True if a value can be meaningfully compared as a number."""
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(re.match(r"^-?\d+(\.\d+)?$", value.strip()))
    return False

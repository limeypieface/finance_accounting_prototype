"""Serialization firewall: ensure no non-JSON-safe object reaches persistence/export."""

from tests.serialization.firewall import (
    assert_json_safe,
    SerializationFirewallError,
)

__all__ = [
    "assert_json_safe",
    "SerializationFirewallError",
]

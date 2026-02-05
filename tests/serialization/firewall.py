"""
Serialization firewall: validate that values are JSON-serializable before persistence.

At persistence/export points we wrap with json.dumps then json.loads.
If any test stores or emits a non-JSON-safe object, fail immediately with the field path.
"""

from __future__ import annotations

import json
from typing import Any

from finance_kernel.exceptions import FinanceKernelError


class SerializationFirewallError(FinanceKernelError):
    """Raised when a non-JSON-safe value is about to be persisted or exported (R18)."""

    code: str = "SERIALIZATION_FIREWALL"

    def __init__(self, field_path: str, detail: str):
        self.field_path = field_path
        self.detail = detail
        super().__init__(f"Non-JSON-safe at {field_path}: {detail}")


def assert_json_safe(value: Any, field_path: str = "root") -> None:
    """
    Raise SerializationFirewallError if value is not JSON-serializable.

    Uses json.dumps then json.loads. On failure, raises with field_path and the
    underlying exception message (e.g. "Object of type Currency is not JSON serializable").
    """
    try:
        serialized = json.dumps(value)
        json.loads(serialized)
    except (TypeError, ValueError) as e:
        raise SerializationFirewallError(field_path, str(e)) from e

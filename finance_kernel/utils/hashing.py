"""
Deterministic hashing utilities.

All hashing in the finance kernel must be deterministic and reproducible.
This module provides the canonical hashing functions used throughout.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def _json_serializer(obj: Any) -> Any:
    """
    Custom JSON serializer for types not natively supported.

    Args:
        obj: Object to serialize.

    Returns:
        JSON-serializable representation.

    Raises:
        TypeError: If object type is not supported.
    """
    if isinstance(obj, Decimal):
        # Normalize Decimal to string representation
        # Remove trailing zeros for consistency
        return str(obj.normalize())
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()

    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def canonicalize_json(data: dict | list | Any) -> str:
    """
    Convert data to canonical JSON string.

    Produces a deterministic JSON representation:
    - Keys are sorted alphabetically
    - No whitespace
    - Consistent handling of special types (Decimal, datetime, UUID)

    Args:
        data: Data to canonicalize.

    Returns:
        Canonical JSON string.
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_serializer,
    )


def hash_payload(payload: dict) -> str:
    """
    Compute SHA-256 hash of a payload.

    Args:
        payload: Dictionary payload to hash.

    Returns:
        Hex-encoded SHA-256 hash (64 characters).
    """
    canonical = canonicalize_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_audit_event(
    entity_type: str,
    entity_id: str,
    action: str,
    payload_hash: str,
    prev_hash: str | None,
) -> str:
    """
    Compute hash for an audit event.

    The hash includes all key fields plus the previous event's hash,
    creating a tamper-evident chain.

    Args:
        entity_type: Type of entity being audited.
        entity_id: ID of the entity.
        action: Action being recorded.
        payload_hash: Hash of the event payload.
        prev_hash: Hash of the previous audit event (None for genesis).

    Returns:
        Hex-encoded SHA-256 hash.
    """
    # Concatenate fields with separator
    components = [
        entity_type,
        str(entity_id),
        action,
        payload_hash,
        prev_hash or "GENESIS",
    ]
    data = "|".join(components)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def hash_journal_entry(
    entry_id: str,
    lines: list[dict],
) -> str:
    """
    Compute deterministic hash of a journal entry and its lines.

    Used for replay validation and determinism checking.

    Args:
        entry_id: Journal entry ID.
        lines: List of line dictionaries with account_id, side, amount, currency.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    # Sort lines by line_seq for deterministic ordering
    sorted_lines = sorted(lines, key=lambda x: x.get("line_seq", 0))

    data = {
        "entry_id": str(entry_id),
        "lines": sorted_lines,
    }

    return hash_payload(data)


def hash_trial_balance(rows: list[dict]) -> str:
    """
    Compute hash of a trial balance for determinism verification.

    Args:
        rows: List of trial balance rows with account_id, currency, debit, credit.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    # Sort by account_id, then currency for deterministic ordering
    sorted_rows = sorted(rows, key=lambda x: (x.get("account_id", ""), x.get("currency", "")))

    return hash_payload({"trial_balance": sorted_rows})


def hash_trace_bundle(bundle_dict: dict) -> str:
    """
    Compute deterministic hash of a trace bundle.

    Excludes volatile fields that vary between generations:
    - generated_at (timestamp varies per generation)
    - trace_id (random UUID per generation)
    - integrity.bundle_hash (would be circular)

    Args:
        bundle_dict: Bundle as a dictionary.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    cleaned = {k: v for k, v in bundle_dict.items()
               if k not in ("generated_at", "trace_id")}
    if isinstance(cleaned.get("integrity"), dict):
        cleaned["integrity"] = {k: v for k, v in cleaned["integrity"].items()
                                if k != "bundle_hash"}
    return hash_payload(cleaned)

"""
Idempotency key generation utilities.

Idempotency keys ensure that the same event always produces the same
journal entry, even under retries and concurrent processing.
"""

from uuid import UUID


def generate_idempotency_key(
    producer: str,
    event_type: str,
    event_id: UUID | str,
) -> str:
    """
    Generate an idempotency key for an event.

    Format: producer:event_type:event_id

    This key is used to ensure exactly-once posting semantics.
    The key is stored on the JournalEntry and has a unique constraint.

    Args:
        producer: Module or system that produced the event.
        event_type: Namespaced event type.
        event_id: Globally unique event identifier.

    Returns:
        Idempotency key string.

    Example:
        >>> generate_idempotency_key("inventory", "receipt.completed", uuid)
        "inventory:receipt.completed:550e8400-e29b-41d4-a716-446655440000"
    """
    return f"{producer}:{event_type}:{event_id}"


def parse_idempotency_key(key: str) -> tuple[str, str, str]:
    """
    Parse an idempotency key into its components.

    Args:
        key: Idempotency key string.

    Returns:
        Tuple of (producer, event_type, event_id).

    Raises:
        ValueError: If key format is invalid.
    """
    parts = key.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid idempotency key format: {key}")
    return parts[0], parts[1], parts[2]

"""
Helper to print the full audit trace for an event when running tests.

Uses the same renderer as interactive.py so the trace looks and feels identical.
Run pytest with -s so output is not captured.

Usage in a test::

    from tests.trace.show_trace import show_trace_for_event

    def test_record_invoice(session, ar_service, ...):
        result = ar_service.record_invoice(...)
        assert result.is_success
        # Print trace (visible when run with pytest -s)
        show_trace_for_event(session, result.event_id)

Then run::

    pytest tests/modules/test_ar_service.py -s -k test_record_invoice

Or use the show_trace fixture and call show_trace(event_id), or run pytest with -T -s (or SHOW_TRACE=1)
for automatic trace after each test that posts (see conftest.py).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session


def show_trace_for_event(session: Session, event_id: UUID) -> None:
    """Print the full audit trace for the given event_id (same format as interactive.py).

    Uses scripts.trace_render.render_trace so output is identical to the
    interactive script. Run pytest with -s so this output is visible.
    """
    import sys
    from pathlib import Path

    # Ensure project root is on path so "scripts.trace_render" resolves
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from scripts.trace_render import render_trace

    render_trace(session, event_id)

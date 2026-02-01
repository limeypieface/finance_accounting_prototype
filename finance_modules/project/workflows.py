"""Project Accounting Workflows."""

from __future__ import annotations

PROJECT_WORKFLOW = {
    "name": "project_lifecycle",
    "states": ["planning", "active", "on_hold", "completed", "cancelled"],
    "transitions": {
        "planning": ["active", "cancelled"],
        "active": ["on_hold", "completed", "cancelled"],
        "on_hold": ["active", "cancelled"],
    },
}

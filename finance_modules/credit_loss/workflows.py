"""Credit Loss Workflows."""

from __future__ import annotations

CREDIT_LOSS_WORKFLOW = {
    "name": "credit_loss_lifecycle",
    "states": ["estimated", "provisioned", "adjusted", "written_off", "recovered"],
    "transitions": {
        "estimated": ["provisioned"],
        "provisioned": ["adjusted", "written_off"],
        "adjusted": ["written_off"],
        "written_off": ["recovered"],
    },
}

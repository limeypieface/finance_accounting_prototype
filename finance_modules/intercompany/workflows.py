"""Intercompany module workflows."""

from __future__ import annotations

IC_TRANSACTION_WORKFLOW = {
    "name": "ic_transaction",
    "states": ["INITIATED", "POSTED", "ELIMINATED", "RECONCILED"],
    "transitions": [
        {"from": "INITIATED", "to": "POSTED", "trigger": "post"},
        {"from": "POSTED", "to": "ELIMINATED", "trigger": "eliminate"},
        {"from": "POSTED", "to": "RECONCILED", "trigger": "reconcile"},
    ],
}

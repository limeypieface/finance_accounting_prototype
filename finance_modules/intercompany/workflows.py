"""Intercompany module workflows."""

from __future__ import annotations

from finance_kernel.domain.workflow import Guard, Transition, Workflow


def _ic_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for IC actions."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


IC_POST_TRANSFER_WORKFLOW = _ic_draft_posted("ic_post_transfer", "Post IC transfer")
IC_GENERATE_ELIMINATIONS_WORKFLOW = _ic_draft_posted("ic_generate_eliminations", "Generate IC eliminations")
IC_POST_TRANSFER_PRICING_ADJUSTMENT_WORKFLOW = _ic_draft_posted(
    "ic_post_transfer_pricing_adjustment", "Post transfer pricing adjustment"
)


IC_TRANSACTION_WORKFLOW = {
    "name": "ic_transaction",
    "states": ["INITIATED", "POSTED", "ELIMINATED", "RECONCILED"],
    "transitions": [
        {"from": "INITIATED", "to": "POSTED", "trigger": "post"},
        {"from": "POSTED", "to": "ELIMINATED", "trigger": "eliminate"},
        {"from": "POSTED", "to": "RECONCILED", "trigger": "reconcile"},
    ],
}

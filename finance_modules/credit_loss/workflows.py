"""Credit Loss Workflows."""

from __future__ import annotations

from finance_kernel.domain.workflow import Guard, Transition, Workflow


def _credit_loss_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for credit loss actions."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


CREDIT_LOSS_RECORD_PROVISION_WORKFLOW = _credit_loss_draft_posted(
    "credit_loss_record_provision", "Record provision"
)
CREDIT_LOSS_ADJUST_PROVISION_WORKFLOW = _credit_loss_draft_posted(
    "credit_loss_adjust_provision", "Adjust provision"
)
CREDIT_LOSS_RECORD_WRITE_OFF_WORKFLOW = _credit_loss_draft_posted(
    "credit_loss_record_write_off", "Record write-off"
)
CREDIT_LOSS_RECORD_RECOVERY_WORKFLOW = _credit_loss_draft_posted(
    "credit_loss_record_recovery", "Record recovery"
)


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

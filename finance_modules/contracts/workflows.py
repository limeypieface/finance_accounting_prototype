"""Contract Workflows.

Contract lifecycle state machine for tracking contract status
from creation through closeout. Action-specific workflows (R28) for
each posting method.
"""

from enum import Enum

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.contracts.workflows")


def _contracts_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for contracts posting actions (R28)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
CONTRACTS_RECORD_COST_INCURRENCE_WORKFLOW = _contracts_draft_posted(
    "contracts_record_cost_incurrence", "Record cost incurrence"
)
CONTRACTS_RECORD_FUNDING_WORKFLOW = _contracts_draft_posted(
    "contracts_record_funding", "Record funding action"
)
CONTRACTS_RECORD_INDIRECT_ALLOCATION_WORKFLOW = _contracts_draft_posted(
    "contracts_record_indirect_allocation", "Record indirect allocation"
)
CONTRACTS_RECORD_RATE_ADJUSTMENT_WORKFLOW = _contracts_draft_posted(
    "contracts_record_rate_adjustment", "Record rate adjustment"
)
CONTRACTS_RECORD_FEE_ACCRUAL_WORKFLOW = _contracts_draft_posted(
    "contracts_record_fee_accrual", "Record fee accrual"
)
CONTRACTS_RECORD_MODIFICATION_WORKFLOW = _contracts_draft_posted(
    "contracts_record_modification", "Record contract modification"
)
CONTRACTS_RECORD_SUBCONTRACT_COST_WORKFLOW = _contracts_draft_posted(
    "contracts_record_subcontract_cost", "Record subcontract cost"
)
CONTRACTS_RECORD_EQUITABLE_ADJUSTMENT_WORKFLOW = _contracts_draft_posted(
    "contracts_record_equitable_adjustment", "Record equitable adjustment"
)
CONTRACTS_RECORD_COST_DISALLOWANCE_WORKFLOW = _contracts_draft_posted(
    "contracts_record_cost_disallowance", "Record cost disallowance"
)
CONTRACTS_RECORD_INDIRECT_RATE_WORKFLOW = _contracts_draft_posted(
    "contracts_record_indirect_rate", "Record indirect rate"
)
CONTRACTS_GENERATE_BILLING_WORKFLOW = _contracts_draft_posted(
    "contracts_generate_billing", "Generate contract billing"
)
CONTRACTS_VERIFY_LABOR_RATE_WORKFLOW = _contracts_draft_posted(
    "contracts_verify_labor_rate", "Verify and record labor rate"
)
CONTRACTS_RATE_RECONCILIATION_WORKFLOW = _contracts_draft_posted(
    "contracts_rate_reconciliation", "Fiscal year rate reconciliation"
)


class ContractLifecycleState(Enum):
    """
    Contract lifecycle states.

    DRAFT      — Contract is being prepared, not yet executed.
    ACTIVE     — Contract is fully executed and costs may be incurred.
    MODIFIED   — Contract has been modified (e.g., scope change, funding increase).
    COMPLETED  — All deliverables accepted; no new costs may be incurred.
    TERMINATED — Contract terminated early (for convenience or default).
    CLOSEOUT   — Final audit, rate adjustments, and de-obligation in progress.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    MODIFIED = "modified"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    CLOSEOUT = "closeout"


logger.info(
    "contract_lifecycle_states_defined",
    extra={
        "states": [s.value for s in ContractLifecycleState],
        "state_count": len(ContractLifecycleState),
    },
)

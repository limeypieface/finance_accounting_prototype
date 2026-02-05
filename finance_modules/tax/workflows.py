"""Tax Workflows.

State machine for tax return processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.tax.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

PERIOD_CLOSED = Guard(
    name="period_closed",
    description="Reporting period is closed",
)

RECONCILED = Guard(
    name="reconciled",
    description="Tax transactions reconciled to GL",
)

REVIEWED = Guard(
    name="reviewed",
    description="Return has been reviewed",
)

logger.info(
    "tax_workflow_guards_defined",
    extra={
        "guards": [
            PERIOD_CLOSED.name,
            RECONCILED.name,
            REVIEWED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Tax Return Workflow
# -----------------------------------------------------------------------------

TAX_RETURN_WORKFLOW = Workflow(
    name="tax_return",
    description="Tax return lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "calculated",
        "reviewed",
        "filed",
        "paid",
        "amended",
    ),
    transitions=(
        Transition("draft", "calculated", action="calculate", guard=PERIOD_CLOSED),
        Transition("calculated", "reviewed", action="review", guard=RECONCILED),
        Transition("reviewed", "filed", action="file", guard=REVIEWED),
        Transition("filed", "paid", action="pay", posts_entry=True),
        Transition("filed", "amended", action="amend"),
        Transition("amended", "filed", action="refile"),
    ),
)

def _tax_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for tax posting actions (R28)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
TAX_RECORD_OBLIGATION_WORKFLOW = _tax_draft_posted("tax_record_obligation", "Record tax obligation")
TAX_RECORD_PAYMENT_WORKFLOW = _tax_draft_posted("tax_record_payment", "Record tax payment")
TAX_RECORD_VAT_SETTLEMENT_WORKFLOW = _tax_draft_posted("tax_record_vat_settlement", "Record VAT settlement")
TAX_RECORD_DEFERRED_ASSET_WORKFLOW = _tax_draft_posted("tax_record_deferred_asset", "Record deferred tax asset")
TAX_RECORD_DEFERRED_LIABILITY_WORKFLOW = _tax_draft_posted("tax_record_deferred_liability", "Record deferred tax liability")
TAX_RECORD_MULTI_JURISDICTION_WORKFLOW = _tax_draft_posted("tax_record_multi_jurisdiction", "Record multi-jurisdiction tax")
TAX_RECORD_ADJUSTMENT_WORKFLOW = _tax_draft_posted("tax_record_adjustment", "Record tax adjustment")

logger.info(
    "tax_return_workflow_registered",
    extra={
        "workflow_name": TAX_RETURN_WORKFLOW.name,
        "state_count": len(TAX_RETURN_WORKFLOW.states),
        "transition_count": len(TAX_RETURN_WORKFLOW.transitions),
        "initial_state": TAX_RETURN_WORKFLOW.initial_state,
    },
)

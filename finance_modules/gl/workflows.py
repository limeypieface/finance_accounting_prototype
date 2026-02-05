"""General Ledger Workflows.

State machine for period close process.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.gl.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

ALL_SUBLEDGERS_CLOSED = Guard(
    name="all_subledgers_closed",
    description="All subledger periods are closed",
)

TRIAL_BALANCE_BALANCED = Guard(
    name="trial_balance_balanced",
    description="Trial balance debits equal credits",
)

ADJUSTMENTS_POSTED = Guard(
    name="adjustments_posted",
    description="All period-end adjustments are posted",
)

YEAR_END_ENTRIES_POSTED = Guard(
    name="year_end_entries_posted",
    description="Year-end closing entries are posted",
)

logger.info(
    "gl_workflow_guards_defined",
    extra={
        "guards": [
            ALL_SUBLEDGERS_CLOSED.name,
            TRIAL_BALANCE_BALANCED.name,
            ADJUSTMENTS_POSTED.name,
            YEAR_END_ENTRIES_POSTED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Period Close Workflow
# -----------------------------------------------------------------------------

PERIOD_CLOSE_WORKFLOW = Workflow(
    name="gl_period_close",
    description="GL period close process",
    initial_state="open",
    states=(
        "future",
        "open",
        "closing",
        "closed",
        "locked",
    ),
    transitions=(
        Transition("future", "open", action="open_period"),
        Transition("open", "closing", action="begin_close", guard=ALL_SUBLEDGERS_CLOSED),
        Transition("closing", "closed", action="close", guard=TRIAL_BALANCE_BALANCED, posts_entry=True),
        Transition("closed", "open", action="reopen"),  # for adjustments
        Transition("closed", "locked", action="lock", guard=YEAR_END_ENTRIES_POSTED),  # year-end only
    ),
)

logger.info(
    "gl_period_close_workflow_registered",
    extra={
        "workflow_name": PERIOD_CLOSE_WORKFLOW.name,
        "state_count": len(PERIOD_CLOSE_WORKFLOW.states),
        "transition_count": len(PERIOD_CLOSE_WORKFLOW.transitions),
        "initial_state": PERIOD_CLOSE_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

def _gl_draft_posted(name: str, description: str) -> Workflow:
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post"),),
    )

GL_JOURNAL_ENTRY_WORKFLOW = _gl_draft_posted("gl_journal_entry", "Manual journal entry")
GL_ADJUSTMENT_WORKFLOW = _gl_draft_posted("gl_adjustment", "Adjustment entry")
GL_CLOSING_ENTRY_WORKFLOW = _gl_draft_posted("gl_closing_entry", "Period closing entry")
GL_INTERCOMPANY_TRANSFER_WORKFLOW = _gl_draft_posted("gl_intercompany_transfer", "Intercompany transfer")
GL_DIVIDEND_DECLARED_WORKFLOW = _gl_draft_posted("gl_dividend_declared", "Dividend declared")
GL_DEFERRED_REVENUE_RECOGNITION_WORKFLOW = _gl_draft_posted(
    "gl_deferred_revenue_recognition", "Deferred revenue recognition"
)
GL_DEFERRED_EXPENSE_RECOGNITION_WORKFLOW = _gl_draft_posted(
    "gl_deferred_expense_recognition", "Deferred expense recognition"
)
GL_FX_UNREALIZED_GAIN_WORKFLOW = _gl_draft_posted("gl_fx_unrealized_gain", "FX unrealized gain")
GL_FX_UNREALIZED_LOSS_WORKFLOW = _gl_draft_posted("gl_fx_unrealized_loss", "FX unrealized loss")
GL_FX_REALIZED_GAIN_WORKFLOW = _gl_draft_posted("gl_fx_realized_gain", "FX realized gain")
GL_FX_REALIZED_LOSS_WORKFLOW = _gl_draft_posted("gl_fx_realized_loss", "FX realized loss")
GL_RECURRING_ENTRY_WORKFLOW = _gl_draft_posted("gl_recurring_entry", "Recurring entry")
GL_RETAINED_EARNINGS_ROLL_WORKFLOW = _gl_draft_posted("gl_retained_earnings_roll", "Retained earnings roll")
GL_CTA_WORKFLOW = _gl_draft_posted("gl_cta", "Cumulative translation adjustment")

_GL_ACTION_WORKFLOWS = (
    GL_JOURNAL_ENTRY_WORKFLOW,
    GL_ADJUSTMENT_WORKFLOW,
    GL_CLOSING_ENTRY_WORKFLOW,
    GL_INTERCOMPANY_TRANSFER_WORKFLOW,
    GL_DIVIDEND_DECLARED_WORKFLOW,
    GL_DEFERRED_REVENUE_RECOGNITION_WORKFLOW,
    GL_DEFERRED_EXPENSE_RECOGNITION_WORKFLOW,
    GL_FX_UNREALIZED_GAIN_WORKFLOW,
    GL_FX_UNREALIZED_LOSS_WORKFLOW,
    GL_FX_REALIZED_GAIN_WORKFLOW,
    GL_FX_REALIZED_LOSS_WORKFLOW,
    GL_RECURRING_ENTRY_WORKFLOW,
    GL_RETAINED_EARNINGS_ROLL_WORKFLOW,
    GL_CTA_WORKFLOW,
)
for wf in _GL_ACTION_WORKFLOWS:
    logger.info(
        "gl_workflow_registered",
        extra={
            "workflow_name": wf.name,
            "state_count": len(wf.states),
            "transition_count": len(wf.transitions),
            "initial_state": wf.initial_state,
        },
    )

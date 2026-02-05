"""Payroll Workflows.

State machine for payroll run processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.payroll.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

ALL_TIMECARDS_APPROVED = Guard(
    name="all_timecards_approved",
    description="All employee timecards are approved",
)

CALCULATION_COMPLETE = Guard(
    name="calculation_complete",
    description="Payroll calculation completed without errors",
)

APPROVAL_OBTAINED = Guard(
    name="approval_obtained",
    description="Required payroll approval obtained",
)

logger.info(
    "payroll_workflow_guards_defined",
    extra={
        "guards": [
            ALL_TIMECARDS_APPROVED.name,
            CALCULATION_COMPLETE.name,
            APPROVAL_OBTAINED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Payroll Run Workflow
# -----------------------------------------------------------------------------

def _payroll_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for payroll actions (no guards)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
PAYROLL_ACCRUAL_WORKFLOW = _payroll_draft_posted(
    "payroll_accrual", "Payroll run accrual (expense + withholding)"
)
PAYROLL_TAX_WORKFLOW = _payroll_draft_posted(
    "payroll_tax", "Payroll tax deposit/remittance"
)
PAYROLL_PAYMENT_WORKFLOW = _payroll_draft_posted(
    "payroll_payment", "Net payroll payment to employees"
)
PAYROLL_BENEFITS_PAYMENT_WORKFLOW = _payroll_draft_posted(
    "payroll_benefits_payment", "Benefits payment to providers"
)
PAYROLL_BENEFITS_DEDUCTION_WORKFLOW = _payroll_draft_posted(
    "payroll_benefits_deduction", "Benefits deduction from pay"
)
PAYROLL_EMPLOYER_CONTRIBUTION_WORKFLOW = _payroll_draft_posted(
    "payroll_employer_contribution", "Employer contribution (e.g. 401k match)"
)
PAYROLL_REGULAR_HOURS_WORKFLOW = _payroll_draft_posted(
    "payroll_regular_hours", "Regular hourly wages"
)
PAYROLL_OVERTIME_WORKFLOW = _payroll_draft_posted(
    "payroll_overtime", "Overtime wages"
)
PAYROLL_PTO_WORKFLOW = _payroll_draft_posted(
    "payroll_pto", "PTO / sick / vacation pay"
)
PAYROLL_FLOOR_CHECK_WORKFLOW = _payroll_draft_posted(
    "payroll_floor_check", "DCAA floor check completed"
)
PAYROLL_LABOR_ALLOCATION_WORKFLOW = _payroll_draft_posted(
    "payroll_labor_allocation", "Labor cost allocation across cost centers"
)

PAYROLL_RUN_WORKFLOW = Workflow(
    name="payroll_run",
    description="Payroll processing lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "calculating",
        "calculated",
        "approved",
        "processing",
        "completed",
        "reversed",
    ),
    transitions=(
        Transition("draft", "calculating", action="calculate", guard=ALL_TIMECARDS_APPROVED),
        Transition("calculating", "calculated", action="finish_calculation", guard=CALCULATION_COMPLETE),
        Transition("calculated", "draft", action="recalculate"),  # changes needed
        Transition("calculated", "approved", action="approve", guard=APPROVAL_OBTAINED),
        Transition("approved", "processing", action="process", posts_entry=True),
        Transition("processing", "completed", action="complete", posts_entry=True),
        Transition("completed", "reversed", action="reverse", posts_entry=True),
    ),
)

logger.info(
    "payroll_run_workflow_registered",
    extra={
        "workflow_name": PAYROLL_RUN_WORKFLOW.name,
        "state_count": len(PAYROLL_RUN_WORKFLOW.states),
        "transition_count": len(PAYROLL_RUN_WORKFLOW.transitions),
        "initial_state": PAYROLL_RUN_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Timesheet Submission Guards (DCAA D1-D5)
# -----------------------------------------------------------------------------

DAILY_RECORDING_VALID = Guard(
    name="daily_recording_valid",
    description="All entries submitted within max retroactive days (D1)",
)

NO_CONCURRENT_OVERLAP = Guard(
    name="no_concurrent_overlap",
    description="No overlapping time charges on same date (D4)",
)

TOTAL_TIME_BALANCED = Guard(
    name="total_time_balanced",
    description="Hours account for expected total within tolerance (D3)",
)

SUPERVISOR_APPROVED = Guard(
    name="supervisor_approved",
    description="Supervisor has approved the timesheet (D2)",
)

REVERSAL_EXISTS = Guard(
    name="reversal_exists",
    description="Correction has reversal event for original entry (D5)",
)

logger.info(
    "timesheet_workflow_guards_defined",
    extra={
        "guards": [
            DAILY_RECORDING_VALID.name,
            NO_CONCURRENT_OVERLAP.name,
            TOTAL_TIME_BALANCED.name,
            SUPERVISOR_APPROVED.name,
            REVERSAL_EXISTS.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Timesheet Submission Workflow (DCAA D1-D5)
# -----------------------------------------------------------------------------

TIMESHEET_WORKFLOW = Workflow(
    name="timesheet",
    description="Timesheet submission and approval lifecycle (DCAA-compliant)",
    initial_state="draft",
    states=(
        "draft",
        "submitted",
        "pending_approval",
        "approved",
        "rejected",
        "correction_pending",
    ),
    transitions=(
        Transition(
            "draft", "submitted",
            action="submit",
            guard=DAILY_RECORDING_VALID,
        ),
        Transition(
            "submitted", "pending_approval",
            action="route_for_approval",
            guard=NO_CONCURRENT_OVERLAP,
        ),
        Transition(
            "pending_approval", "approved",
            action="approve",
            guard=SUPERVISOR_APPROVED,
            posts_entry=True,
        ),
        Transition(
            "pending_approval", "rejected",
            action="reject",
        ),
        Transition(
            "rejected", "draft",
            action="revise",
        ),
        Transition(
            "approved", "correction_pending",
            action="initiate_correction",
            guard=REVERSAL_EXISTS,
        ),
    ),
)

logger.info(
    "timesheet_workflow_registered",
    extra={
        "workflow_name": TIMESHEET_WORKFLOW.name,
        "state_count": len(TIMESHEET_WORKFLOW.states),
        "transition_count": len(TIMESHEET_WORKFLOW.transitions),
        "initial_state": TIMESHEET_WORKFLOW.initial_state,
    },
)

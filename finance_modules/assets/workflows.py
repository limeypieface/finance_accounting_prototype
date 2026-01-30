"""
Fixed Assets Workflows.

State machine for asset lifecycle.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.assets.workflows")


@dataclass(frozen=True)
class Guard:
    """A condition for a transition."""
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    """A valid state transition."""
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False


@dataclass(frozen=True)
class Workflow:
    """A state machine definition."""
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

IN_SERVICE_DATE_SET = Guard(
    name="in_service_date_set",
    description="Asset has an in-service date",
)

FULLY_DEPRECIATED = Guard(
    name="fully_depreciated",
    description="Net book value equals salvage value",
)

DISPOSAL_APPROVED = Guard(
    name="disposal_approved",
    description="Disposal has required approval",
)

logger.info(
    "asset_workflow_guards_defined",
    extra={
        "guards": [
            IN_SERVICE_DATE_SET.name,
            FULLY_DEPRECIATED.name,
            DISPOSAL_APPROVED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Asset Workflow
# -----------------------------------------------------------------------------

ASSET_WORKFLOW = Workflow(
    name="fixed_asset",
    description="Fixed asset lifecycle",
    initial_state="pending",
    states=(
        "pending",
        "in_service",
        "fully_depreciated",
        "disposed",
        "impaired",
    ),
    transitions=(
        Transition("pending", "in_service", action="place_in_service", guard=IN_SERVICE_DATE_SET, posts_entry=True),
        Transition("pending", "disposed", action="dispose", guard=DISPOSAL_APPROVED),  # never used
        Transition("in_service", "fully_depreciated", action="complete_depreciation", guard=FULLY_DEPRECIATED),
        Transition("in_service", "disposed", action="dispose", guard=DISPOSAL_APPROVED, posts_entry=True),
        Transition("in_service", "impaired", action="record_impairment", posts_entry=True),
        Transition("impaired", "disposed", action="dispose", guard=DISPOSAL_APPROVED, posts_entry=True),
        Transition("fully_depreciated", "disposed", action="dispose", guard=DISPOSAL_APPROVED, posts_entry=True),
    ),
)

logger.info(
    "asset_workflow_registered",
    extra={
        "workflow_name": ASSET_WORKFLOW.name,
        "state_count": len(ASSET_WORKFLOW.states),
        "transition_count": len(ASSET_WORKFLOW.transitions),
        "initial_state": ASSET_WORKFLOW.initial_state,
    },
)

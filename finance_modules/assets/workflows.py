"""Fixed Assets Workflows.

State machine for asset lifecycle.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.assets.workflows")


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


def _assets_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for asset actions (no guards)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
ASSETS_RECORD_ACQUISITION_WORKFLOW = _assets_draft_posted(
    "assets_record_acquisition", "Record asset acquisition"
)
ASSETS_RECORD_CIP_CAPITALIZED_WORKFLOW = _assets_draft_posted(
    "assets_record_cip_capitalized", "Capitalize CIP to asset"
)
ASSETS_RECORD_DEPRECIATION_WORKFLOW = _assets_draft_posted(
    "assets_record_depreciation", "Record depreciation"
)
ASSETS_RECORD_DISPOSAL_WORKFLOW = _assets_draft_posted(
    "assets_record_disposal", "Record asset disposal"
)
ASSETS_RECORD_IMPAIRMENT_WORKFLOW = _assets_draft_posted(
    "assets_record_impairment", "Record impairment"
)
ASSETS_RECORD_SCRAP_WORKFLOW = _assets_draft_posted(
    "assets_record_scrap", "Record scrap"
)
ASSETS_RUN_MASS_DEPRECIATION_WORKFLOW = _assets_draft_posted(
    "assets_run_mass_depreciation", "Run mass depreciation"
)
ASSETS_RECORD_ASSET_TRANSFER_WORKFLOW = _assets_draft_posted(
    "assets_record_asset_transfer", "Record asset transfer"
)
ASSETS_RECORD_REVALUATION_WORKFLOW = _assets_draft_posted(
    "assets_record_revaluation", "Record revaluation"
)
ASSETS_RECORD_COMPONENT_DEPRECIATION_WORKFLOW = _assets_draft_posted(
    "assets_record_component_depreciation", "Record component depreciation"
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

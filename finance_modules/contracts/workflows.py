"""
Contract Workflows.

Contract lifecycle state machine for tracking contract status
from creation through closeout.
"""

from enum import Enum

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.contracts.workflows")


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

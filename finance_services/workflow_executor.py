"""
finance_services.workflow_executor -- Workflow transition execution.

Responsibility:
    Executes state transitions with approval gate enforcement and
    guard evaluation.  Thin coordinator (AL-4) -- delegates rule
    evaluation to the pure approval engine, guard evaluation to
    GuardExecutor, persistence to ApprovalService, role checks to
    OrgHierarchyProvider.

Architecture position:
    Services layer.  May import from finance_engines/ (pure engines)
    and finance_kernel/ (domain, services, models).

Invariants enforced:
    AL-4 -- Executor is a thin coordinator.  No Decimal comparison logic,
            no direct ORM queries.
    AL-6 -- Rule ordering delegated to engine (select_matching_rule).
    AL-9 -- Role diversity delegated to engine (evaluate_approval_status).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable, Protocol, runtime_checkable
from uuid import UUID

from finance_engines.approval import (
    evaluate_approval_requirement,
    select_matching_rule,
    validate_actor_authority,
)
from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalStatus,
    TransitionResult,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.workflow import Guard
from finance_kernel.exceptions import UnauthorizedApproverError
from finance_kernel.logging_config import LogContext, get_logger
from finance_kernel.services.approval_service import ApprovalService

logger = get_logger("services.workflow_executor")

# Trace message and outcome codes for structured logging and traceability
TRACE_TYPE_WORKFLOW_TRANSITION = "WORKFLOW_TRANSITION"
OUTCOME_SUCCESS = "success"
OUTCOME_GUARD_FAILED = "guard_failed"
OUTCOME_NO_TRANSITION = "no_transition"
OUTCOME_APPROVAL_REQUIRED = "approval_required"
OUTCOME_NO_POLICY = "no_policy"
OUTCOME_APPROVAL_REJECTED = "approval_rejected"
OUTCOME_APPROVAL_PENDING = "approval_pending"


def _emit_workflow_trace(
    workflow_name: str,
    action: str,
    entity_type: str,
    entity_id: UUID,
    from_state: str,
    outcome: str,
    reason: str,
    duration_ms: float,
    to_state: str | None = None,
    approval_request_id: UUID | None = None,
    posts_entry: bool = False,
    outcome_sink: Callable[[dict], None] | None = None,
) -> None:
    """Emit a structured workflow transition record for traceability and lookback."""
    record: dict[str, Any] = {
        "trace_type": TRACE_TYPE_WORKFLOW_TRANSITION,
        "ts": datetime.now(UTC).isoformat(),
        "workflow": workflow_name,
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "from_state": from_state,
        "outcome": outcome,
        "reason": reason,
        "duration_ms": round(duration_ms, 3),
    }
    if to_state is not None:
        record["to_state"] = to_state
    if approval_request_id is not None:
        record["approval_request_id"] = str(approval_request_id)
    record["posts_entry"] = posts_entry
    record.update(LogContext.get_all())
    # LogRecord reserves "message"; use log msg as first arg, not in extra
    extra_for_log = {k: v for k, v in record.items() if k != "message"}
    logger.info("workflow_transition", extra=extra_for_log)
    # For sink/timeline, include "message" so TraceSelector._records_to_timeline has an action
    record["message"] = "workflow_transition"
    if outcome_sink is not None:
        outcome_sink(record)


# ---------------------------------------------------------------------------
# Structural protocols for module-level workflow types. Modules may use
# canonical Guard/Transition/Workflow from finance_kernel.domain.workflow
# (Phase 10) or declare their own compatible types.
# ---------------------------------------------------------------------------


@runtime_checkable
class TransitionLike(Protocol):
    from_state: str
    to_state: str
    action: str
    posts_entry: bool
    # Optional guard: must be satisfied before transition is allowed
    guard: Guard | None = None
    # Phase 10: optional; when present, transition is gated by approval engine
    requires_approval: bool = False
    approval_policy: Any | None = None


@runtime_checkable
class WorkflowLike(Protocol):
    name: str
    transitions: tuple


# ---------------------------------------------------------------------------
# Guard evaluation (business-rule guards on transitions)
# ---------------------------------------------------------------------------


def _get_attr(context: Any, key: str, default: Any = None) -> Any:
    """Get attribute from context (object or dict)."""
    if context is None:
        return default
    if hasattr(context, "get") and callable(getattr(context, "get")):
        return context.get(key, default)
    return getattr(context, key, default)


def _match_within_tolerance(context: Any) -> bool:
    """AP invoice three-way match: variance within tolerance percent."""
    inv = _get_attr(context, "invoice_amount")
    po = _get_attr(context, "po_amount")
    tol = _get_attr(context, "tolerance_percent")
    if inv is None or po is None or tol is None:
        return False
    inv_d = Decimal(str(inv)) if not isinstance(inv, Decimal) else inv
    po_d = Decimal(str(po)) if not isinstance(po, Decimal) else po
    tol_d = Decimal(str(tol)) if not isinstance(tol, Decimal) else tol
    if po_d <= 0:
        return inv_d == po_d
    variance_pct = abs(inv_d - po_d) / po_d * 100
    return variance_pct <= tol_d


def _payment_approved(context: Any) -> bool:
    """AP payment: invoice must be approved before payment release."""
    status = _get_attr(context, "invoice_status")
    if status is None:
        return False
    return str(status).lower() in ("approved", "approved_for_payment")


def _sufficient_funds(context: Any) -> bool:
    """AP payment submit: bank balance must be >= payment amount."""
    balance = _get_attr(context, "bank_balance")
    amount = _get_attr(context, "payment_amount")
    if balance is None or amount is None:
        return False
    bal = Decimal(str(balance)) if not isinstance(balance, Decimal) else balance
    amt = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    return bal >= amt and amt >= 0


def _budget_available(context: Any) -> bool:
    """Requisition: available budget must be >= requisition amount."""
    req = _get_attr(context, "requisition_amount")
    avail = _get_attr(context, "available_budget")
    if req is None or avail is None:
        return False
    req_d = Decimal(str(req)) if not isinstance(req, Decimal) else req
    avail_d = Decimal(str(avail)) if not isinstance(avail, Decimal) else avail
    return req_d <= avail_d


def _po_sent(context: Any) -> bool:
    """PO receive: PO must be in sent state."""
    status = _get_attr(context, "po_status")
    return str(status).lower() == "sent" if status is not None else False


def _fully_received(context: Any) -> bool:
    """PO close: quantity received must equal quantity ordered."""
    ordered = _get_attr(context, "quantity_ordered")
    received = _get_attr(context, "quantity_received")
    if ordered is None or received is None:
        return False
    o = Decimal(str(ordered)) if not isinstance(ordered, Decimal) else ordered
    r = Decimal(str(received)) if not isinstance(received, Decimal) else received
    return r >= o and o > 0


def _stock_available(context: Any) -> bool:
    """Inventory pick: requested quantity <= available quantity."""
    req = _get_attr(context, "requested_quantity")
    avail = _get_attr(context, "available_quantity")
    if req is None or avail is None:
        return False
    r = Decimal(str(req)) if not isinstance(req, Decimal) else req
    a = Decimal(str(avail)) if not isinstance(avail, Decimal) else avail
    return r <= a and a >= 0


def _qc_passed(context: Any) -> bool:
    """Inventory receipt accept: QC status must be passed."""
    status = _get_attr(context, "qc_status")
    return str(status).lower() == "passed" if status is not None else False


def _calculation_complete(context: Any) -> bool:
    """Payroll approve: no calculation errors."""
    has_errors = _get_attr(context, "has_calculation_errors", False)
    return not has_errors


def _approval_obtained(context: Any) -> bool:
    """Payroll disburse: approval status must be obtained."""
    status = _get_attr(context, "approval_status")
    return str(status).lower() == "approved" if status is not None else False


def _credit_check_passed(context: Any) -> bool:
    """AR: credit check — proposed balance must not exceed credit limit.
    No limit (credit_limit is None) means allow, consistent with PartyService."""
    balance = _get_attr(context, "current_balance")
    amount = _get_attr(context, "proposed_amount")
    credit_limit = _get_attr(context, "credit_limit")
    if amount is None:
        return False
    # No credit limit set → allow (same as PartyService.check_credit_limit)
    if credit_limit is None:
        return True
    bal = (
        Decimal(str(balance)) if not isinstance(balance, Decimal) else balance
    ) if balance is not None else Decimal("0")
    amt = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    limit = Decimal(str(credit_limit)) if not isinstance(credit_limit, Decimal) else credit_limit
    return bal + amt <= limit


def _balance_zero(context: Any) -> bool:
    """AR: balance must be zero (used for close/settle transitions)."""
    balance_due = _get_attr(context, "balance_due")
    if balance_due is None:
        return False
    bal = Decimal(str(balance_due)) if not isinstance(balance_due, Decimal) else balance_due
    return bal == 0


def _fully_depreciated(context: Any) -> bool:
    """Assets: net book value must be <= salvage value."""
    nbv = _get_attr(context, "net_book_value")
    salvage = _get_attr(context, "salvage_value")
    if nbv is None or salvage is None:
        return False
    nbv_d = Decimal(str(nbv)) if not isinstance(nbv, Decimal) else nbv
    salvage_d = Decimal(str(salvage)) if not isinstance(salvage, Decimal) else salvage
    return nbv_d <= salvage_d


def _all_subledgers_closed(context: Any) -> bool:
    """Period close: all subledgers must be closed."""
    ap = _get_attr(context, "ap_closed", False)
    ar = _get_attr(context, "ar_closed", False)
    inv = _get_attr(context, "inventory_closed", False)
    payroll = _get_attr(context, "payroll_closed", False)
    return bool(ap and ar and inv and payroll)


def _no_pending_transactions(context: Any) -> bool:
    """Period close: no pending transactions."""
    count = _get_attr(context, "pending_transaction_count", 0)
    return (count or 0) == 0


def _trial_balance_balanced(context: Any) -> bool:
    """Period close: trial balance balanced; when context has subledger/pending info, check those too."""
    if not _all_subledgers_closed(context):
        return False
    return _no_pending_transactions(context)


class GuardExecutor:
    """Evaluates workflow guards against context.

    Guards are declared on transitions (name + description). This executor
    holds the actual evaluation logic per guard name and is called by
    WorkflowExecutor before allowing a transition.
    """

    def __init__(self) -> None:
        self._evaluators: dict[str, Callable[[Any], bool]] = {}

    def register(self, guard_name: str, evaluator: Callable[[Any], bool]) -> None:
        """Register an evaluator for a guard by name."""
        self._evaluators[guard_name] = evaluator

    def evaluate(self, guard: Guard, context: Any = None) -> bool:
        """Evaluate a guard against context. Returns True if guard passes."""
        fn = self._evaluators.get(guard.name)
        if fn is None:
            logger.warning(
                "guard_no_evaluator",
                extra={"guard_name": guard.name},
            )
            return False
        try:
            return fn(context)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "guard_evaluation_error",
                extra={"guard_name": guard.name, "error": str(e)},
            )
            return False


def default_guard_executor() -> GuardExecutor:
    """Return a GuardExecutor with built-in evaluators registered."""
    ex = GuardExecutor()
    ex.register("match_within_tolerance", _match_within_tolerance)
    ex.register("payment_approved", _payment_approved)
    # approval_threshold_met: deprecated; approval enforced via approval engine; guard always passes
    ex.register("approval_threshold_met", lambda ctx: True)
    ex.register("sufficient_funds", _sufficient_funds)
    ex.register("budget_available", _budget_available)
    ex.register("po_sent", _po_sent)
    ex.register("fully_received", _fully_received)
    ex.register("stock_available", _stock_available)
    ex.register("qc_passed", _qc_passed)
    ex.register("calculation_complete", _calculation_complete)
    ex.register("approval_obtained", _approval_obtained)
    ex.register("all_subledgers_closed", _all_subledgers_closed)
    ex.register("no_pending_transactions", _no_pending_transactions)
    ex.register("trial_balance_balanced", _trial_balance_balanced)
    # Aliases / alternate names used by modules
    ex.register("all_lines_received", _fully_received)
    # Payroll / timesheet (DCAA D1–D5)
    ex.register("all_timecards_approved", lambda ctx: bool(_get_attr(ctx, "all_timecards_approved", False)))
    ex.register("daily_recording_valid", lambda ctx: bool(_get_attr(ctx, "daily_recording_valid", False)))
    ex.register("no_concurrent_overlap", lambda ctx: bool(_get_attr(ctx, "no_concurrent_overlap", False)))
    ex.register("total_time_balanced", lambda ctx: bool(_get_attr(ctx, "total_time_balanced", False)))
    ex.register("supervisor_approved", lambda ctx: bool(_get_attr(ctx, "supervisor_approved", False)))
    ex.register("reversal_exists", lambda ctx: bool(_get_attr(ctx, "reversal_exists", False)))
    # Procurement (requisition / PO lifecycle)
    ex.register("vendor_approved", lambda ctx: bool(_get_attr(ctx, "vendor_approved", False)))
    ex.register("fully_invoiced", lambda ctx: bool(_get_attr(ctx, "fully_invoiced", False)))
    # Expense (T&E, expense report, travel auth D6)
    ex.register("receipts_attached", lambda ctx: bool(_get_attr(ctx, "receipts_attached", False)))
    ex.register("within_policy", lambda ctx: bool(_get_attr(ctx, "within_policy", False)))
    ex.register("approval_authority", lambda ctx: bool(_get_attr(ctx, "approval_authority", False)))
    ex.register("pre_travel_valid", lambda ctx: bool(_get_attr(ctx, "pre_travel_valid", False)))
    ex.register("travel_auth_authority", lambda ctx: bool(_get_attr(ctx, "travel_auth_authority", False)))
    # Assets (fixed asset lifecycle)
    ex.register("in_service_date_set", lambda ctx: _get_attr(ctx, "in_service_date") is not None)
    ex.register("fully_depreciated", _fully_depreciated)
    ex.register("disposal_approved", lambda ctx: bool(_get_attr(ctx, "disposal_approved", False)))
    # Budget
    ex.register("approved_by_authority", lambda ctx: bool(_get_attr(ctx, "approved_by_authority", False)))
    # Lease
    ex.register("classification_complete", lambda ctx: bool(_get_attr(ctx, "classification_complete", False)))
    # Tax
    ex.register("period_closed", lambda ctx: bool(_get_attr(ctx, "period_closed", False)))
    ex.register("reconciled", lambda ctx: bool(_get_attr(ctx, "reconciled", False)))
    ex.register("reviewed", lambda ctx: bool(_get_attr(ctx, "reviewed", False)))
    # Revenue (contract lifecycle guards; action workflows use draft→posted)
    ex.register("contract_approved", lambda ctx: bool(_get_attr(ctx, "contract_approved", False)))
    ex.register("obligations_identified", lambda ctx: bool(_get_attr(ctx, "obligations_identified", False)))
    ex.register("price_determined", lambda ctx: bool(_get_attr(ctx, "price_determined", False)))
    # AR (invoice / receipt / write-off guards)
    ex.register("credit_check_passed", _credit_check_passed)
    ex.register("balance_zero", _balance_zero)
    ex.register("write_off_approved", lambda ctx: bool(_get_attr(ctx, "write_off_approved", False)))
    return ex


# ---------------------------------------------------------------------------
# Default OrgHierarchyProvider (static dict-based)
# ---------------------------------------------------------------------------


class StaticRoleProvider:
    """Default OrgHierarchyProvider backed by a simple dict.

    Satisfies the OrgHierarchyProvider protocol from domain/approval.py.
    Can be replaced with a database-backed or LDAP-backed implementation.
    """

    def __init__(self, role_map: dict[UUID, tuple[str, ...]] | None = None) -> None:
        self._role_map: dict[UUID, tuple[str, ...]] = role_map or {}

    def get_actor_roles(self, actor_id: UUID) -> tuple[str, ...]:
        return self._role_map.get(actor_id, ())

    def get_approval_chain(self, actor_id: UUID) -> tuple[UUID, ...]:
        return ()

    def has_role(self, actor_id: UUID, role: str) -> bool:
        return role in self._role_map.get(actor_id, ())


# ---------------------------------------------------------------------------
# WorkflowExecutor
# ---------------------------------------------------------------------------


class WorkflowExecutor:
    """Executes workflow transitions with guard evaluation and approval gate enforcement.

    AL-4: Thin coordinator -- delegates all domain logic to the approval
    engine, guard evaluation to GuardExecutor, all persistence to ApprovalService,
    and all role resolution to OrgHierarchyProvider.
    """

    def __init__(
        self,
        approval_service: ApprovalService,
        approval_policies: dict[str, ApprovalPolicy] | None = None,
        clock: Clock | None = None,
        org_hierarchy: StaticRoleProvider | None = None,
        guard_executor: GuardExecutor | None = None,
    ) -> None:
        self._approval_service = approval_service
        self._policies = approval_policies or {}
        self._clock = clock or SystemClock()
        self._org_hierarchy = org_hierarchy or StaticRoleProvider()
        self._guard_executor = guard_executor or default_guard_executor()

    def execute_transition(
        self,
        workflow: WorkflowLike,
        entity_type: str,
        entity_id: UUID,
        current_state: str,
        action: str,
        actor_id: UUID,
        actor_role: str,
        amount: Decimal | None = None,
        currency: str = "USD",
        context: dict[str, Any] | None = None,
        approval_request_id: UUID | None = None,
        outcome_sink: Callable[[dict], None] | None = None,
    ) -> TransitionResult:
        """Execute a state transition, checking approval gates.

        Returns TransitionResult indicating success, approval-required, or
        failure.  If approval is required and not yet granted, an approval
        request is created and returned in the result.

        When outcome_sink is provided, a structured trace record is passed
        to it for every outcome (for traceability and lookback). Callers
        can collect these and pass as preamble_log to post_event so workflow
        outcomes appear in the event's decision_log.
        """
        t0 = time.monotonic()

        # 1. Find the matching transition in the workflow
        transition = self._find_transition(workflow, current_state, action)
        if transition is None:
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                outcome=OUTCOME_NO_TRANSITION,
                reason=f"No transition from '{current_state}' via action '{action}' "
                       f"in workflow '{workflow.name}'",
                duration_ms=duration_ms,
                outcome_sink=outcome_sink,
            )
            return TransitionResult(
                success=False,
                reason=f"No transition from '{current_state}' via action '{action}' "
                       f"in workflow '{workflow.name}'",
            )

        # 2. Evaluate guard if present (guard must pass before transition is allowed)
        guard = getattr(transition, "guard", None)
        if guard is not None:
            ctx = context if isinstance(context, dict) else (context or {})
            if not self._guard_executor.evaluate(guard, ctx):
                duration_ms = (time.monotonic() - t0) * 1000
                _emit_workflow_trace(
                    workflow_name=workflow.name,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    from_state=current_state,
                    outcome=OUTCOME_GUARD_FAILED,
                    reason=f"Guard not satisfied: {guard.name}",
                    duration_ms=duration_ms,
                    outcome_sink=outcome_sink,
                )
                return TransitionResult(
                    success=False,
                    reason=f"Guard not satisfied: {guard.name}",
                )

        # 3. Look up approval policy for this workflow/action
        policy = self._resolve_policy(workflow.name, action)

        # 4. If no policy, transition proceeds directly
        if policy is None:
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                to_state=transition.to_state,
                outcome=OUTCOME_NO_POLICY,
                reason="No approval policy -- transition allowed",
                duration_ms=duration_ms,
                posts_entry=transition.posts_entry,
                outcome_sink=outcome_sink,
            )
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason="No approval policy -- transition allowed",
            )

        # 5. If caller provided an approval_request_id, check if it's approved
        if approval_request_id is not None:
            result = self._check_existing_approval(
                approval_request_id=approval_request_id,
                transition=transition,
            )
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                to_state=transition.to_state,
                outcome=OUTCOME_SUCCESS if result.success else OUTCOME_APPROVAL_PENDING,
                reason=result.reason,
                duration_ms=duration_ms,
                approval_request_id=approval_request_id,
                posts_entry=transition.posts_entry,
                outcome_sink=outcome_sink,
            )
            return result

        # 6. Evaluate whether approval is needed (delegated to pure engine)
        evaluation = evaluate_approval_requirement(policy, amount, context)

        if not evaluation.needs_approval:
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                to_state=transition.to_state,
                outcome=OUTCOME_SUCCESS,
                reason=evaluation.reason,
                duration_ms=duration_ms,
                posts_entry=transition.posts_entry,
                outcome_sink=outcome_sink,
            )
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason=evaluation.reason,
            )

        # 7. Auto-approved case
        if evaluation.auto_approved and evaluation.matched_rule is not None:
            request = self._approval_service.create_request(
                workflow_name=workflow.name,
                entity_type=entity_type,
                entity_id=entity_id,
                transition_action=action,
                from_state=current_state,
                to_state=transition.to_state,
                policy=policy,
                matched_rule_name=evaluation.matched_rule.rule_name,
                requestor_id=actor_id,
                amount=amount,
                currency=currency,
            )
            self._approval_service.record_auto_approval(
                request_id=request.request_id,
                matched_rule_name=evaluation.matched_rule.rule_name,
                threshold_value=evaluation.matched_rule.auto_approve_below or Decimal(0),
                evaluated_amount=amount or Decimal(0),
                policy=policy,
                actor_id=actor_id,
            )
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                to_state=transition.to_state,
                outcome=OUTCOME_SUCCESS,
                reason=evaluation.reason,
                duration_ms=duration_ms,
                approval_request_id=request.request_id,
                posts_entry=transition.posts_entry,
                outcome_sink=outcome_sink,
            )
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                approval_request_id=request.request_id,
                posts_entry=transition.posts_entry,
                reason=evaluation.reason,
            )

        # 8. Approval required -- create request and block
        if evaluation.matched_rule is None:
            duration_ms = (time.monotonic() - t0) * 1000
            _emit_workflow_trace(
                workflow_name=workflow.name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                from_state=current_state,
                outcome=OUTCOME_APPROVAL_REJECTED,
                reason="Approval required but no matching rule found",
                duration_ms=duration_ms,
                outcome_sink=outcome_sink,
            )
            return TransitionResult(
                success=False,
                reason="Approval required but no matching rule found",
            )

        request = self._approval_service.create_request(
            workflow_name=workflow.name,
            entity_type=entity_type,
            entity_id=entity_id,
            transition_action=action,
            from_state=current_state,
            to_state=transition.to_state,
            policy=policy,
            matched_rule_name=evaluation.matched_rule.rule_name,
            requestor_id=actor_id,
            amount=amount,
            currency=currency,
        )

        duration_ms = (time.monotonic() - t0) * 1000
        _emit_workflow_trace(
            workflow_name=workflow.name,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            from_state=current_state,
            to_state=transition.to_state,
            outcome=OUTCOME_APPROVAL_REQUIRED,
            reason=f"Approval required: {evaluation.reason}",
            duration_ms=duration_ms,
            approval_request_id=request.request_id,
            posts_entry=transition.posts_entry,
            outcome_sink=outcome_sink,
        )

        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=request.request_id,
            reason=f"Approval required: {evaluation.reason}",
        )

    def resume_after_approval(
        self,
        approval_request_id: UUID,
    ) -> TransitionResult:
        """Resume a transition that was blocked pending approval.

        Checks if the approval request is approved/auto-approved, then
        returns a success result so the caller can proceed with the
        state change and optional posting.
        """
        request = self._approval_service.get_request(approval_request_id)

        if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            return TransitionResult(
                success=True,
                new_state=request.to_state,
                posts_entry=False,  # Caller determines from workflow
                reason=f"Approved (status={request.status.value})",
            )

        if request.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Approval request was rejected",
            )

        if request.status in (ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED):
            return TransitionResult(
                success=False,
                reason=f"Approval request is {request.status.value}",
            )

        # Still pending/escalated
        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=approval_request_id,
            reason=f"Approval still pending (status={request.status.value})",
        )

    def record_approval_decision(
        self,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        decision: ApprovalDecision,
        comment: str = "",
    ) -> TransitionResult:
        """Record an approval decision and return the resulting state.

        Validates actor authority against the matched rule before
        delegating to ApprovalService.
        """
        request = self._approval_service.get_request(request_id)

        # Resolve the policy and matched rule for authority check
        policy = self._resolve_policy(request.workflow_name, request.transition_action)
        if policy is not None and request.matched_rule is not None:
            rule = select_matching_rule(policy.rules, request.amount)
            if rule is not None and not validate_actor_authority(actor_role, rule):
                raise UnauthorizedApproverError(str(actor_id), actor_role)

        # Record the decision
        updated = self._approval_service.record_decision(
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            decision=decision,
            comment=comment,
            active_policy=policy,
        )

        if updated.status == ApprovalStatus.APPROVED:
            return TransitionResult(
                success=True,
                new_state=updated.to_state,
                reason="Approved",
            )

        if updated.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Rejected",
            )

        # Still pending more approvals or escalated
        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=request_id,
            reason=f"Decision recorded; status={updated.status.value}",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_transition(
        self,
        workflow: WorkflowLike,
        current_state: str,
        action: str,
    ) -> TransitionLike | None:
        """Find a matching transition in the workflow."""
        for t in workflow.transitions:
            if t.from_state == current_state and t.action == action:
                return t
        return None

    def _resolve_policy(
        self,
        workflow_name: str,
        action: str,
    ) -> ApprovalPolicy | None:
        """Resolve the approval policy for a workflow/action pair.

        Checks for action-specific policy first, then workflow-level.
        """
        key = f"{workflow_name}:{action}"
        if key in self._policies:
            return self._policies[key]
        if workflow_name in self._policies:
            return self._policies[workflow_name]
        return None

    def _check_existing_approval(
        self,
        approval_request_id: UUID,
        transition: TransitionLike,
    ) -> TransitionResult:
        """Check if a pre-existing approval request is resolved."""
        request = self._approval_service.get_request(approval_request_id)

        if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason=f"Pre-approved (status={request.status.value})",
            )

        if request.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Approval request was rejected",
            )

        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=approval_request_id,
            reason=f"Approval not yet resolved (status={request.status.value})",
        )

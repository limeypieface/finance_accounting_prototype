# Plan: Period Close Orchestrator

**Date:** 2026-01-31
**Status:** COMPLETE (all implementation steps done, tests passing)
**Depends on:** Existing services (all production-tested)

---

## Problem

Every component an accountant needs to close the books exists as production code:
PeriodService, SubledgerPeriodService, ReportingService, GeneralLedgerService,
CorrectionEngine, and a declared PERIOD_CLOSE_WORKFLOW with guards. But there is
no orchestrating service that sequences these into a guided, guard-enforced
workflow. An accountant today would need to call 6+ services in the correct order,
manually verify each precondition, and has no visibility into what's blocking
close or why.

Beyond sequencing, the real pain in period close is **finding what's wrong** —
the investigation work that takes 80% of close time. The orchestrator must
surface exceptions, drilldown into variances, and show the accountant exactly
what needs fixing before a guard will pass.

Beyond diagnostics, period close is a **governed financial process**. It requires
authority (who is allowed to close), exclusivity (no concurrent posting during
close), evidence (immutable close certificate), and auditability (every phase
transition in the hash chain). The orchestrator treats close as a first-class
financial artifact, not a batch job.

## Objective

Build a `PeriodCloseOrchestrator` that:

1. Walks the declared workflow state machine (`open -> closing -> closed -> locked`)
2. Evaluates each guard and surfaces structured failure reasons
3. Runs a **pre-close health check** that finds problems before the accountant starts
4. Provides **exception drilldown** when a guard fails (which items, which accounts, how much)
5. **Acquires an exclusive close lock** on the period — no non-close postings while close is in progress
6. Enforces a **close authority model** — roles govern who can execute each phase
7. Logs every phase to the **existing structured logging + file handler** infrastructure
8. Records every phase transition via **AuditorService** (hash chain)
9. Produces an **immutable close certificate** hash-anchored via R24 + audit chain
10. Makes each close event **traceable** via the existing TraceSelector + `_render_trace()` — an accountant can pick "T" or "F" after close to see exactly what happened

---

## Design Principles

1. **Reuse everything** — No new logging, tracing, or audit infrastructure. The
   orchestrator plugs into the existing `get_logger()` + `StructuredFormatter` +
   `LogContext` + `AuditorService` + `TraceSelector` + `InterpretationOutcome`
   stack. Close events that post entries (adjustments, closing entries) flow
   through the normal `ModulePostingService` pipeline, which means they
   automatically get decision journals, trace bundles, and outcome records.

2. **Close is a financial artifact** — `PeriodCloseRun` is not a transient
   orchestration state. It is an auditable object that can be replayed, inspected,
   and reasoned about. It carries a correlation ID, an actor, a phase history,
   and a ledger hash. It can itself be audited.

3. **Thin orchestrator, fat services** — The orchestrator calls existing services.
   It adds no new business logic. Every close step already works independently.

4. **Guard-gated transitions** — Each workflow transition evaluates its declared
   guard before proceeding. If a guard fails, the transition is rejected with a
   structured reason + exception drilldown.

5. **Exclusive close lock** — While a `PeriodCloseRun` is `IN_PROGRESS`, all
   non-close postings to that period are rejected. Close is a system mode, not
   just a workflow step.

6. **Authority at phase boundaries** — Each phase declares a required role.
   The orchestrator validates the actor's role before executing.

7. **Idempotent phases** — Running a phase twice is safe. If subledgers are
   already closed, re-running that phase is a no-op.

8. **Transaction per phase** — Each phase commits independently. A crash between
   phases resumes from the last completed phase (tracked via PeriodCloseTask).

9. **CLI-first** — The first consumer is `interactive.py`. No REST/API layer yet.

---

## Existing Infrastructure Reused

| Component | How it's used in close | File |
|-----------|----------------------|------|
| `get_logger("services.period_close")` | Structured JSON logging for every phase decision | `finance_kernel/logging_config.py` |
| `LogContext.bind(correlation_id=...)` | Correlation ID ties all close phases into one trace | `finance_kernel/logging_config.py` |
| `StructuredFormatter` + FileHandler | All close logs written to `logs/interactive.log` at DEBUG | `scripts/interactive.py` (setup) |
| `AuditorService.record_period_closed()` | Phase 5 transition recorded in hash chain (R11) | `finance_kernel/services/auditor_service.py` |
| `AuditorService._create_audit_event()` | New domain methods for CLOSE_BEGUN, SUBLEDGER_CLOSED | `finance_kernel/services/auditor_service.py` |
| `ModulePostingService.post_event()` | Adjustments + closing entries flow through normal pipeline | `finance_kernel/services/module_posting_service.py` |
| `InterpretationOutcome` | Every posted/blocked/rejected entry gets an outcome (P15) | `finance_kernel/models/interpretation_outcome.py` |
| `TraceSelector.trace_by_event_id()` | Full audit trace on any close-related event via "T" menu | `finance_kernel/selectors/trace_selector.py` |
| `_render_trace()` | Shared trace renderer (9 sections) for CLI display | `scripts/interactive.py` |
| `show_failed_traces()` | "F" menu shows rejected/blocked close events | `scripts/interactive.py` |
| `SubledgerSelector.get_open_items(entity_id, sl_type)` | Exception drilldown: which items cause SL variance | `finance_kernel/selectors/subledger_selector.py` |
| `SubledgerSelector.get_balance(entity_id, sl_type, as_of, currency)` | Per-entity balance for reconciliation detail | `finance_kernel/selectors/subledger_selector.py` |
| `SubledgerSelector.get_aggregate_balance(sl_type, as_of, currency)` | SL total for reconciliation | `finance_kernel/selectors/subledger_selector.py` |
| `SubledgerSelector.get_entities(sl_type)` | Entity enumeration for drilldown | `finance_kernel/selectors/subledger_selector.py` |
| `JournalSelector.get_entries_by_period(start, end, status?)` | Period activity for health check | `finance_kernel/selectors/journal_selector.py` |
| `ReportingService.trial_balance(as_of_date)` | TB with `.is_balanced`, `.total_debits`, `.total_credits` | `finance_modules/reporting/service.py` |
| `ReportingService.income_statement(start, end)` | Net income for year-end closing entry | `finance_modules/reporting/service.py` |
| `LedgerSelector.account_balance(account_id: UUID, as_of?, currency?)` | Suspense/clearing account balance checks | `finance_kernel/selectors/ledger_selector.py` |
| `LedgerSelector.canonical_hash()` | R24 ledger hash for close certificate | `finance_kernel/selectors/ledger_selector.py` |
| `PeriodService.close_period(period_code, actor_id)` | GL period lock (R12) | `finance_kernel/services/period_service.py` |
| `PeriodService.validate_effective_date(date)` | R12 enforcement (extended for CLOSING) | `finance_kernel/services/period_service.py` |

---

## Architecture

```
PeriodCloseOrchestrator          (finance_services/)
    |
    +-- Close Lock Contract
    |     +-- PeriodService           CLOSING status blocks non-close posts
    |     +-- FiscalPeriod ORM        closing_run_id column for ownership
    |
    +-- Authority Model
    |     +-- CloseRole enum          PREPARER, REVIEWER, APPROVER
    |     +-- Phase requirements      each phase declares required_role
    |
    +-- PreCloseHealthCheck      (method on orchestrator)
    |     +-- SubledgerSelector       get_open_items, get_balance, get_aggregate_balance
    |     +-- LedgerSelector          account_balance (suspense accts, via UUID lookup)
    |     +-- JournalSelector         get_entries_by_period
    |     +-- ReportingService        trial_balance (comparative)
    |
    +-- Phase execution
    |     +-- SubledgerPeriodService  SL close + reconciliation (optional — may be None)
    |     +-- ReportingService        trial balance verification
    |     +-- GeneralLedgerService    adjustments + closing entries
    |     +-- PeriodService           GL period lock
    |
    +-- Exception drilldown
    |     +-- SubledgerSelector       open items causing variance
    |     +-- LedgerSelector          account-level detail
    |
    +-- Close certificate
    |     +-- LedgerSelector          canonical_hash (R24)
    |     +-- AuditorService          hash-anchored close record
    |
    +-- Observability (all existing)
          +-- get_logger()            structured JSON to file handler
          +-- LogContext.bind()       correlation ID across phases
          +-- AuditorService          hash chain per phase
          +-- InterpretationOutcome   auto for posted entries
          +-- TraceSelector           "T" menu traces any close event

Interactive CLI                  (scripts/interactive.py)
    +-- "C" menu option          drives orchestrator step by step
    +-- "H" menu option          health check (read-only)
    +-- "T" menu option          traces any close event (already works)
    +-- "F" menu option          shows blocked/rejected close events (already works)
```

### Layer placement

`finance_services/period_close_orchestrator.py` — Composes kernel services +
module services. Same layer as PostingOrchestrator and EngineDispatcher.

---

## NEW: Close Lock Contract (R25)

### The invariant

> While a `PeriodCloseRun` is `IN_PROGRESS` for a period, all non-close postings
> to that period MUST be rejected.

Close is a **system mode**, not just a workflow step. When an accountant begins
close, the period enters an exclusive state where only close-related operations
(adjustments via Phase 3, closing entries via Phase 4) are permitted. Normal
business postings are blocked until close completes or is cancelled.

### Implementation

**1. Extend `PeriodStatus` enum** (ORM model: `finance_kernel/models/fiscal_period.py`)

The current ORM has only `OPEN` and `CLOSED`. The workflow declares 5 states.
Add `CLOSING` and `LOCKED`:

```python
class PeriodStatus(str, Enum):
    OPEN = "open"
    CLOSING = "closing"    # NEW — close in progress, non-close posts blocked
    CLOSED = "closed"
    LOCKED = "locked"      # NEW — permanent, no reopen possible
```

**2. Add `closing_run_id` column** to `FiscalPeriod`:

```python
# ID of the PeriodCloseRun that owns the CLOSING lock
closing_run_id: Mapped[str | None] = mapped_column(
    UUIDString(),
    nullable=True,
)
```

This establishes **ownership**. Only the run that acquired the lock can advance
the period through close phases or release the lock.

**3. Extend `validate_effective_date()`** in `PeriodService`:

```python
def validate_effective_date(self, effective_date: date, *, is_close_posting: bool = False) -> None:
    period = self._get_period_for_date_orm(effective_date)
    if period is None:
        raise PeriodNotFoundError(str(effective_date))
    if period.is_closed:
        raise ClosedPeriodError(period.period_code, str(effective_date))
    # R25: CLOSING period blocks non-close postings
    if period.status == PeriodStatus.CLOSING and not is_close_posting:
        raise PeriodClosingError(period.period_code)
```

Normal posting pipeline calls `validate_effective_date()` without the flag.
The orchestrator's adjustment/closing-entry phases pass `is_close_posting=True`.

**4. Acquire lock via `SELECT ... FOR UPDATE`** in `begin_close()`:

```python
period = session.execute(
    select(FiscalPeriod)
    .where(FiscalPeriod.period_code == period_code)
    .with_for_update()
).scalar_one_or_none()

if period.status == PeriodStatus.CLOSING:
    raise PeriodCloseAlreadyInProgressError(period_code, period.closing_run_id)

period.status = PeriodStatus.CLOSING
period.closing_run_id = str(run_id)
session.flush()
```

Two accountants starting close on the same period: the second blocks on
`FOR UPDATE`, then sees `CLOSING` status and gets a clear error.

**5. Release lock on cancel or failure:**

```python
def cancel_close(self, period_code: str, actor_id: UUID) -> None:
    period = self._get_period_for_update(period_code)
    if period.status != PeriodStatus.CLOSING:
        raise ValueError(f"Period {period_code} is not in CLOSING state")
    period.status = PeriodStatus.OPEN
    period.closing_run_id = None
    session.flush()
```

### New exception type

```python
class PeriodClosingError(FinanceKernelError):
    """Non-close posting attempted on a period that is mid-close."""
    code = "PERIOD_CLOSING"
```

### New invariant

| Rule | Name | Summary |
|------|------|---------|
| R25 | Close lock exclusivity | CLOSING period rejects all non-close postings |

---

## NEW: Close Authority Model

### Roles

```python
class CloseRole(str, Enum):
    """Roles for period close operations."""
    PREPARER = "preparer"     # Can run health check, begin close, execute phases 1-4
    REVIEWER = "reviewer"     # Can verify results, approve phase transitions
    APPROVER = "approver"     # Can execute phases 5-6 (GL close, lock)
    AUDITOR = "auditor"       # Read-only access to health check, close certificate, traces
```

### Phase authority requirements

| Phase | Operation | Minimum role |
|-------|-----------|-------------|
| 0 | Health check | `AUDITOR` (read-only) |
| - | Begin close | `PREPARER` |
| 1 | Close subledgers | `PREPARER` |
| 2 | Verify trial balance | `PREPARER` |
| 3 | Post adjustments | `PREPARER` |
| 4 | Post closing entries | `PREPARER` |
| 5 | Close GL period | `APPROVER` |
| 6 | Lock period | `APPROVER` |
| - | Cancel close | `APPROVER` |
| - | Reopen period | `APPROVER` |

### Enforcement

```python
PHASE_AUTHORITY: dict[int, CloseRole] = {
    0: CloseRole.AUDITOR,
    1: CloseRole.PREPARER,
    2: CloseRole.PREPARER,
    3: CloseRole.PREPARER,
    4: CloseRole.PREPARER,
    5: CloseRole.APPROVER,
    6: CloseRole.APPROVER,
}

def _check_authority(self, actor_id: UUID, phase: int) -> None:
    required = PHASE_AUTHORITY[phase]
    actor_role = self._resolve_close_role(actor_id)
    if not actor_role.has_authority(required):
        raise CloseAuthorityError(
            actor_id=actor_id,
            required_role=required,
            actual_role=actor_role,
            phase=phase,
        )
```

### Role resolution — pluggable, not hardcoded

The orchestrator receives a `CloseRoleResolver` protocol:

```python
class CloseRoleResolver(Protocol):
    def resolve(self, actor_id: UUID) -> CloseRole: ...
```

Default implementation: everyone is `APPROVER` (no restriction). This preserves
backward compatibility for the CLI-first mode where a single operator runs
everything. In regulated environments, plug in a resolver that checks against
a role table, LDAP, or IAM service.

```python
class DefaultCloseRoleResolver:
    """Default: all actors are APPROVER (unrestricted)."""
    def resolve(self, actor_id: UUID) -> CloseRole:
        return CloseRole.APPROVER
```

### Segregation of duties tracking

Even with the default resolver, the `PeriodCloseRun` records `started_by` and
each `ClosePhaseResult` records `executed_by`. An auditor can verify after the
fact whether proper segregation was observed by inspecting these fields.

### New exception type

```python
class CloseAuthorityError(FinanceKernelError):
    """Actor lacks authority for the requested close operation."""
    code = "CLOSE_AUTHORITY_DENIED"
```

---

## NEW: Close Certificate (Immutable Artifact)

### What it is

When close completes, the orchestrator persists a `CloseCertificate` — a frozen
record that attests to the state of the ledger at the moment of close. It is:

- **Hash-anchored** via R24 `canonical_hash()` — cryptographic proof of ledger state
- **Audit-chained** via R11 — recorded as an audit event with prev_hash linkage
- **Actor-signed** — records who closed and who approved
- **Correlation-linked** — carries the close run's correlation ID for full traceability

### Data structure

```python
@dataclass(frozen=True)
class CloseCertificate:
    """Immutable attestation of period close."""
    id: UUID
    period_code: str
    closed_at: datetime
    closed_by: UUID                      # Actor who executed Phase 5
    approved_by: UUID | None             # Actor who executed Phase 6 (if year-end)
    correlation_id: str                  # Links to all close logs + events
    ledger_hash: str                     # R24 canonical hash at close
    trial_balance_debits: Decimal
    trial_balance_credits: Decimal
    subledgers_closed: tuple[str, ...]   # List of SL types closed
    adjustments_posted: int              # Count of Phase 3 adjustments
    closing_entries_posted: int          # Count of Phase 4 entries
    phases_completed: int
    phases_skipped: int
    audit_event_id: UUID                 # Points to the hash-chain record
```

### Persistence

The certificate is persisted via `AuditorService` as an audit event with
action `PERIOD_CLOSE_CERTIFIED` and the certificate data as payload. This means:

1. It's in the hash chain (R11) — tamper-evident
2. It's append-only — immutable by ORM + DB triggers
3. It's queryable via existing audit queries
4. It doesn't require a new ORM model — it's payload on an AuditEvent

```python
def _persist_certificate(self, cert: CloseCertificate) -> AuditEvent:
    return self._auditor.record_close_certified(
        period_id=period_id,
        period_code=cert.period_code,
        actor_id=cert.closed_by,
        certificate_data={
            "certificate_id": str(cert.id),
            "ledger_hash": cert.ledger_hash,
            "correlation_id": cert.correlation_id,
            "trial_balance_debits": str(cert.trial_balance_debits),
            "trial_balance_credits": str(cert.trial_balance_credits),
            "subledgers_closed": list(cert.subledgers_closed),
            "adjustments_posted": cert.adjustments_posted,
            "closing_entries_posted": cert.closing_entries_posted,
        },
    )
```

### No new ORM model

The certificate is a domain DTO. Its persistence is an audit event. This avoids
a new table while providing full immutability, hash-chain integrity, and
queryability through the existing audit infrastructure.

---

## Workflow State Machine

From `finance_modules/gl/workflows.py` (with CLOSING status now enforced):

```
  future --> open --> closing --> closed --> locked
                       |  ^                    |
                       |  |       reopen <-----+
                       |  |
                       |  cancel_close (back to open)
                       |
            R25: non-close posts BLOCKED
            guard: ALL_SUBLEDGERS_CLOSED
                                  |
                       guard: TRIAL_BALANCE_BALANCED
                       posts_entry: true
                                            |
                                 guard: YEAR_END_ENTRIES_POSTED
                                 (year-end only)
```

### ORM gap to close

The `FiscalPeriod` ORM model (`finance_kernel/models/fiscal_period.py`) currently
defines only `OPEN` and `CLOSED`. The `Workflow` in `gl/workflows.py` declares
5 states. The plan must extend `PeriodStatus` to:

```python
class PeriodStatus(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LOCKED = "locked"
```

And update `FiscalPeriod.is_closed` to:

```python
@property
def is_closed(self) -> bool:
    return self.status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)
```

The `PeriodService.validate_effective_date()` and `PeriodService.close_period()`
methods must be updated to handle `CLOSING` status. See R25 section above.

---

## Phase 0: Pre-Close Health Check

Before starting the close workflow, the orchestrator runs a diagnostic scan
that surfaces problems the accountant will hit during close. This is what
saves time — finding issues before Phase 1 blocks on them.

**Authority:** `AUDITOR` (read-only — anyone can run health check).

**What it checks (using existing selectors):**

### 0a. Subledger reconciliation preview

For each subledger type, compare `SubledgerSelector.get_aggregate_balance(sl_type, as_of, currency)`
against the GL control account via `LedgerSelector.account_balance(account_id, as_of)`.

**Integration note:** `LedgerSelector.account_balance()` takes `account_id: UUID`,
not an account code. The orchestrator resolves codes to UUIDs via a private helper:

```python
def _account_id_for_code(self, code: str) -> UUID | None:
    """Resolve account code to UUID via Account ORM model."""
    from finance_kernel.models.account import Account
    acct = self._session.execute(
        select(Account).where(Account.code == code)
    ).scalar_one_or_none()
    return acct.id if acct else None
```

```
  PRE-CLOSE HEALTH CHECK: 2026-01
  ================================

  Subledger Reconciliation Preview:
    AP ........... SL $45,200.00  GL $45,200.00  variance: $0.00       OK
    AR ........... SL $12,340.00  GL $10,000.00  variance: $2,340.00   MISMATCH
    Inventory .... SL $89,000.00  GL $89,000.00  variance: $0.00       OK
    Bank ......... SL $23,456.00  GL $23,456.00  variance: $0.00       OK
```

### 0b. Unmatched / open items per subledger

For subledgers with mismatches, call `SubledgerSelector.get_open_items(entity_id, sl_type)` per
entity and `SubledgerSelector.get_balance(entity_id, sl_type, as_of, currency)` to show which
entities contribute to the variance.

```
  AR Mismatch Detail:
    Entity CUST-001 .... balance: $8,500.00   open items: 3
    Entity CUST-002 .... balance: $3,840.00   open items: 1
    SL total: $12,340.00   GL control: $10,000.00
    Variance: $2,340.00 — likely unposted credit memo or misapplied payment
```

### 0c. Suspense / clearing account balances

Query accounts tagged with `AccountTag.SUSPENSE` (code 6998), GRNI (code 2100),
and labor clearing (code 2500) via `LedgerSelector.account_balance(account_id, as_of)`.
Non-zero balances at period end are red flags.

```
  Suspense / Clearing Accounts:
    2100 GRNI .............. $3,200.00   WARNING — should be zero at close
    2500 Labor Clearing .... $0.00       OK
    6998 Rounding .......... $0.12       OK (within tolerance)
```

### 0d. Trial balance check (with prior period comparison)

```
  Trial Balance:
    Total Debits:  $1,234,567.89
    Total Credits: $1,234,567.89
    Balanced: YES

  vs Prior Period (2025-12):
    Revenue change: +$45,000.00 (+12%)
    Expense change: +$38,000.00 (+9%)
    Net income this period: $7,000.00
```

### 0e. Period activity summary

Via `JournalSelector.get_entries_by_period(start_date, end_date)`:

```
  Period Activity:
    Journal entries: 47
    Adjusting entries: 0
    Reversal entries: 2
    Events posted: 42
    Events rejected: 3   <-- viewable via "F" menu
```

### 0f. Health check verdict

```
  HEALTH CHECK RESULT: 2 issues found
    [BLOCKING] AR subledger variance: $2,340.00
    [WARNING]  GRNI balance: $3,200.00

  Fix blocking issues before starting close, or proceed to investigate.
```

The health check is **read-only** — no state changes, no transactions. It can
be run as many times as needed. It uses the same `LogContext.bind()` correlation
ID as the rest of the close so all health check logs appear in
`logs/interactive.log` under the same trace.

---

## Phases 1-6: Close Execution

### Phase 1: Close Subledgers (tasks 1-5)

**Authority:** `PREPARER`.
**Action:** For each registered subledger with `enforce_on_close=True`, call
`SubledgerPeriodService.close_subledger_period()`.
**Guard:** `ALL_SUBLEDGERS_CLOSED`.

**Integration note:** `PostingOrchestrator.subledger_period_service` may be `None`.
If `None`, Phase 1 skips subledger close with a warning and the
`ALL_SUBLEDGERS_CLOSED` guard evaluates as `PASS` (no subledgers configured).

**Logging:** Each subledger close logged at INFO:
```python
logger.info("phase_1_subledger_close", extra={
    "correlation_id": close_correlation_id,
    "subledger_type": sl_type.value,
    "period_code": period_code,
    "result": "closed" | "skipped",
    "variance": str(variance),
})
```

**Audit:** `AuditorService.record_subledger_closed()` (new method):
```python
def record_subledger_closed(
    self,
    period_id: UUID,
    period_code: str,
    subledger_type: str,
    actor_id: UUID,
) -> AuditEvent:
    """Record that a subledger was closed for a period."""
    return self._create_audit_event(
        entity_type="SubledgerClose",
        entity_id=period_id,
        action=AuditAction.SUBLEDGER_CLOSED,
        actor_id=actor_id,
        payload={
            "period_code": period_code,
            "subledger_type": subledger_type,
        },
    )
```

**On failure — exception drilldown:**

When a subledger fails reconciliation, the orchestrator doesn't just say
"AR failed." It calls `SubledgerSelector.get_open_items(entity_id, sl_type)` for
each entity in that subledger and shows the specific items causing the variance:

```
  Phase 1/6: CLOSE SUBLEDGERS
  ----------------------------
    AP .......... CLOSED  (variance: $0.00)
    AR .......... FAILED  (variance: -$2,340.00)

  Guard [ALL_SUBLEDGERS_CLOSED]: BLOCKED

  AR Exception Drilldown:
  +-----------+----------+------------+------+------------------------+
  | Entity    | Amount   | Date       | Side | Description            |
  +-----------+----------+------------+------+------------------------+
  | CUST-001  | $1,500   | 2026-01-15 | Dr   | INV-1042 (unmatched)   |
  | CUST-001  |   $500   | 2026-01-22 | Dr   | INV-1055 (unmatched)   |
  | CUST-002  |   $340   | 2026-01-28 | Dr   | INV-1061 (partial)     |
  +-----------+----------+------------+------+------------------------+
  Total open: $2,340.00

  These items exist in the AR subledger but have no matching GL entry.
  Post correcting entries, then retry Phase 1.

  Trace any of these? Enter entity ID (or blank to skip): CUST-001
  [renders SubledgerSelector detail for that entity]
```

**Idempotency:** Already-closed subledgers are skipped (service returns CLOSED).

### Phase 2: Verify Trial Balance (task 6)

**Authority:** `PREPARER`.
**Action:** `ReportingService.trial_balance(as_of_date=period_end_date)`.
**Guard:** `TRIAL_BALANCE_BALANCED` — `report.is_balanced` must be True.

**Logging:**
```python
logger.info("phase_2_trial_balance", extra={
    "correlation_id": close_correlation_id,
    "total_debits": str(tb.total_debits),
    "total_credits": str(tb.total_credits),
    "is_balanced": tb.is_balanced,
    "account_count": len(tb.lines),
})
```

**On failure:** Show the specific accounts with the largest imbalance
contribution (top 5 by absolute balance) using `tb.lines`.

### Phase 3: Post Adjustments (task 7) — INTERACTIVE

**Authority:** `PREPARER`.
**Action:** User-driven. The orchestrator does NOT auto-generate adjustments.

```
  Phase 3/6: ADJUSTMENTS
  -----------------------
    Options:
      1. Post an adjusting entry (accrual, deferral, reclass)
      2. View current trial balance
      3. View suspense account detail
      4. Done — skip remaining adjustments

    Pick: 1
    Type [ACCRUAL/DEFERRAL/RECLASS]: ACCRUAL
    Description: January rent accrual
    Debit account code: 6100
    Credit account code: 2300
    Amount: 5000.00

    Posted: entry #48, event_id: a1b2c3...
    (Traceable via "T 48" from main menu)
```

Each adjustment flows through `GeneralLedgerService.record_adjustment()` →
`ModulePostingService.post_event()` → normal pipeline. The posting pipeline
is called with `is_close_posting=True` so R25 allows it through the CLOSING
period lock. This means:
- Decision journal captured automatically (`InterpretationOutcome.decision_log`)
- Audit event recorded automatically (`AuditorService`)
- Full trace available via "T" menu immediately
- If posting fails, visible via "F" menu with full trace

**Logging:** Normal pipeline logging (already exists). Plus:
```python
logger.info("phase_3_adjustment_posted", extra={
    "correlation_id": close_correlation_id,
    "adjustment_type": adj_type,
    "amount": str(amount),
    "journal_entry_id": str(entry_id),
})
```

After all adjustments, re-verify trial balance before marking phase complete.

### Phase 4: Post Closing Entries (task 8) — YEAR-END ONLY

**Authority:** `PREPARER`.
**Action:** `GeneralLedgerService.record_closing_entry()` to zero out
revenue/expense into retained earnings.

```python
income = reporting_service.income_statement(
    period_start=fiscal_year_start,
    period_end=period_end_date,
)

result = gl_service.record_closing_entry(
    period_id=period_code,
    effective_date=period_end_date,
    actor_id=actor_id,
    net_income=income.net_income,
)
```

Flows through normal posting pipeline (with `is_close_posting=True`) —
full trace + decision journal + audit.

**Logging:**
```python
logger.info("phase_4_closing_entry", extra={
    "correlation_id": close_correlation_id,
    "net_income": str(income.net_income),
    "journal_entry_id": str(result.journal_entry_ids[0]),
})
```

**Skip condition:** Non-year-end periods skip this phase entirely.

### Phase 5: Close GL Period (task 9)

**Authority:** `APPROVER` — this is the sign-off step.
**Action:** `PeriodService.close_period(period_code, actor_id)`.
**Effect:** Period status `CLOSING` → `CLOSED`. R12 enforcement active.
Close lock released (closing_run_id cleared).

**Audit:** `AuditorService.record_period_closed(period_id, period_code, actor_id)` —
uses the existing method with exact signature match.

**Segregation tracking:** The `PeriodCloseRun` records `started_by` (Phase 1 actor)
separately from the Phase 5 actor. An auditor can verify that a different person
approved the close than prepared it.

### Phase 6: Lock Period (task 10) — YEAR-END ONLY, OPTIONAL

**Authority:** `APPROVER`.
**Action:** Permanent lock. No reopening possible.
**Guard:** `YEAR_END_ENTRIES_POSTED`.
**Effect:** Period status `CLOSED` → `LOCKED`.

---

## Close Completion Summary + Certificate

After all phases complete, the orchestrator:

1. Computes `LedgerSelector.canonical_hash()` for R24 anchoring
2. Builds a `CloseCertificate` DTO
3. Persists it via `AuditorService.record_close_certified()` (hash chain)
4. Logs the summary
5. Prints the close certificate to console

```
  ===================================================================
  PERIOD 2026-01 CLOSED SUCCESSFULLY
  ===================================================================

  Close Certificate ID:  c4d5e6f7-...
  Period:                2026-01 (2026-01-01 .. 2026-01-31)
  Closed by:             ADMIN-001 (System Administrator)
  Closed at:             2026-01-31T23:59:00Z
  Duration:              12.3 seconds

  Authority:
    Prepared by:         ADMIN-001
    Approved by:         ADMIN-001  (same — flag for audit in regulated env)

  Phases:
    [1] Close subledgers .... DONE  (5 subledgers, 0 variances)
    [2] Trial balance ....... DONE  (Dr $1,234,567.89 = Cr $1,234,567.89)
    [3] Adjustments ......... DONE  (2 adjustments posted)
    [4] Closing entries ..... SKIP  (not year-end)
    [5] Close GL period ..... DONE  (period_status=CLOSED)
    [6] Lock period ......... SKIP  (not year-end)

  Artifacts:
    Journal entries created:  2 (adjustments)
    Audit events recorded:    7
    Correlation ID:           a1b2c3d4-...

  All close events are traceable via "T" from the main menu.
  Any blocked/rejected events are viewable via "F".

  Ledger hash (R24): 9f8e7d6c5b4a...
  Certificate audit event: ae-789...
  ===================================================================
```

**Logging (final):**
```python
logger.info("period_close_completed", extra={
    "correlation_id": close_correlation_id,
    "period_code": period_code,
    "certificate_id": str(cert.id),
    "phases_completed": completed,
    "phases_skipped": skipped,
    "adjustments_posted": adj_count,
    "duration_ms": duration,
    "ledger_hash": canonical_hash,
    "prepared_by": str(run.started_by),
    "approved_by": str(phase_5_actor),
})
```

---

## Traceability Integration (How Existing Trace Works With Close)

### During close: Structured logging to file

Every phase uses `LogContext.bind(correlation_id=close_correlation_id)` so all
log entries — from the orchestrator, from subledger services, from the posting
pipeline — share one correlation ID. These flow to `logs/interactive.log` via
the FileHandler we added (DEBUG level, JSON format).

An auditor can grep `logs/interactive.log` for the correlation ID and see the
entire close sequence with timestamps, decisions, and outcomes.

### After close: Trace any event

Adjustments and closing entries posted during close flow through
`ModulePostingService.post_event()`. This means:

1. Each gets an `InterpretationOutcome` with `decision_log` (captured by
   `InterpretationCoordinator` via `LogCapture`)
2. Each gets audit events in the hash chain
3. Each is traceable via `TraceSelector.trace_by_event_id()` — the same "T"
   menu option that already works

If a close adjustment fails or is rejected, it appears in the "F" menu
(`show_failed_traces()`) with full trace rendering (origin event, posting
context, interpretation outcome with reason_code, decision journal).

### Close certificate is traceable

The certificate's `audit_event_id` can be looked up via existing audit queries.
The certificate's `correlation_id` links to all close-phase logs. The
certificate's `ledger_hash` proves the ledger state at close time.

### No new trace infrastructure needed

The orchestrator does not create its own trace system. It creates events that
flow through the existing pipeline. The only new logging is phase-level INFO
logs (phase started, phase completed, guard passed/failed) that go to the
same structured logger.

---

## Data Model

### CloseRunStatus (enum — `finance_modules/gl/models.py`)

```python
class CloseRunStatus(Enum):
    """Period close run lifecycle."""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### PeriodCloseRun (DTO — `finance_modules/gl/models.py`)

First-class financial artifact. Records the full close lifecycle for audit.

```python
@dataclass(frozen=True)
class PeriodCloseRun:
    """Auditable record of a period close attempt."""
    id: UUID
    period_code: str
    fiscal_year: int
    is_year_end: bool
    status: CloseRunStatus
    current_phase: int              # 0-6
    correlation_id: str             # LogContext correlation ID for all phases
    started_at: datetime
    started_by: UUID                # Actor who began close (PREPARER)
    completed_at: datetime | None
    tasks: tuple[PeriodCloseTask, ...]
    phase_actors: dict[int, UUID]   # Who executed each phase (segregation tracking)
    ledger_hash: str | None         # R24 canonical hash at close
    certificate_id: UUID | None     # Points to CloseCertificate
```

Domain DTO, not ORM model. Orchestrator persists state via `PeriodCloseTask`
records (already defined in `gl/models.py`) and the `closing_run_id` on
`FiscalPeriod`. The `PeriodCloseRun` is reconstructed from these at resume time.

### ClosePhaseResult

```python
@dataclass(frozen=True)
class ClosePhaseResult:
    phase: int
    phase_name: str
    success: bool
    executed_by: UUID               # Actor who ran this phase
    guard: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    exceptions: tuple[CloseException, ...] = ()
```

### CloseException (drilldown item)

```python
@dataclass(frozen=True)
class CloseException:
    """Structured investigation item — formalizes investigation as data, not a screen."""
    category: str          # "sl_variance", "suspense_balance", "open_item"
    subledger_type: str | None
    entity_id: str | None
    account_code: str | None
    amount: Decimal
    currency: str
    description: str
    severity: str          # "blocking" or "warning"
```

### HealthCheckResult

```python
@dataclass(frozen=True)
class HealthCheckResult:
    period_code: str
    run_at: datetime
    sl_reconciliation: dict[str, dict]   # per-SL variance + status
    suspense_balances: list[dict]        # non-zero suspense/clearing accounts
    trial_balance_ok: bool
    total_debits: Decimal
    total_credits: Decimal
    period_entry_count: int
    period_rejection_count: int
    blocking_issues: list[CloseException]
    warnings: list[CloseException]
    can_proceed: bool                    # True if zero blocking issues
```

### CloseCertificate (immutable attestation)

```python
@dataclass(frozen=True)
class CloseCertificate:
    """Immutable attestation of period close. Persisted as audit event payload."""
    id: UUID
    period_code: str
    closed_at: datetime
    closed_by: UUID
    approved_by: UUID | None
    correlation_id: str
    ledger_hash: str
    trial_balance_debits: Decimal
    trial_balance_credits: Decimal
    subledgers_closed: tuple[str, ...]
    adjustments_posted: int
    closing_entries_posted: int
    phases_completed: int
    phases_skipped: int
    audit_event_id: UUID              # Hash-chain record
```

### PeriodCloseResult (return type from `close_period_full()`)

```python
@dataclass(frozen=True)
class PeriodCloseResult:
    period_code: str
    status: CloseRunStatus
    correlation_id: str
    phases_completed: int
    phases_total: int
    phase_results: tuple[ClosePhaseResult, ...]
    started_at: datetime
    completed_at: datetime | None
    certificate: CloseCertificate | None  # Present only on success
    message: str
```

---

## Service Interface

```python
class PeriodCloseOrchestrator:
    """
    Sequences period-close phases with guard enforcement, diagnostics,
    authority control, and close certification.

    Close is treated as a first-class financial artifact:
    - Exclusive lock on the period (R25)
    - Authority model at phase boundaries
    - Immutable close certificate with ledger hash
    - Full traceability via existing infrastructure
    """

    def __init__(
        self,
        session: Session,
        period_service: PeriodService,
        sl_period_service: SubledgerPeriodService | None,  # May be None
        reporting_service: ReportingService,
        gl_service: GeneralLedgerService,
        auditor_service: AuditorService,
        subledger_selector: SubledgerSelector,
        ledger_selector: LedgerSelector,
        journal_selector: JournalSelector,
        clock: Clock,
        role_resolver: CloseRoleResolver | None = None,  # Default: unrestricted
    ): ...

    @classmethod
    def from_posting_orchestrator(
        cls,
        posting_orch: PostingOrchestrator,
        reporting_service: ReportingService,
        gl_service: GeneralLedgerService,
    ) -> PeriodCloseOrchestrator:
        """
        Preferred constructor — reuses singleton services from PostingOrchestrator.

        Handles optional subledger_period_service (may be None on PostingOrchestrator).
        """
        return cls(
            session=posting_orch.session,
            period_service=posting_orch.period_service,
            sl_period_service=posting_orch.subledger_period_service,  # may be None
            reporting_service=reporting_service,
            gl_service=gl_service,
            auditor_service=posting_orch.auditor,
            subledger_selector=SubledgerSelector(posting_orch.session),
            ledger_selector=LedgerSelector(posting_orch.session),
            journal_selector=JournalSelector(posting_orch.session),
            clock=posting_orch.clock,
        )

    # --- Pre-close diagnostics ---

    def health_check(
        self,
        period_code: str,
        period_end_date: date,
        currency: str = "USD",
    ) -> HealthCheckResult:
        """
        Read-only diagnostic scan. No state changes. Safe to run repeatedly.
        Authority: AUDITOR (anyone can run).
        Includes subledger drilldown for mismatches and suspense account detail.
        """
        ...

    # --- Close execution ---

    def begin_close(
        self,
        period_code: str,
        actor_id: UUID,
        is_year_end: bool = False,
    ) -> PeriodCloseRun:
        """
        Initialize close run.
        - Validates authority (PREPARER minimum)
        - Acquires exclusive close lock (R25): period → CLOSING status
        - Creates PeriodCloseTask records
        - Binds LogContext correlation ID
        - Records CLOSE_BEGUN audit event
        """
        ...

    def run_phase(
        self,
        run: PeriodCloseRun,
        phase: int,
        actor_id: UUID,
        **kwargs,
    ) -> ClosePhaseResult:
        """
        Execute a single phase.
        - Validates authority for the phase
        - Evaluates guard
        - Executes phase action
        - Records phase actor for segregation tracking
        - On failure: drills down exceptions
        """
        ...

    def get_status(self, period_code: str) -> PeriodCloseRun | None:
        """Reconstruct PeriodCloseRun from PeriodCloseTask records + period row."""
        ...

    def cancel_close(self, period_code: str, actor_id: UUID) -> None:
        """
        Cancel an in-progress close.
        - Validates authority (APPROVER)
        - Releases close lock: period → OPEN, closing_run_id = None
        - Records CLOSE_CANCELLED audit event
        """
        ...

    def close_period_full(
        self,
        period_code: str,
        actor_id: UUID,
        is_year_end: bool = False,
        adjustment_callback: Callable | None = None,
    ) -> PeriodCloseResult:
        """
        All phases in sequence. Stops on first blocking failure.
        On success: persists CloseCertificate.
        On failure: period remains in CLOSING (resume or cancel).
        """
        ...
```

---

## AuditorService Extensions

### New AuditAction values

```python
class AuditAction(str, Enum):
    # ... existing ...

    # Close lifecycle (NEW)
    CLOSE_BEGUN = "close_begun"
    SUBLEDGER_CLOSED = "subledger_closed"
    CLOSE_CERTIFIED = "close_certified"
    CLOSE_CANCELLED = "close_cancelled"
```

### New domain methods

```python
def record_close_begun(
    self,
    period_id: UUID,
    period_code: str,
    actor_id: UUID,
    correlation_id: str,
) -> AuditEvent:
    """Record that a period close was initiated."""
    ...

def record_subledger_closed(
    self,
    period_id: UUID,
    period_code: str,
    subledger_type: str,
    actor_id: UUID,
) -> AuditEvent:
    """Record that a subledger was closed for a period."""
    ...

def record_close_certified(
    self,
    period_id: UUID,
    period_code: str,
    actor_id: UUID,
    certificate_data: dict,
) -> AuditEvent:
    """Record the close certificate. Certificate data in payload."""
    ...

def record_close_cancelled(
    self,
    period_id: UUID,
    period_code: str,
    actor_id: UUID,
    reason: str,
) -> AuditEvent:
    """Record that a close was cancelled."""
    ...
```

These follow the existing pattern: thin wrappers around `_create_audit_event()`
with domain-specific signatures. No new infrastructure.

---

## Interactive CLI Integration

### Menu

```
  View:
    R   View all reports
    J   View journal entries
    S   Subledger reports (entity balances, open items)
    T   Trace a journal entry (full auditor decision trail)
    F   Trace a failed/rejected/blocked event

  Close:
    C   Close a period (guided workflow)
    H   Pre-close health check (read-only diagnostic)

  Other:
    A   Post ALL scenarios at once
    X   Reset database
    Q   Quit
```

### "H" — Health Check (read-only, safe, repeatable)

```
  ===================================================================
    PRE-CLOSE HEALTH CHECK: 2026-01
  ===================================================================

  Subledger Reconciliation:
    AP ........... SL $45,200.00  GL $45,200.00  variance: $0.00       OK
    AR ........... SL $12,340.00  GL $10,000.00  variance: $2,340.00   MISMATCH
    Inventory .... SL $89,000.00  GL $89,000.00  variance: $0.00       OK
    Bank ......... SL $23,456.00  GL $23,456.00  variance: $0.00       OK

  Suspense / Clearing Accounts:
    2100 GRNI .............. $3,200.00   WARNING
    2500 Labor Clearing .... $0.00       OK

  Trial Balance:
    Debits:  $1,234,567.89
    Credits: $1,234,567.89
    Balanced: YES

  Period Activity (2026-01):
    Entries: 47   Adjustments: 0   Reversals: 2   Rejected: 3

  RESULT: 1 blocking, 1 warning
    [BLOCKING] AR subledger variance: $2,340.00
    [WARNING]  GRNI balance not zero: $3,200.00

  Drill into AR detail? [y/N]: y

    AR Open Items:
    +-----------+----------+------------+------+------------------------+
    | Entity    | Amount   | Date       | Side | Reference              |
    +-----------+----------+------------+------+------------------------+
    | CUST-001  | $1,500   | 2026-01-15 | Dr   | INV-1042               |
    | CUST-001  |   $500   | 2026-01-22 | Dr   | INV-1055               |
    | CUST-002  |   $340   | 2026-01-28 | Dr   | INV-1061 (partial)     |
    +-----------+----------+------------+------+------------------------+
```

### "C" — Close Period (guided workflow)

```
  ===================================================================
    PERIOD CLOSE WORKFLOW: 2026-01
  ===================================================================

  Running health check first...
  [health check output as above]

  0 blocking issues. Proceeding with close.
  Year-end close? [y/N]: N

  Acquiring close lock on period 2026-01...
  Close lock acquired. Non-close postings blocked until close completes.
  Close ID: c4d5e6f7-...

  Phase 1/6: CLOSE SUBLEDGERS [requires: PREPARER]
  ----------------------------
    AP .......... CLOSED  (variance: $0.00)
    AR .......... CLOSED  (variance: $0.00)
    Inventory ... CLOSED  (variance: $0.00)
    Bank ........ CLOSED  (variance: $0.00)
    WIP ......... SKIPPED (no entries)
  Guard [ALL_SUBLEDGERS_CLOSED]: PASS

  Phase 2/6: VERIFY TRIAL BALANCE [requires: PREPARER]
  --------------------------------
    Debits:  $1,234,567.89  Credits: $1,234,567.89
    Balanced: YES
  Guard [TRIAL_BALANCE_BALANCED]: PASS

  Phase 3/6: ADJUSTMENTS [requires: PREPARER]
  -----------------------
    Post an adjusting entry? [y/N]: N
    Skipping adjustments.

  Phase 4/6: CLOSING ENTRIES (skipped — not year-end)

  Phase 5/6: CLOSE GL PERIOD [requires: APPROVER]
  ---------------------------
    Period 2026-01 -> CLOSED
    Closed at: 2026-01-31T23:59:00Z
    Closed by: ADMIN-001

  Phase 6/6: LOCK PERIOD (skipped — not year-end)

  ===================================================================
  PERIOD 2026-01 CLOSED

  Close Certificate ID:  c4d5e6f7-...
  Prepared by:           ADMIN-001
  Approved by:           ADMIN-001
  5/6 phases done, 1 skipped
  Correlation ID: a1b2c3d4-...
  Ledger hash (R24): 9f8e7d6c5b4a...
  Certificate audit event: ae-789...

  Close events are traceable via "T". Rejected events via "F".
  Full log: logs/interactive.log (grep a1b2c3d4)
  ===================================================================
```

---

## Files to Create / Modify

| File | Action | Est. Lines | Description |
|------|--------|------------|-------------|
| `finance_services/period_close_orchestrator.py` | CREATE | ~500 | Orchestrator + health check + drilldown + authority + certificate |
| `finance_modules/gl/models.py` | MODIFY | ~100 | DTOs: CloseRunStatus, PeriodCloseRun, ClosePhaseResult, CloseException, HealthCheckResult, CloseCertificate, PeriodCloseResult, CloseRole |
| `finance_kernel/models/fiscal_period.py` | MODIFY | ~15 | Add CLOSING + LOCKED to PeriodStatus, add closing_run_id column |
| `finance_kernel/models/audit_event.py` | MODIFY | ~10 | Add CLOSE_BEGUN, SUBLEDGER_CLOSED, CLOSE_CERTIFIED, CLOSE_CANCELLED to AuditAction |
| `finance_kernel/services/auditor_service.py` | MODIFY | ~40 | Add record_close_begun(), record_subledger_closed(), record_close_certified(), record_close_cancelled() |
| `finance_kernel/services/period_service.py` | MODIFY | ~20 | Extend validate_effective_date() for CLOSING (R25), update is_closed for LOCKED |
| `finance_kernel/exceptions.py` | MODIFY | ~15 | Add PeriodClosingError, CloseAuthorityError |
| `scripts/interactive.py` | MODIFY | ~200 | "C" close handler, "H" health check handler |
| `tests/services/test_period_close_orchestrator.py` | CREATE | ~400 | Tests: health check, phases, guards, drilldown, authority, lock, certificate, idempotency |

### Estimated scope: ~1,300 lines across 9 files.

---

## Implementation Order

1. **Foundation** — Extend PeriodStatus (CLOSING, LOCKED), add closing_run_id, add AuditAction values, add exceptions
2. **R25 enforcement** — Extend validate_effective_date() for CLOSING status
3. **AuditorService methods** — Add 4 new domain-specific record methods
4. **DTOs** — Add all frozen dataclasses to `gl/models.py`
5. **Orchestrator core** — `begin_close()`, `run_phase()`, `close_period_full()`, `cancel_close()`, `get_status()` with logging + audit + lock
6. **Health check** — `health_check()` with integrated drilldown
7. **Authority model** — CloseRole, CloseRoleResolver protocol, phase enforcement
8. **Close certificate** — Build + persist on success
9. **Tests** — Cover each phase, guard failures, exception drilldown, authority, lock exclusivity, certificate, idempotency, year-end vs monthly, resume after crash
10. **CLI "H"** — Health check display (read-only, safe to ship first)
11. **CLI "C"** — Full close workflow handler
12. **Verify** — Full test suite, demo the CLI flow end-to-end

---

## What This Plan Does NOT Include

- **Automatic adjustment generation** — Adjustments require human judgment.
  The orchestrator prompts but does not auto-generate.
- **Depreciation auto-run** — Could be added as a pre-Phase 1 step calling
  `FixedAssetService.run_mass_depreciation()`. Straightforward addition later.
- **FX revaluation auto-run** — Same. Plug in as optional pre-close step.
- **Recurring adjustment templates** — Save and replay common month-end
  accruals. Valuable, but separate feature. Can plug into Phase 3 callback.
- **Multi-entity consolidation** — Runs after individual entity close.
  Separate workflow using `IntercompanyService`.
- **REST/API layer** — CLI-first. API wraps the same orchestrator later.
- **Legal close vs operational close** — The state machine supports a
  certification gate between CLOSED and LOCKED. The `CloseCertificate` is
  the evidentiary artifact that supports formal sign-off. A future plan
  could add a `CERTIFIED` state requiring controller/CFO sign-off before
  LOCKED. The current design makes this additive, not structural.

All of these plug into the orchestrator's existing phase structure without
changing its core.

---

## Invariants Preserved

| Rule | How |
|------|-----|
| R7 (Transaction boundaries) | Each phase commits independently |
| R11 (Audit chain) | AuditorService records each phase transition + close certificate |
| R12 (Closed period enforcement) | PeriodService.close_period() enforces |
| R13 (Adjustment policy) | enable_adjustments() only during Phase 3 |
| R24 (Canonical ledger hash) | Recorded in CloseCertificate at close completion |
| **R25 (Close lock exclusivity)** | **NEW — CLOSING period rejects non-close postings** |
| SL-G6 (Reconciliation) | SubledgerPeriodService enforces before SL close |
| P15 (One outcome per event) | Adjustments/closing entries flow through normal pipeline |
| L5 (Outcome-journal atomicity) | Normal pipeline guarantees this |

---

## Concurrency Model

| Scenario | How it's handled |
|----------|-----------------|
| Two accountants begin close on same period | `SELECT ... FOR UPDATE` on period row. Second blocks, then sees CLOSING status → `PeriodCloseAlreadyInProgressError` |
| Normal posting during close | `validate_effective_date()` sees CLOSING status → `PeriodClosingError` (R25) |
| Crash mid-close | Period remains CLOSING. `get_status()` reconstructs run from PeriodCloseTask records. Resume via `run_phase()` or cancel via `cancel_close()` |
| Cancel close | Requires APPROVER. Period → OPEN, closing_run_id cleared. CLOSE_CANCELLED audit event recorded |
| Post to already-closed period | Existing R12 enforcement (unchanged) |

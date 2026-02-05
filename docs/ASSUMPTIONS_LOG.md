# Assumptions Log

**Generated:** 2026-01-31
**Scope:** Every inferred contract, invariant, or audit boundary documented in source code

**Total entries:** 172 (128 INVARIANT markers, 44 runtime assertions)

**Accuracy (verified 2026-02-04):** The rule descriptions and file locations are still correct, but **line numbers have drifted** in many files due to edits since generation (e.g. `module_posting_service.py` R1/R2 are at 393–394, `journal_writer.py` R5/R21/R9 at 793/821/837, `economic_link.py` L2/L5/L4 at 329/335/343, `period_service.py` R12/R25 at 476/480 and 566/571). Two files with INVARIANT comments are **not** in this table: `finance_kernel/selectors/trace_selector.py` (3 markers) and `finance_kernel/db/types.py` (1 marker). For exact line numbers, grep the codebase for `# INVARIANT` or consider regenerating this log.

| File | Line | Type | Rule | Description |
|------|------|------|------|-------------|
| `finance_kernel/services/auditor_service.py` | 209 | INVARIANT | R1 | INVARIANT: R1 -- Append-only: audit events are immutable once flushed |
| `finance_kernel/services/ingestor_service.py` | 236 | INVARIANT | R1 | INVARIANT: R1 -- Create new immutable event record |
| `finance_kernel/services/module_posting_service.py` | 399 | INVARIANT | R1 | INVARIANT: R1 — Event immutability via IngestorService |
| `finance_kernel/domain/dtos.py` | 306 | INVARIANT | R2.5 | INVARIANT: R2.5 -- payload must be immutable to prevent strategy tampering |
| `finance_kernel/domain/dtos.py` | 671 | INVARIANT | R2.5 | INVARIANT: R2.5 -- all mutable containers must be frozen before strategy access |
| `finance_kernel/services/ingestor_service.py` | 200 | INVARIANT | R2 | INVARIANT: R2 -- Compute payload hash for verification |
| `finance_kernel/services/ingestor_service.py` | 207 | INVARIANT | R2 | INVARIANT: R2 -- Payload hash verification: same event_id + |
| `finance_kernel/services/module_posting_service.py` | 400 | INVARIANT | R2 | INVARIANT: R2 — Payload hash verification via IngestorService |
| `finance_kernel/services/ingestor_service.py` | 203 | INVARIANT | R3 | INVARIANT: R3/R8 -- Check for existing event (idempotency) |
| `finance_kernel/services/journal_writer.py` | 475 | INVARIANT | R3 | INVARIANT: R3 — Idempotency key uniqueness check |
| `finance_engines/allocation.py` | 423 | INVARIANT | R4 | INVARIANT: R4 — total_allocated + unallocated == source_amount |
| `finance_engines/allocation.py` | 424 | assert | R4 | assert total_allocated_money.amount + unallocated.amount == amount.amount, ( |
| `finance_engines/correction/unwind.py` | 163 | INVARIANT | R4 | INVARIANT [R4]: double-entry balance -- debits must equal credits. |
| `finance_engines/reconciliation/domain.py` | 203 | INVARIANT | R4 | INVARIANT [R4]: reject negative application amounts to prevent silent balance corruption. |
| `finance_kernel/domain/accounting_intent.py` | 110 | INVARIANT | R4 | INVARIANT: R4 -- side must be a valid debit or credit literal |
| `finance_kernel/domain/accounting_intent.py` | 113 | INVARIANT | R4 | INVARIANT: R4 -- amounts are always non-negative; side indicates direction |
| `finance_kernel/domain/dtos.py` | 153 | INVARIANT | R4 | INVARIANT: R4 -- line amounts must be non-negative (side indicates direction) |
| `finance_kernel/domain/dtos.py` | 156 | assert | R4 | assert self.money.amount >= Decimal("0"), f"R4 violation: negative line amount {self.money.amount}" |
| `finance_kernel/domain/dtos.py` | 485 | INVARIANT | R4 | INVARIANT: R4 -- debits must equal credits per currency per entry |
| `finance_kernel/domain/values.py` | 130 | INVARIANT | R4 | INVARIANT: R4 -- amount must be Decimal, never float |
| `finance_kernel/domain/values.py` | 143 | assert | R4 | assert isinstance(self.amount, Decimal), f"R4 violation: amount must be Decimal, got {type(self.amount)}" |
| `finance_kernel/domain/values.py` | 470 | INVARIANT | R4 | INVARIANT: R4 -- rate must be Decimal, never float |
| `finance_kernel/domain/values.py` | 481 | assert | R4 | assert isinstance(self.rate, Decimal), f"R4 violation: rate must be Decimal, got {type(self.rate)}" |
| `finance_kernel/services/journal_writer.py` | 428 | INVARIANT | R4 | INVARIANT: R4 — Debits = Credits per currency per entry |
| `finance_modules/cash/service.py` | 164 | INVARIANT | R4 | INVARIANT [R4]: amount passed to kernel; kernel enforces Dr = Cr. |
| `finance_modules/cash/service.py` | 165 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 235 | INVARIANT | R4 | INVARIANT [R4]: amount passed to kernel; kernel enforces Dr = Cr. |
| `finance_modules/cash/service.py` | 236 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 300 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 301 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 360 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 361 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 422 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 423 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 486 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 487 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 548 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 549 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_modules/cash/service.py` | 1002 | INVARIANT | R4 | INVARIANT [R4]: balanced entry enforced by kernel. |
| `finance_modules/cash/service.py` | 1003 | assert | R4 | assert isinstance(amount, Decimal), "amount must be Decimal, not float" |
| `finance_engines/allocation.py` | 382 | INVARIANT | R5 | INVARIANT: R5 — rounding difference assigned to exactly one target |
| `finance_kernel/services/journal_writer.py` | 743 | INVARIANT | R5 | INVARIANT: R5 — At most ONE is_rounding=True line per entry |
| `finance_engines/allocation_cascade.py` | 212 | INVARIANT | R6 | INVARIANT: R6 — input dict is never mutated; copy for local work |
| `finance_kernel/services/module_posting_service.py` | 315 | INVARIANT | R7 | INVARIANT: R7 — Transaction boundaries: commit on success |
| `finance_kernel/services/auditor_service.py` | 175 | INVARIANT | R9 | INVARIANT: R9 -- Sequence monotonicity via locked counter row |
| `finance_kernel/services/auditor_service.py` | 177 | assert | R9 | assert seq > 0, "R9 violation: audit sequence must be strictly positive" |
| `finance_kernel/services/journal_writer.py` | 801 | INVARIANT | R9 | INVARIANT: R9 — Sequence monotonicity via locked counter row |
| `finance_kernel/services/journal_writer.py` | 803 | assert | R9 | assert seq > 0, "R9 violation: sequence must be strictly positive" |
| `finance_kernel/services/sequence_service.py` | 142 | INVARIANT | R9 | INVARIANT: R9 -- Sequence monotonicity via locked counter row. |
| `finance_kernel/services/sequence_service.py` | 184 | INVARIANT | R9 | INVARIANT: R9 -- Increment via locked row, never aggregate-max+1 |
| `finance_kernel/models/cost_lot.py` | 107 | INVARIANT | R10 | INVARIANT R10: Immutable after creation -- remaining derived from links |
| `finance_kernel/models/cost_lot.py` | 120 | INVARIANT | R10 | INVARIANT R10: Immutable after creation |
| `finance_services/correction_service.py` | 226 | INVARIANT | R10 | INVARIANT [R10]: Posted records are never mutated; corrections |
| `finance_services/correction_service.py` | 597 | INVARIANT | R10 | INVARIANT [R10]: Re-check root not corrected (race condition guard). |
| `finance_kernel/services/auditor_service.py` | 185 | INVARIANT | R11 | INVARIANT: R11 -- hash = H(payload_hash + prev_hash) |
| `finance_kernel/services/auditor_service.py` | 193 | assert | R11 | assert event_hash, "R11 violation: event hash must be non-empty" |
| `finance_kernel/services/auditor_service.py` | 463 | INVARIANT | R11 | INVARIANT: R11 -- First event should have no prev_hash (chain genesis) |
| `finance_kernel/services/auditor_service.py` | 472 | INVARIANT | R11 | INVARIANT: R11 -- Validate each event's hash and chain linkage |
| `finance_kernel/services/auditor_service.py` | 496 | INVARIANT | R11 | INVARIANT: R11 -- Validate chain linkage (except for first event) |
| `finance_kernel/services/module_posting_service.py` | 378 | INVARIANT | R12 | INVARIANT: R12 — Closed period enforcement |
| `finance_kernel/services/period_service.py` | 474 | INVARIANT | R12 | INVARIANT: R12 -- Closed period enforcement |
| `finance_kernel/services/period_service.py` | 564 | INVARIANT | R12 | INVARIANT: R12 -- Closed period enforcement |
| `finance_services/correction_service.py` | 399 | INVARIANT | R12 | INVARIANT [R12 / G12]: Period lock check -- corrections to |
| `finance_kernel/services/module_posting_service.py` | 379 | INVARIANT | R13 | INVARIANT: R13 — Adjustment policy enforcement |
| `finance_kernel/services/period_service.py` | 573 | INVARIANT | R13 | INVARIANT: R13 -- Adjustment policy enforcement |
| `finance_kernel/domain/bookkeeper.py` | 208 | INVARIANT | R14 | INVARIANT: R14 -- Strategy lookup via registry, no if/switch on event_type |
| `finance_services/engine_dispatcher.py` | 136 | INVARIANT | R14 | INVARIANT [R14]: Registration-key must match invoker identity. |
| `finance_services/invokers.py` | 400 | INVARIANT | R15 | INVARIANT [R15]: Open/closed compliance -- adding a new engine requires |
| `finance_kernel/domain/values.py` | 63 | INVARIANT | R16 | INVARIANT: R16 -- ISO 4217 enforcement at construction boundary |
| `finance_kernel/domain/values.py` | 137 | INVARIANT | R16 | INVARIANT: R16 -- currency must be a valid ISO 4217 Currency |
| `finance_kernel/domain/values.py` | 464 | INVARIANT | R16 | INVARIANT: R16 -- currencies must be valid ISO 4217 |
| `finance_modules/revenue/service.py` | 163 | INVARIANT | R16 | INVARIANT: Monetary amounts must be Decimal, never float (R16). |
| `finance_modules/revenue/service.py` | 164 | assert | R16 | assert isinstance(total_consideration, Decimal), \ |
| `finance_modules/revenue/service.py` | 268 | INVARIANT | R16 | INVARIANT: Monetary amounts must be Decimal (R16). |
| `finance_modules/revenue/service.py` | 269 | assert | R16 | assert isinstance(base_price, Decimal), "base_price must be Decimal" |
| `finance_modules/revenue/service.py` | 335 | INVARIANT | R16 | INVARIANT: Monetary amounts must be Decimal (R16). |
| `finance_modules/revenue/service.py` | 336 | assert | R16 | assert isinstance(total_price, Decimal), "total_price must be Decimal" |
| `finance_modules/revenue/service.py` | 457 | INVARIANT | R16 | INVARIANT: Recognition amount must be Decimal (R16). |
| `finance_modules/revenue/service.py` | 458 | assert | R16 | assert isinstance(amount, Decimal), "amount must be Decimal" |
| `finance_modules/revenue/service.py` | 536 | INVARIANT | R16 | INVARIANT: Monetary amounts must be Decimal (R16). |
| `finance_modules/revenue/service.py` | 537 | assert | R16 | assert isinstance(price_change, Decimal), "price_change must be Decimal" |
| `finance_modules/revenue/service.py` | 623 | INVARIANT | R16 | INVARIANT: Monetary amounts must be Decimal (R16). |
| `finance_modules/revenue/service.py` | 624 | assert | R16 | assert isinstance(new_estimate, Decimal), "new_estimate must be Decimal" |
| `finance_services/invokers.py` | 85 | INVARIANT | R16 | INVARIANT [R16]: currency propagated; defaults to 'USD' when not specified. |
| `finance_engines/allocation.py` | 372 | INVARIANT | R17 | INVARIANT: R17 — rounding precision derived from currency decimal places |
| `finance_engines/allocation_cascade.py` | 239 | INVARIANT | R17 | INVARIANT: R17 — deterministic rounding to 2 decimal places |
| `finance_engines/billing.py` | 1051 | INVARIANT | R17 | INVARIANT: R17 — precision-derived rounding to 2 decimal places |
| `finance_kernel/domain/values.py` | 88 | INVARIANT | R17 | INVARIANT: R17 -- tolerance derived from currency decimal places |
| `finance_kernel/domain/values.py` | 211 | INVARIANT | R17 | INVARIANT: R17 -- rounding precision derived from currency decimal places |
| `finance_kernel/domain/strategy.py` | 331 | INVARIANT | R21 | INVARIANT: R21 -- reference snapshot versions recorded for replay |
| `finance_kernel/services/journal_writer.py` | 784 | INVARIANT | R21 | INVARIANT: R21 — Reference snapshot determinism |
| `finance_kernel/services/reference_snapshot_service.py` | 146 | INVARIANT | R21 | INVARIANT: R21 -- Reference snapshot determinism: freeze all |
| `finance_kernel/domain/strategy.py` | 295 | INVARIANT | R22 | INVARIANT: R22 -- only the Bookkeeper may generate is_rounding=True lines |
| `finance_kernel/domain/bookkeeper.py` | 100 | INVARIANT | R23 | INVARIANT: R23 -- strategy version must be recorded for replay |
| `finance_kernel/domain/bookkeeper.py` | 101 | assert | R23 | assert strategy_version >= 1, f"R23 violation: strategy_version must be >= 1, got {strategy_version}" |
| `finance_kernel/domain/strategy_registry.py` | 162 | INVARIANT | R23 | INVARIANT: R23 -- validate lifecycle metadata before admission |
| `finance_kernel/services/period_service.py` | 478 | INVARIANT | R25 | INVARIANT: R25 -- CLOSING period blocks non-close postings |
| `finance_kernel/services/period_service.py` | 569 | INVARIANT | R25 | INVARIANT: R25 -- CLOSING period blocks non-close postings |
| `finance_kernel/services/journal_writer.py` | 463 | INVARIANT | L1 | INVARIANT: L1 — Every account role resolves to exactly one COA account |
| `finance_kernel/domain/economic_link.py` | 415 | INVARIANT | L2 | INVARIANT: L2 -- no self-links (parent_ref != child_ref) |
| `finance_kernel/services/link_graph_service.py` | 194 | INVARIANT | L3 | INVARIANT: L3 -- Acyclic enforcement for directed link types |
| `finance_kernel/domain/economic_link.py` | 429 | INVARIANT | L4 | INVARIANT: L4 -- creating_event_id must be present |
| `finance_kernel/domain/economic_link.py` | 430 | assert | L4 | assert self.creating_event_id is not None, ( |
| `finance_kernel/models/economic_event.py` | 161 | INVARIANT | L4 | INVARIANT L4/R21: Reference Snapshot Determinism |
| `finance_kernel/services/reference_snapshot_service.py` | 519 | INVARIANT | L4 | INVARIANT: L4 -- Replay determinism: hash drift means |
| `finance_kernel/domain/economic_link.py` | 421 | INVARIANT | L5 | INVARIANT: L5 -- parent/child types must be valid for this link_type |
| `finance_kernel/services/interpretation_coordinator.py` | 457 | INVARIANT | L5 | INVARIANT: L5 — POSTED outcome in same transaction as journal writes |
| `finance_kernel/services/interpretation_coordinator.py` | 468 | INVARIANT | L5 | INVARIANT: L5 — Both outcome and journal entries must exist together |
| `finance_kernel/services/interpretation_coordinator.py` | 469 | assert | L5 | assert outcome is not None, "L5 violation: POSTED requires an outcome record" |
| `finance_kernel/services/interpretation_coordinator.py` | 470 | assert | L5 | assert journal_result.is_success, "L5 violation: POSTED requires successful journal write" |
| `finance_kernel/services/module_posting_service.py` | 514 | INVARIANT | L5 | INVARIANT: L5 — Atomic journal + outcome via InterpretationCoordinator |
| `finance_kernel/services/module_posting_service.py` | 550 | INVARIANT | L5 | INVARIANT: L5 — POSTED status requires at least one journal entry |
| `finance_kernel/services/module_posting_service.py` | 551 | assert | L5 | assert len(journal_entry_ids) > 0, ( |
| `finance_kernel/services/outcome_recorder.py` | 187 | INVARIANT | L5 | INVARIANT: L5 -- POSTED outcome requires journal entries |
| `finance_kernel/services/outcome_recorder.py` | 188 | assert | L5 | assert len(journal_entry_ids) > 0, ( |
| `finance_kernel/domain/accounting_policy.py` | 258 | INVARIANT | P1 | INVARIANT: P1 -- profile must have a name for unique identification |
| `finance_kernel/domain/accounting_policy.py` | 263 | INVARIANT | P1 | INVARIANT: P1 -- trigger must specify which event type to match |
| `finance_kernel/domain/policy_compiler.py` | 139 | INVARIANT | P1 | INVARIANT: P1 -- overlap detection (exactly one profile per event) |
| `finance_kernel/services/module_posting_service.py` | 426 | INVARIANT | P1 | INVARIANT: P1 — Exactly one EconomicProfile matches any event |
| `finance_kernel/domain/policy_compiler.py` | 150 | INVARIANT | P7 | INVARIANT: P7 -- ledger semantic completeness (required roles provided) |
| `finance_kernel/domain/policy_compiler.py` | 144 | INVARIANT | P10 | INVARIANT: P10 -- field references validated against event schema |
| `finance_kernel/domain/accounting_intent.py` | 366 | INVARIANT | P11 | INVARIANT: P11 -- at least one ledger intent required for atomic posting |
| `finance_kernel/services/interpretation_coordinator.py` | 433 | INVARIANT | P11 | INVARIANT: P11 — Multi-ledger postings from single intent are atomic |
| `finance_kernel/services/module_posting_service.py` | 515 | INVARIANT | P11 | INVARIANT: P11 — Multi-ledger postings are atomic |
| `finance_kernel/domain/meaning_builder.py` | 377 | INVARIANT | P12 | INVARIANT: P12 -- evaluate guards (reject is terminal, block is resumable) |
| `finance_kernel/services/interpretation_coordinator.py` | 458 | INVARIANT | P15 | INVARIANT: P15 — Exactly one InterpretationOutcome per event |
| `finance_kernel/services/outcome_recorder.py` | 185 | INVARIANT | P15 | INVARIANT: P15 -- Exactly one outcome per event |
| `finance_kernel/services/outcome_recorder.py` | 259 | INVARIANT | P15 | INVARIANT: P15 -- Exactly one outcome per event |
| `finance_engines/subledger.py` | 136 | INVARIANT | SL-G1 | INVARIANT [SL-G1]: single-sided entry -- exactly one of debit/credit must be set. |
| `finance_engines/reconciliation/domain.py` | 101 | INVARIANT | SL-G6 | INVARIANT [SL-G6]: remaining = original - applied (within rounding tolerance). |
| `finance_services/subledger_period_service.py` | 238 | INVARIANT | SL-G6 | INVARIANT [SL-G6]: blocking violations prevent close; persist failure report. |
| `finance_kernel/services/module_posting_service.py` | 361 | INVARIANT | G14 | INVARIANT: G14 — Actor authorization at posting boundary |
| `finance_kernel/models/cost_lot.py` | 106 | INVARIANT | C1 | INVARIANT C1: original_quantity > 0 (enforced at service layer) |
| `finance_kernel/models/cost_lot.py` | 119 | INVARIANT | C2 | INVARIANT C2: original_cost >= 0 (enforced at service layer) |
| `finance_kernel/models/cost_lot.py` | 137 | INVARIANT | C3 | INVARIANT C3: source_event_id is NOT NULL -- every lot is traceable |
| `finance_config/__init__.py` | 130 | INVARIANT | -- | INVARIANT: compiled checksum must match assembled source checksum. |
| `finance_config/__init__.py` | 131 | assert | -- | assert pack.checksum == config_set.checksum, ( |
| `finance_config/assembler.py` | 219 | INVARIANT | -- | INVARIANT: checksum must be a non-empty SHA-256 hex digest. |
| `finance_config/assembler.py` | 220 | assert | -- | assert checksum and len(checksum) == 64, ( |
| `finance_engines/billing.py` | 495 | INVARIANT | -- | INVARIANT: fee ceiling enforcement -- billing never exceeds funded amount. |
| `finance_engines/billing.py` | 566 | INVARIANT | -- | INVARIANT: funding limit enforcement -- billing capped at funded amount. |
| `finance_engines/contracts.py` | 36 | assert | -- | assert contract.engine_version == "1.0" |
| `finance_engines/reconciliation/domain.py` | 251 | INVARIANT | -- | INVARIANT: a match requires at least 2 documents; THREE_WAY requires exactly 3. |
| `finance_kernel/domain/accounting_intent.py` | 116 | assert | -- | assert isinstance(self.money.amount, Decimal), ( |
| `finance_kernel/domain/accounting_intent.py` | 369 | assert | -- | assert all(isinstance(li, LedgerIntent) for li in self.ledger_intents), ( |
| `finance_kernel/domain/dtos.py` | 439 | INVARIANT | -- | INVARIANT: Every journal entry must have at least one line |
| `finance_kernel/domain/dtos.py` | 442 | assert | -- | assert len(self.lines) >= 1, "ProposedJournalEntry must have at least one line" |
| `finance_kernel/models/party.py` | 182 | INVARIANT | -- | INVARIANT: Guard enforcement point -- callers MUST check this |
| `finance_kernel/services/interpretation_coordinator.py` | 681 | assert | -- | assert guard_result is not None |
| `finance_kernel/services/interpretation_coordinator.py` | 726 | assert | -- | assert guard_result is not None |
| `finance_kernel/services/reference_snapshot_service.py` | 154 | assert | -- | assert len(snapshot.component_versions) == len(request.include_components), ( |
| `finance_kernel/services/retry_service.py` | 50 | assert | -- | assert outcome.status == OutcomeStatus.RETRYING |
| `finance_kernel/services/retry_service.py` | 129 | INVARIANT | -- | INVARIANT: Safety limit -- prevents infinite retry loops |
| `finance_kernel/services/retry_service.py` | 180 | INVARIANT | -- | INVARIANT: MAX_RETRIES -- Safety limit prevents infinite retry loops |
| `finance_kernel/services/sequence_service.py` | 186 | assert | -- | assert counter.current_value > 0, ( |
| `finance_modules/ap/models.py` | 139 | INVARIANT | -- | INVARIANT: total_amount == subtotal + tax_amount (accounting identity) |
| `finance_modules/ap/models.py` | 155 | INVARIANT | -- | INVARIANT: credit memo cannot carry positive tax on negative subtotal |
| `finance_modules/inventory/models.py` | 150 | INVARIANT | -- | INVARIANT: stock quantities must be non-negative and internally consistent. |
| `finance_modules/revenue/helpers.py` | 64 | INVARIANT | -- | INVARIANT: Method must be one of the two ASC 606 approaches. |
| `finance_modules/revenue/helpers.py` | 65 | assert | -- | assert method in ("expected_value", "most_likely_amount"), \ |
| `finance_modules/revenue/service.py` | 215 | INVARIANT | -- | INVARIANT: At least one deliverable required for obligation identification. |
| `finance_modules/revenue/service.py` | 216 | assert | -- | assert len(deliverables) > 0, "deliverables must not be empty" |
| `finance_modules/revenue/service.py` | 333 | INVARIANT | -- | INVARIANT: At least one obligation required for allocation. |
| `finance_modules/revenue/service.py` | 334 | assert | -- | assert len(obligations) > 0, "obligations must not be empty" |
| `finance_modules/tax/helpers.py` | 47 | assert | -- | assert isinstance(book_basis, Decimal), "book_basis must be Decimal" |
| `finance_modules/tax/helpers.py` | 48 | assert | -- | assert isinstance(tax_basis, Decimal), "tax_basis must be Decimal" |
| `finance_services/correction_service.py` | 579 | INVARIANT | -- | INVARIANT: Dry-run plans must never be executed. |
| `finance_services/posting_orchestrator.py` | 119 | INVARIANT | -- | INVARIANT: single-instance lifecycle -- no duplicate service instances. |
| `finance_services/reconciliation_service.py` | 282 | INVARIANT | -- | INVARIANT: Over-application guard -- applied amount must not |
| `finance_services/reconciliation_service.py` | 290 | INVARIANT | -- | INVARIANT: Amount cannot exceed remaining balance. |
| `finance_services/reconciliation_service.py` | 291 | assert | -- | assert amount.amount >= 0, "Payment amount must be non-negative" |
| `finance_services/reconciliation_service.py` | 478 | INVARIANT | -- | INVARIANT: Tolerance enforcement -- quantity and price variances |

## Rule Coverage Summary

| Rule | Count | Description |
|------|-------|-------------|
| R1 | 3 | Event immutability |
| R2.5 | 2 | Payload immutability |
| R2 | 3 | Payload hash verification |
| R3 | 2 | Idempotency key uniqueness |
| R4 | 30 | Double-entry balance / Decimal enforcement |
| R5 | 2 | Rounding line uniqueness |
| R6 | 1 | Replay safety (no stored balances) |
| R7 | 1 | Transaction boundaries |
| R9 | 6 | Sequence monotonicity |
| R10 | 4 | Posted record immutability |
| R11 | 5 | Audit chain integrity |
| R12 | 4 | Closed period enforcement |
| R13 | 2 | Adjustment policy |
| R14 | 2 | No central dispatch |
| R15 | 1 | Open/closed compliance |
| R16 | 16 | ISO 4217 enforcement |
| R17 | 5 | Precision-derived tolerance |
| R21 | 3 | Reference snapshot determinism |
| R22 | 1 | Rounding line isolation |
| R23 | 3 | Strategy lifecycle governance |
| R25 | 2 | Close lock enforcement |
| L1 | 1 | Role-to-COA resolution |
| L2 | 1 | No self-links |
| L3 | 1 | Acyclic enforcement |
| L4 | 4 | Creating event required / Replay determinism |
| L5 | 10 | Atomic POSTED outcome |
| P1 | 4 | Exactly one profile per event |
| P7 | 1 | Ledger semantic completeness |
| P10 | 1 | Field reference validation |
| P11 | 3 | Multi-ledger atomicity |
| P12 | 1 | Guard evaluation |
| P15 | 3 | One outcome per event |
| SL-G1 | 1 | Single-sided subledger entries |
| SL-G6 | 2 | GL-SL reconciliation |
| G14 | 1 | Actor authorization |
| C1 | 1 | Positive quantity invariant |
| C2 | 1 | Non-negative cost invariant |
| C3 | 1 | Lot traceability |
| -- | 37 | Uncategorized business rule |

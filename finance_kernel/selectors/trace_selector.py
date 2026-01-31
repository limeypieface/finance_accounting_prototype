"""
Trace bundle assembly selector.

Reconstructs the complete lifecycle of any financial artifact by querying
existing ledger tables, event records, audit events, economic links,
interpretation outcomes, and structured logs.

DTOs are defined inline following the pattern established by
journal_selector.py and ledger_selector.py.

Zero new persistent storage. Everything derives from existing infrastructure.
"""

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.models.account import Account
from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.models.economic_event import EconomicEvent
from finance_kernel.models.economic_link import EconomicLinkModel
from finance_kernel.models.event import Event
from finance_kernel.models.interpretation_outcome import InterpretationOutcome
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.selectors.base import BaseSelector
from finance_kernel.utils.hashing import hash_payload, hash_trace_bundle


# ============================================================================
# DTOs (frozen dataclasses, defined inline per selector convention)
# ============================================================================


@dataclass(frozen=True)
class ArtifactIdentifier:
    """Anchor artifact for the trace bundle."""

    artifact_type: str
    artifact_id: UUID


@dataclass(frozen=True)
class OriginEvent:
    """Canonical source event snapshot. Source: events table."""

    event_id: UUID
    event_type: str
    occurred_at: datetime
    effective_date: date
    actor_id: UUID
    producer: str
    payload_hash: str
    schema_version: int
    ingested_at: datetime


@dataclass(frozen=True)
class JournalLineSnapshot:
    """Single journal line snapshot. Source: journal_lines + accounts."""

    line_id: UUID
    account_id: UUID
    account_code: str
    side: str  # "debit" or "credit"
    amount: Decimal
    currency: str
    dimensions: dict | None
    is_rounding: bool
    line_seq: int
    exchange_rate_id: UUID | None


@dataclass(frozen=True)
class JournalEntrySnapshot:
    """Journal entry with lines and account codes. Source: journal_entries."""

    entry_id: UUID
    source_event_id: UUID
    source_event_type: str
    effective_date: date
    occurred_at: datetime
    posted_at: datetime | None
    status: str
    seq: int | None
    idempotency_key: str
    reversal_of_id: UUID | None
    description: str | None
    lines: tuple[JournalLineSnapshot, ...]
    # R21 snapshot fields
    coa_version: int | None
    dimension_schema_version: int | None
    rounding_policy_version: int | None
    currency_registry_version: int | None
    posting_rule_version: int | None


@dataclass(frozen=True)
class InterpretationInfo:
    """Interpretation outcome. Source: interpretation_outcomes."""

    source_event_id: UUID
    status: str
    econ_event_id: UUID | None
    journal_entry_ids: tuple[str, ...] | None
    reason_code: str | None
    reason_detail: dict | None
    profile_id: str
    profile_version: int
    profile_hash: str | None
    trace_id: UUID | None
    decision_log: tuple[dict, ...] | None = None


@dataclass(frozen=True)
class ReproducibilityInfo:
    """R21 reference snapshot for deterministic replay."""

    coa_version: int | None
    dimension_schema_version: int | None
    rounding_policy_version: int | None
    currency_registry_version: int | None
    fx_policy_version: int | None
    posting_rule_version: int | None


@dataclass(frozen=True)
class TimelineEntry:
    """Single action in the trace timeline. Source: audit_events or logs."""

    timestamp: datetime
    source: str  # "audit_event" or "structured_log"
    action: str
    entity_type: str | None
    entity_id: str | None
    detail: dict | None
    seq: int | None  # audit event seq for deterministic ordering


@dataclass(frozen=True)
class LifecycleLink:
    """Economic link snapshot. Source: economic_links."""

    link_id: UUID
    link_type: str
    parent_artifact_type: str
    parent_artifact_id: UUID
    child_artifact_type: str
    child_artifact_id: UUID
    creating_event_id: UUID
    created_at: datetime
    link_metadata: dict | None


@dataclass(frozen=True)
class ConflictInfo:
    """Deduplication or protocol violation. Source: audit_events."""

    action: str
    occurred_at: datetime
    entity_type: str
    entity_id: str
    payload: dict | None


@dataclass(frozen=True)
class IntegrityInfo:
    """Bundle hash and verification results."""

    bundle_hash: str
    payload_hash_verified: bool | None
    balance_verified: bool | None
    audit_chain_segment_valid: bool | None


@dataclass(frozen=True)
class MissingFact:
    """Explicitly declared missing data. Never inferred or invented."""

    fact: str
    expected_source: str
    correlation_key: str | None
    detail: str | None


@dataclass(frozen=True)
class TraceBundle:
    """Top-level trace bundle container."""

    version: str
    trace_id: UUID
    generated_at: datetime
    artifact: ArtifactIdentifier
    origin: OriginEvent | None
    journal_entries: tuple[JournalEntrySnapshot, ...]
    interpretation: InterpretationInfo | None
    reproducibility: ReproducibilityInfo | None
    timeline: tuple[TimelineEntry, ...]
    lifecycle_links: tuple[LifecycleLink, ...]
    conflicts: tuple[ConflictInfo, ...]
    integrity: IntegrityInfo
    missing_facts: tuple[MissingFact, ...]


# ============================================================================
# Log Query Protocol (optional, inline)
# ============================================================================


@runtime_checkable
class LogQueryPort(Protocol):
    """
    Protocol for querying structured logs.

    Optional -- the bundle assembles without it, declaring MissingFact instead.
    Implementors return log records as dicts with at minimum:
    ts/timestamp, message, and any structured fields.
    """

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]: ...

    def query_by_event_id(self, event_id: str) -> list[dict]: ...

    def query_by_trace_id(self, trace_id: str) -> list[dict]: ...


# ============================================================================
# TraceSelector
# ============================================================================


class TraceSelector(BaseSelector[JournalEntry]):
    """
    Read-only trace bundle assembler.

    Reconstructs the complete lifecycle of any financial artifact
    from existing ledger tables, event records, audit events,
    economic links, and structured logs. Zero new persistent storage.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        log_query: LogQueryPort | None = None,
    ):
        super().__init__(session)
        self._clock = clock or SystemClock()
        self._log_query = log_query

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def trace_by_event_id(self, event_id: UUID) -> TraceBundle:
        """Trace by source event ID. Uses uq_event_id index."""
        artifact = ArtifactIdentifier("event", event_id)
        entry_ids = self._find_entry_ids_by_event(event_id)
        return self._assemble_bundle(artifact, event_id, entry_ids)

    def trace_by_journal_entry_id(self, entry_id: UUID) -> TraceBundle:
        """Trace by journal entry ID. Uses primary key index."""
        artifact = ArtifactIdentifier("journal_entry", entry_id)
        entry = self.session.execute(
            select(JournalEntry).where(JournalEntry.id == entry_id)
        ).scalar_one_or_none()

        event_id = entry.source_event_id if entry else None
        return self._assemble_bundle(artifact, event_id, [entry_id])

    def trace_by_artifact_ref(
        self, artifact_type: str, artifact_id: UUID
    ) -> TraceBundle:
        """Trace by arbitrary artifact reference. Uses link indexes."""
        if artifact_type == "event":
            return self.trace_by_event_id(artifact_id)
        if artifact_type == "journal_entry":
            return self.trace_by_journal_entry_id(artifact_id)

        artifact = ArtifactIdentifier(artifact_type, artifact_id)
        event_id, entry_ids = self._resolve_via_links(artifact_type, artifact_id)
        return self._assemble_bundle(artifact, event_id, entry_ids)

    # -----------------------------------------------------------------------
    # Core Assembly
    # -----------------------------------------------------------------------

    def _assemble_bundle(
        self,
        artifact: ArtifactIdentifier,
        event_id: UUID | None,
        entry_ids: list[UUID],
    ) -> TraceBundle:
        """Assemble a complete trace bundle from existing data."""
        missing: list[MissingFact] = []

        # Step 1: Canonical event (Priority 2)
        origin, event_orm = self._resolve_event(event_id, missing)

        # Step 2: Journal entries with lines and account codes (Priority 1)
        journal_entries = self._resolve_journal_entries(event_id, entry_ids, missing)

        # Step 3: Interpretation outcome (Priority 2)
        interpretation = self._resolve_interpretation(event_id, missing)

        # Step 4: Economic event data for R21 enrichment (Priority 2)
        econ_event_data = self._resolve_economic_event_data(event_id)

        # Step 5: Lifecycle links (Priority 3)
        lifecycle_links = self._resolve_lifecycle_links(
            artifact, event_id, entry_ids,
        )

        # Step 6: Audit trail (Priority 5)
        audit_timeline = self._resolve_audit_trail(event_id, entry_ids)

        # Step 7: Structured log entries - optional (Priority 4)
        log_timeline = self._resolve_log_entries(event_id, missing)

        # Step 8: Merge and sort timeline
        timeline = self._build_timeline(audit_timeline, log_timeline)

        # Step 9: Conflicts / protocol violations
        conflicts = self._check_conflicts(event_id)

        # Step 10: Reproducibility (R21 snapshot)
        reproducibility = self._extract_reproducibility(
            journal_entries, econ_event_data,
        )

        # Step 11: Integrity checks
        payload_hash_ok = self._verify_payload_hash(event_orm)
        balance_ok = self._verify_balance(journal_entries)
        chain_ok = self._verify_audit_segment(audit_timeline)

        # Build bundle with placeholder hash
        bundle = TraceBundle(
            version="1.0",
            trace_id=uuid4(),
            generated_at=self._clock.now(),
            artifact=artifact,
            origin=origin,
            journal_entries=tuple(journal_entries),
            interpretation=interpretation,
            reproducibility=reproducibility,
            timeline=tuple(timeline),
            lifecycle_links=tuple(lifecycle_links),
            conflicts=tuple(conflicts),
            integrity=IntegrityInfo(
                bundle_hash="",
                payload_hash_verified=payload_hash_ok,
                balance_verified=balance_ok,
                audit_chain_segment_valid=chain_ok,
            ),
            missing_facts=tuple(missing),
        )

        # Compute deterministic bundle hash and finalize
        bundle_dict = asdict(bundle)
        bundle_hash = hash_trace_bundle(bundle_dict)
        bundle = replace(
            bundle,
            integrity=replace(bundle.integrity, bundle_hash=bundle_hash),
        )

        return bundle

    # -----------------------------------------------------------------------
    # Resolution Methods
    # -----------------------------------------------------------------------

    def _find_entry_ids_by_event(self, event_id: UUID) -> list[UUID]:
        """Find journal entry IDs by source event ID. Uses idx_journal_source_event."""
        rows = self.session.execute(
            select(JournalEntry.id).where(
                JournalEntry.source_event_id == event_id
            )
        ).scalars().all()
        return list(rows)

    def _resolve_via_links(
        self, artifact_type: str, artifact_id: UUID,
    ) -> tuple[UUID | None, list[UUID]]:
        """Resolve event_id and entry_ids via economic links."""
        links = self.session.execute(
            select(EconomicLinkModel).where(
                or_(
                    and_(
                        EconomicLinkModel.parent_artifact_type == artifact_type,
                        EconomicLinkModel.parent_artifact_id == artifact_id,
                    ),
                    and_(
                        EconomicLinkModel.child_artifact_type == artifact_type,
                        EconomicLinkModel.child_artifact_id == artifact_id,
                    ),
                )
            )
        ).scalars().all()

        event_ids: set[UUID] = set()
        entry_ids: list[UUID] = []

        for link in links:
            event_ids.add(link.creating_event_id)
            if link.parent_artifact_type == "journal_entry":
                entry_ids.append(link.parent_artifact_id)
            if link.child_artifact_type == "journal_entry":
                entry_ids.append(link.child_artifact_id)
            if link.parent_artifact_type == "event":
                event_ids.add(link.parent_artifact_id)
            if link.child_artifact_type == "event":
                event_ids.add(link.child_artifact_id)

        event_id = next(iter(event_ids), None)

        if event_id:
            event_entries = self._find_entry_ids_by_event(event_id)
            entry_ids = list(set(entry_ids + event_entries))

        return event_id, entry_ids

    def _resolve_event(
        self, event_id: UUID | None, missing: list[MissingFact],
    ) -> tuple[OriginEvent | None, Event | None]:
        """Load canonical event. Source: events table (uq_event_id)."""
        if event_id is None:
            missing.append(MissingFact(
                fact="ORIGIN_EVENT",
                expected_source="events",
                correlation_key=None,
                detail="No event_id could be resolved for this artifact",
            ))
            return None, None

        event = self.session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one_or_none()

        if event is None:
            missing.append(MissingFact(
                fact="ORIGIN_EVENT",
                expected_source="events",
                correlation_key=str(event_id),
                detail="Event not found in events table",
            ))
            return None, None

        origin = OriginEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            effective_date=event.effective_date,
            actor_id=event.actor_id,
            producer=event.producer,
            payload_hash=event.payload_hash,
            schema_version=event.schema_version,
            ingested_at=event.ingested_at,
        )
        return origin, event

    def _resolve_journal_entries(
        self,
        event_id: UUID | None,
        entry_ids: list[UUID],
        missing: list[MissingFact],
    ) -> list[JournalEntrySnapshot]:
        """Load journal entries with lines and account codes."""
        all_ids: set[UUID] = set(entry_ids)
        if event_id:
            event_entries = self._find_entry_ids_by_event(event_id)
            all_ids.update(event_entries)

        if not all_ids:
            missing.append(MissingFact(
                fact="JOURNAL_ENTRIES",
                expected_source="journal_entries",
                correlation_key=str(event_id) if event_id else None,
                detail="No journal entries found",
            ))
            return []

        entries = self.session.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
                .selectinload(JournalLine.account)
            )
            .where(JournalEntry.id.in_(all_ids))
            .order_by(JournalEntry.seq)
        ).scalars().all()

        snapshots: list[JournalEntrySnapshot] = []
        for entry in entries:
            line_snapshots = tuple(
                JournalLineSnapshot(
                    line_id=line.id,
                    account_id=line.account_id,
                    account_code=(
                        line.account.code if line.account else "UNKNOWN"
                    ),
                    side=(
                        line.side.value
                        if isinstance(line.side, LineSide)
                        else str(line.side)
                    ),
                    amount=line.amount,
                    currency=line.currency,
                    dimensions=line.dimensions,
                    is_rounding=line.is_rounding,
                    line_seq=line.line_seq,
                    exchange_rate_id=line.exchange_rate_id,
                )
                for line in sorted(entry.lines, key=lambda ln: ln.line_seq)
            )

            status_val = (
                entry.status.value
                if isinstance(entry.status, JournalEntryStatus)
                else str(entry.status)
            )

            snapshots.append(JournalEntrySnapshot(
                entry_id=entry.id,
                source_event_id=entry.source_event_id,
                source_event_type=entry.source_event_type,
                effective_date=entry.effective_date,
                occurred_at=entry.occurred_at,
                posted_at=entry.posted_at,
                status=status_val,
                seq=entry.seq,
                idempotency_key=entry.idempotency_key,
                reversal_of_id=entry.reversal_of_id,
                description=entry.description,
                lines=line_snapshots,
                coa_version=entry.coa_version,
                dimension_schema_version=entry.dimension_schema_version,
                rounding_policy_version=entry.rounding_policy_version,
                currency_registry_version=entry.currency_registry_version,
                posting_rule_version=entry.posting_rule_version,
            ))

        return snapshots

    def _resolve_interpretation(
        self, event_id: UUID | None, missing: list[MissingFact],
    ) -> InterpretationInfo | None:
        """Load interpretation outcome. Source: interpretation_outcomes."""
        if event_id is None:
            return None

        outcome = self.session.execute(
            select(InterpretationOutcome).where(
                InterpretationOutcome.source_event_id == event_id
            )
        ).scalar_one_or_none()

        if outcome is None:
            # Not a MissingFact -- events without interpretation don't have outcomes
            return None

        journal_ids = None
        if outcome.journal_entry_ids:
            journal_ids = tuple(str(jid) for jid in outcome.journal_entry_ids)

        decision_log = None
        if outcome.decision_log:
            decision_log = tuple(outcome.decision_log)

        return InterpretationInfo(
            source_event_id=outcome.source_event_id,
            status=(
                outcome.status.value
                if hasattr(outcome.status, "value")
                else str(outcome.status)
            ),
            econ_event_id=outcome.econ_event_id,
            journal_entry_ids=journal_ids,
            reason_code=outcome.reason_code,
            reason_detail=outcome.reason_detail,
            profile_id=outcome.profile_id,
            profile_version=outcome.profile_version,
            profile_hash=outcome.profile_hash,
            trace_id=outcome.trace_id,
            decision_log=decision_log,
        )

    def _resolve_economic_event_data(self, event_id: UUID | None) -> dict | None:
        """Load economic event R21 fields. Source: economic_events."""
        if event_id is None:
            return None

        econ = self.session.execute(
            select(EconomicEvent).where(
                EconomicEvent.source_event_id == event_id
            )
        ).scalar_one_or_none()

        if econ is None:
            return None

        return {
            "coa_version": econ.coa_version,
            "dimension_schema_version": econ.dimension_schema_version,
            "currency_registry_version": econ.currency_registry_version,
            "fx_policy_version": econ.fx_policy_version,
        }

    def _resolve_lifecycle_links(
        self,
        artifact: ArtifactIdentifier,
        event_id: UUID | None,
        entry_ids: list[UUID],
    ) -> list[LifecycleLink]:
        """Load economic links for traced artifacts. Source: economic_links."""
        conditions = [
            and_(
                EconomicLinkModel.parent_artifact_type == artifact.artifact_type,
                EconomicLinkModel.parent_artifact_id == artifact.artifact_id,
            ),
            and_(
                EconomicLinkModel.child_artifact_type == artifact.artifact_type,
                EconomicLinkModel.child_artifact_id == artifact.artifact_id,
            ),
        ]

        for eid in entry_ids:
            conditions.append(and_(
                EconomicLinkModel.parent_artifact_type == "journal_entry",
                EconomicLinkModel.parent_artifact_id == eid,
            ))
            conditions.append(and_(
                EconomicLinkModel.child_artifact_type == "journal_entry",
                EconomicLinkModel.child_artifact_id == eid,
            ))

        if event_id:
            conditions.append(
                EconomicLinkModel.creating_event_id == event_id,
            )

        links = self.session.execute(
            select(EconomicLinkModel).where(or_(*conditions))
        ).scalars().all()

        seen: set[UUID] = set()
        result: list[LifecycleLink] = []
        for link in links:
            if link.id in seen:
                continue
            seen.add(link.id)
            result.append(LifecycleLink(
                link_id=link.id,
                link_type=link.link_type,
                parent_artifact_type=link.parent_artifact_type,
                parent_artifact_id=link.parent_artifact_id,
                child_artifact_type=link.child_artifact_type,
                child_artifact_id=link.child_artifact_id,
                creating_event_id=link.creating_event_id,
                created_at=link.created_at,
                link_metadata=link.link_metadata,
            ))

        return result

    def _resolve_audit_trail(
        self, event_id: UUID | None, entry_ids: list[UUID],
    ) -> list[TimelineEntry]:
        """Load audit events for traced artifacts. Source: audit_events."""
        conditions = []
        if event_id:
            conditions.append(and_(
                AuditEvent.entity_type == "Event",
                AuditEvent.entity_id == event_id,
            ))
        for eid in entry_ids:
            conditions.append(and_(
                AuditEvent.entity_type == "JournalEntry",
                AuditEvent.entity_id == eid,
            ))

        if not conditions:
            return []

        audit_events = self.session.execute(
            select(AuditEvent)
            .where(or_(*conditions))
            .order_by(AuditEvent.seq)
        ).scalars().all()

        return [
            TimelineEntry(
                timestamp=ae.occurred_at,
                source="audit_event",
                action=(
                    ae.action.value
                    if isinstance(ae.action, AuditAction)
                    else str(ae.action)
                ),
                entity_type=ae.entity_type,
                entity_id=str(ae.entity_id),
                detail=ae.payload,
                seq=ae.seq,
            )
            for ae in audit_events
        ]

    def _resolve_log_entries(
        self, event_id: UUID | None, missing: list[MissingFact],
    ) -> list[TimelineEntry]:
        """
        Query structured logs from DB or LogQueryPort.

        Priority order:
        1. InterpretationOutcome.decision_log (persisted on every posting)
        2. LogQueryPort (live capture fallback)
        3. MissingFact if neither source available
        """
        if event_id is None:
            return []

        # Priority 1: Persisted decision_log from InterpretationOutcome
        decision_log = self._load_decision_log(event_id)
        if decision_log:
            return self._records_to_timeline(decision_log)

        # Priority 2: LogQueryPort (live capture)
        if self._log_query is not None:
            try:
                records = self._log_query.query_by_event_id(str(event_id))
            except Exception:
                missing.append(MissingFact(
                    fact="STRUCTURED_LOGS",
                    expected_source="log_query_port",
                    correlation_key=str(event_id),
                    detail="LogQueryPort query failed",
                ))
                return []
            return self._records_to_timeline(records)

        # Neither source available
        missing.append(MissingFact(
            fact="STRUCTURED_LOGS",
            expected_source="interpretation_outcome.decision_log",
            correlation_key=str(event_id),
            detail="No decision_log on outcome and no LogQueryPort provided",
        ))
        return []

    def _load_decision_log(self, event_id: UUID) -> list[dict] | None:
        """Load persisted decision_log from InterpretationOutcome."""
        result = self.session.execute(
            select(InterpretationOutcome.decision_log).where(
                InterpretationOutcome.source_event_id == event_id
            )
        ).scalar_one_or_none()
        return result if result else None

    def _records_to_timeline(
        self, records: list[dict],
    ) -> list[TimelineEntry]:
        """Convert structured log records to TimelineEntry objects."""
        entries: list[TimelineEntry] = []
        for rec in records:
            ts = rec.get("ts") or rec.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if not isinstance(ts, datetime):
                continue

            entries.append(TimelineEntry(
                timestamp=ts,
                source="structured_log",
                action=rec.get("message", "unknown"),
                entity_type=rec.get("entity_type"),
                entity_id=rec.get("entity_id") or rec.get("event_id"),
                detail={
                    k: v for k, v in rec.items()
                    if k not in ("ts", "timestamp", "message")
                },
                seq=None,
            ))

        return entries

    def _build_timeline(
        self,
        audit_entries: list[TimelineEntry],
        log_entries: list[TimelineEntry],
    ) -> list[TimelineEntry]:
        """Merge and sort audit + log timeline entries."""
        combined = audit_entries + log_entries
        return sorted(combined, key=lambda e: (e.timestamp, e.seq or 0))

    def _check_conflicts(self, event_id: UUID | None) -> list[ConflictInfo]:
        """Find protocol violations and dedup conflicts. Source: audit_events."""
        if event_id is None:
            return []

        violations = self.session.execute(
            select(AuditEvent).where(
                and_(
                    AuditEvent.entity_type == "Event",
                    AuditEvent.entity_id == event_id,
                    AuditEvent.action.in_([
                        AuditAction.PROTOCOL_VIOLATION.value,
                        AuditAction.PAYLOAD_MISMATCH.value,
                    ]),
                )
            )
            .order_by(AuditEvent.seq)
        ).scalars().all()

        return [
            ConflictInfo(
                action=(
                    v.action.value
                    if isinstance(v.action, AuditAction)
                    else str(v.action)
                ),
                occurred_at=v.occurred_at,
                entity_type=v.entity_type,
                entity_id=str(v.entity_id),
                payload=v.payload,
            )
            for v in violations
        ]

    def _extract_reproducibility(
        self,
        entries: list[JournalEntrySnapshot],
        econ_event_data: dict | None,
    ) -> ReproducibilityInfo | None:
        """Extract R21 snapshot from journal entries and economic event."""
        if not entries and not econ_event_data:
            return None

        coa_version = None
        dim_version = None
        rounding_version = None
        currency_version = None
        posting_rule_version = None

        for snap in entries:
            if snap.coa_version is not None:
                coa_version = snap.coa_version
                dim_version = snap.dimension_schema_version
                rounding_version = snap.rounding_policy_version
                currency_version = snap.currency_registry_version
                posting_rule_version = snap.posting_rule_version
                break

        fx_version = None
        if econ_event_data:
            coa_version = coa_version or econ_event_data.get("coa_version")
            dim_version = dim_version or econ_event_data.get(
                "dimension_schema_version",
            )
            currency_version = currency_version or econ_event_data.get(
                "currency_registry_version",
            )
            fx_version = econ_event_data.get("fx_policy_version")

        return ReproducibilityInfo(
            coa_version=coa_version,
            dimension_schema_version=dim_version,
            rounding_policy_version=rounding_version,
            currency_registry_version=currency_version,
            fx_policy_version=fx_version,
            posting_rule_version=posting_rule_version,
        )

    # -----------------------------------------------------------------------
    # Integrity Verification
    # -----------------------------------------------------------------------

    def _verify_payload_hash(self, event: Event | None) -> bool | None:
        """Verify event payload hash matches stored hash."""
        if event is None or event.payload is None:
            return None
        recomputed = hash_payload(event.payload)
        return recomputed == event.payload_hash

    def _verify_balance(
        self, entries: list[JournalEntrySnapshot],
    ) -> bool | None:
        """Verify all journal entries are balanced (debits == credits per currency)."""
        if not entries:
            return None

        for entry in entries:
            currency_totals: dict[str, tuple[Decimal, Decimal]] = {}
            for line in entry.lines:
                debits, credits = currency_totals.get(
                    line.currency, (Decimal("0"), Decimal("0")),
                )
                if line.side == "debit":
                    debits += line.amount
                else:
                    credits += line.amount
                currency_totals[line.currency] = (debits, credits)

            for _currency, (debits, credits) in currency_totals.items():
                if debits != credits:
                    return False

        return True

    def _verify_audit_segment(
        self, audit_entries: list[TimelineEntry],
    ) -> bool | None:
        """Verify audit trail segment has monotonically increasing sequences."""
        if not audit_entries:
            return None

        seqs = [e.seq for e in audit_entries if e.seq is not None]
        if len(seqs) < 2:
            return True

        for i in range(1, len(seqs)):
            if seqs[i] <= seqs[i - 1]:
                return False

        return True

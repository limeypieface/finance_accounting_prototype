"""
ReferenceSnapshotService -- captures and retrieves frozen economic reality.

Responsibility:
    Captures the current state of all reference data components (COA,
    dimension schemas, FX rates, rounding policy, tax rules, policy
    registry, account roles) into an immutable ``ReferenceSnapshot``
    with deterministic content hashes.  Provides snapshot retrieval
    for replay and integrity validation for drift detection.

Architecture position:
    Kernel > Services -- imperative shell.
    Called by InterpretationCoordinator during the posting pipeline
    to freeze economic reality at posting time (R21).  Also called
    during replay to verify determinism (L4).

Invariants enforced:
    R21 -- Reference snapshot determinism: every JournalEntry records
           the snapshot version IDs at posting time.  This service
           produces the snapshot that is attached to the entry.
    L4  -- Replay determinism: replaying with a stored snapshot must
           produce identical results.  ``validate_integrity()`` detects
           any drift between the stored snapshot and current data.
    R7  -- Flush-only: never commits or rolls back the session.

Failure modes:
    - SnapshotIntegrityError: Content hash mismatch detected during
      ``validate_integrity()`` (reference data has changed since snapshot).
    - Cache miss: ``get(snapshot_id)`` returns None if the snapshot was
      not captured in this service instance's lifetime.

Audit relevance:
    Snapshots provide the forensic anchor for "what data was in effect
    when this entry was posted."  Content hashes enable tamper detection.
    Every snapshot is identified by a unique ``snapshot_id`` (UUID) and
    records ``captured_at`` and ``captured_by``.

Usage:
    service = ReferenceSnapshotService(session, clock)

    # Capture current state
    snapshot = service.capture(SnapshotRequest.all_components(actor_id))

    # Use snapshot for posting
    meaning_builder.build(event, snapshot)

    # Later: replay with same snapshot
    old_snapshot = service.get(snapshot_id)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.reference_snapshot import (
    ComponentVersion,
    ReferenceSnapshot,
    SnapshotComponentType,
    SnapshotIntegrityError,
    SnapshotRequest,
    SnapshotValidationResult,
)


class ReferenceSnapshotService:
    """
    Service for capturing and retrieving reference snapshots.

    Contract:
        Accepts a ``SnapshotRequest`` and returns a frozen
        ``ReferenceSnapshot`` with deterministic content hashes.
        Provides retrieval by ``snapshot_id`` and integrity validation.

    Guarantees:
        - R21: Every captured snapshot includes a unique ``snapshot_id``,
          ``captured_at`` timestamp, and content hash per component.
        - L4: ``validate_integrity()`` detects any change in reference data
          since the snapshot was taken by recomputing content hashes.
        - Deterministic hashing: JSON serialization with ``sort_keys=True``
          ensures the same data produces the same hash.

    Non-goals:
        - Does NOT persist snapshots to a database table (currently
          in-memory cache; production would persist).
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        compiled_pack: CompiledPolicyPack | None = None,
    ):
        """
        Initialize the service.

        Args:
            session: SQLAlchemy session for database access.
            clock: Clock for timestamps. Defaults to SystemClock.
            compiled_pack: Compiled policy pack for policy/role snapshots.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._compiled_pack = compiled_pack
        self._snapshot_cache: dict[UUID, ReferenceSnapshot] = {}

    def capture(self, request: SnapshotRequest) -> ReferenceSnapshot:
        """
        Capture a new reference snapshot.

        Reads current state of all requested components, computes content
        hashes, and creates an immutable snapshot.

        Preconditions:
            - ``request.include_components`` is non-empty.
            - ``request.requested_by`` is a valid actor identifier.

        Postconditions:
            - Returns a ``ReferenceSnapshot`` with a unique ``snapshot_id``
              and one ``ComponentVersion`` per requested component.
            - Each ``ComponentVersion`` has a deterministic ``content_hash`` (R21).
            - The snapshot is cached for fast retrieval via ``get()``.

        Args:
            request: SnapshotRequest specifying what to capture.

        Returns:
            ReferenceSnapshot with unique snapshot_id.
        """
        captured_at = request.as_of or self._clock.now()
        snapshot_id = uuid4()

        component_versions: list[ComponentVersion] = []

        for component_type in request.include_components:
            cv = self._capture_component(component_type, captured_at)
            component_versions.append(cv)

        # INVARIANT: R21 -- Reference snapshot determinism: freeze all
        # component versions with content hashes at capture time
        snapshot = ReferenceSnapshot(
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            captured_by=request.requested_by,
            component_versions=tuple(component_versions),
        )
        assert len(snapshot.component_versions) == len(request.include_components), (
            "R21 violation: snapshot must have one version per requested component"
        )

        # Cache for fast retrieval
        self._snapshot_cache[snapshot_id] = snapshot

        return snapshot

    def _capture_component(
        self,
        component_type: SnapshotComponentType,
        as_of: datetime,
    ) -> ComponentVersion:
        """
        Capture a single component's version and hash.

        Dispatches to component-specific capture methods.
        """
        if component_type == SnapshotComponentType.COA:
            return self._capture_coa(as_of)
        elif component_type == SnapshotComponentType.DIMENSION_SCHEMA:
            return self._capture_dimension_schema(as_of)
        elif component_type == SnapshotComponentType.FX_RATES:
            return self._capture_fx_rates(as_of)
        elif component_type == SnapshotComponentType.ROUNDING_POLICY:
            return self._capture_rounding_policy(as_of)
        elif component_type == SnapshotComponentType.TAX_RULES:
            return self._capture_tax_rules(as_of)
        elif component_type == SnapshotComponentType.POLICY_REGISTRY:
            return self._capture_policy_registry(as_of)
        elif component_type == SnapshotComponentType.ACCOUNT_ROLES:
            return self._capture_account_roles(as_of)
        else:
            # Default fallback
            return ComponentVersion(
                component_type=component_type,
                version=1,
                content_hash=self._compute_hash({"component": component_type.value}),
                effective_from=as_of,
            )

    def _capture_coa(self, as_of: datetime) -> ComponentVersion:
        """Capture Chart of Accounts state."""
        from finance_kernel.models.account import Account

        accounts = self._session.execute(
            select(Account).order_by(Account.code)
        ).scalars().all()

        # Build deterministic representation
        coa_state = [
            {
                "code": acc.code,
                "name": acc.name,
                "account_type": acc.account_type.value if acc.account_type else None,
                "is_active": acc.is_active,
                "currency": acc.currency,
            }
            for acc in accounts
        ]

        return ComponentVersion(
            component_type=SnapshotComponentType.COA,
            version=self._get_coa_version(),
            content_hash=self._compute_hash(coa_state),
            effective_from=as_of,
        )

    def _capture_dimension_schema(self, as_of: datetime) -> ComponentVersion:
        """Capture dimension schema state."""
        from finance_kernel.models.dimensions import Dimension, DimensionValue

        dimensions = self._session.execute(
            select(Dimension).order_by(Dimension.code)
        ).scalars().all()

        values = self._session.execute(
            select(DimensionValue).order_by(
                DimensionValue.dimension_code, DimensionValue.code
            )
        ).scalars().all()

        schema_state = {
            "dimensions": [
                {
                    "code": dim.code,
                    "name": dim.name,
                    "is_active": dim.is_active,
                    "is_required": dim.is_required,
                }
                for dim in dimensions
            ],
            "values": [
                {
                    "dimension_code": val.dimension_code,
                    "code": val.code,
                    "name": val.name,
                    "is_active": val.is_active,
                }
                for val in values
            ],
        }

        return ComponentVersion(
            component_type=SnapshotComponentType.DIMENSION_SCHEMA,
            version=self._get_dimension_schema_version(),
            content_hash=self._compute_hash(schema_state),
            effective_from=as_of,
        )

    def _capture_fx_rates(self, as_of: datetime) -> ComponentVersion:
        """Capture exchange rate state."""
        from finance_kernel.models.exchange_rate import ExchangeRate

        rates = self._session.execute(
            select(ExchangeRate)
            .where(ExchangeRate.effective_at <= as_of)
            .order_by(
                ExchangeRate.from_currency,
                ExchangeRate.to_currency,
                ExchangeRate.effective_at.desc(),
            )
        ).scalars().all()

        # Deduplicate to most recent rate per pair
        seen: set[tuple[str, str]] = set()
        rate_state: list[dict[str, Any]] = []

        for rate in rates:
            key = (rate.from_currency, rate.to_currency)
            if key not in seen:
                rate_state.append({
                    "from": rate.from_currency,
                    "to": rate.to_currency,
                    "rate": str(rate.rate),
                    "effective_at": rate.effective_at.isoformat(),
                })
                seen.add(key)

        return ComponentVersion(
            component_type=SnapshotComponentType.FX_RATES,
            version=self._get_fx_rates_version(),
            content_hash=self._compute_hash(rate_state),
            effective_from=as_of,
        )

    def _capture_rounding_policy(self, as_of: datetime) -> ComponentVersion:
        """Capture rounding policy state."""
        from finance_kernel.domain.currency import CurrencyRegistry

        # Rounding policy is currently embedded in CurrencyRegistry
        policy_state = {
            "currencies": [
                {
                    "code": code,
                    "decimal_places": CurrencyRegistry.get_decimal_places(code),
                    "rounding_tolerance": str(
                        CurrencyRegistry.get_rounding_tolerance(code)
                    ),
                }
                for code in sorted(CurrencyRegistry.all_codes())
            ],
        }

        return ComponentVersion(
            component_type=SnapshotComponentType.ROUNDING_POLICY,
            version=1,  # Currently static
            content_hash=self._compute_hash(policy_state),
            effective_from=as_of,
        )

    def _capture_tax_rules(self, as_of: datetime) -> ComponentVersion:
        """Capture tax rules from compiled policy pack.

        Tax rules are expressed as YAML policies compiled into the pack.
        Captures all tax-trigger policies deterministically.
        """
        if self._compiled_pack is None:
            return ComponentVersion(
                component_type=SnapshotComponentType.TAX_RULES,
                version=1,
                content_hash=self._compute_hash({"rules": []}),
                effective_from=as_of,
            )

        tax_policies = [
            {
                "name": p.name,
                "version": p.version,
                "trigger_event_type": p.trigger.event_type if p.trigger else None,
                "scope": p.scope,
                "module": p.module,
                "required_engines": list(p.required_engines),
            }
            for p in self._compiled_pack.policies
            if p.module == "tax"
        ]

        return ComponentVersion(
            component_type=SnapshotComponentType.TAX_RULES,
            version=self._compiled_pack.config_version,
            content_hash=self._compute_hash({"rules": tax_policies}),
            effective_from=as_of,
        )

    def _capture_policy_registry(self, as_of: datetime) -> ComponentVersion:
        """Capture the full compiled policy registry.

        Serializes all policies from the CompiledPolicyPack deterministically
        so that any policy change produces a different content hash.
        """
        if self._compiled_pack is None:
            return ComponentVersion(
                component_type=SnapshotComponentType.POLICY_REGISTRY,
                version=1,
                content_hash=self._compute_hash({"policies": []}),
                effective_from=as_of,
            )

        policy_state = [
            {
                "name": p.name,
                "version": p.version,
                "trigger_event_type": p.trigger.event_type if p.trigger else None,
                "scope": p.scope,
                "module": p.module,
                "required_engines": list(p.required_engines),
                "capability_tags": list(p.capability_tags),
                "description": p.description,
            }
            for p in self._compiled_pack.policies
        ]

        return ComponentVersion(
            component_type=SnapshotComponentType.POLICY_REGISTRY,
            version=self._compiled_pack.config_version,
            content_hash=self._compute_hash({"policies": policy_state}),
            effective_from=as_of,
        )

    def _capture_account_roles(self, as_of: datetime) -> ComponentVersion:
        """Capture account role bindings from the compiled policy pack.

        Role bindings map abstract roles (CASH, INVENTORY, REVENUE) to
        concrete account codes per ledger. Any binding change produces a
        different content hash.
        """
        if self._compiled_pack is None:
            return ComponentVersion(
                component_type=SnapshotComponentType.ACCOUNT_ROLES,
                version=1,
                content_hash=self._compute_hash({"roles": []}),
                effective_from=as_of,
            )

        role_state = [
            {
                "role": rb.role,
                "ledger": rb.ledger,
                "account_code": rb.account_code,
                "effective_from": str(rb.effective_from),
                "effective_to": str(rb.effective_to) if rb.effective_to else None,
            }
            for rb in self._compiled_pack.role_bindings
        ]

        return ComponentVersion(
            component_type=SnapshotComponentType.ACCOUNT_ROLES,
            version=self._compiled_pack.config_version,
            content_hash=self._compute_hash({"roles": role_state}),
            effective_from=as_of,
        )

    def _get_coa_version(self) -> int:
        """Get current COA version from record count.

        Uses active account count as a proxy for schema version.
        Any account addition/removal changes the version.
        Content hash provides the true integrity check.
        """
        from sqlalchemy import func

        from finance_kernel.models.account import Account

        count = self._session.execute(
            select(func.count()).select_from(Account)
        ).scalar_one()
        return max(1, count)

    def _get_dimension_schema_version(self) -> int:
        """Get current dimension schema version from record count."""
        from sqlalchemy import func

        from finance_kernel.models.dimensions import Dimension

        count = self._session.execute(
            select(func.count()).select_from(Dimension)
        ).scalar_one()
        return max(1, count)

    def _get_fx_rates_version(self) -> int:
        """Get current FX rates version from record count."""
        from sqlalchemy import func

        from finance_kernel.models.exchange_rate import ExchangeRate

        count = self._session.execute(
            select(func.count()).select_from(ExchangeRate)
        ).scalar_one()
        return max(1, count)

    def _compute_hash(self, data: Any) -> str:
        """
        Compute SHA-256 hash of data.

        Uses deterministic JSON serialization for consistency.
        """
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def get(self, snapshot_id: UUID) -> ReferenceSnapshot | None:
        """
        Retrieve a snapshot by ID.

        Args:
            snapshot_id: The snapshot ID to retrieve.

        Returns:
            ReferenceSnapshot if found, None otherwise.

        Note:
            Currently uses in-memory cache. Production implementation
            would persist snapshots to database.
        """
        return self._snapshot_cache.get(snapshot_id)

    def validate_integrity(
        self, snapshot: ReferenceSnapshot
    ) -> SnapshotValidationResult:
        """
        Validate that a snapshot still matches current data.

        Used to detect if reference data has changed since snapshot.
        This is the L4 enforcement point: if any component's content
        hash has drifted, replay would produce different results.

        Preconditions:
            - ``snapshot`` is a valid ``ReferenceSnapshot`` with at
              least one ``ComponentVersion``.

        Postconditions:
            - Returns ``valid`` if and only if every component's
              current content hash matches the stored hash (L4).
            - Returns ``invalid`` with specific error details if any
              component has drifted.

        Args:
            snapshot: The snapshot to validate.

        Returns:
            SnapshotValidationResult with any integrity errors.
        """
        errors: list[SnapshotIntegrityError] = []

        for cv in snapshot.component_versions:
            current = self._capture_component(cv.component_type, snapshot.captured_at)

            # INVARIANT: L4 -- Replay determinism: hash drift means
            # replaying with this snapshot would produce different results
            if current.content_hash != cv.content_hash:
                errors.append(
                    SnapshotIntegrityError(
                        snapshot_id=snapshot.snapshot_id,
                        component_type=cv.component_type,
                        expected_hash=cv.content_hash,
                        actual_hash=current.content_hash,
                        message=(
                            f"{cv.component_type.value} has changed since snapshot. "
                            f"Expected hash {cv.content_hash[:8]}..., "
                            f"got {current.content_hash[:8]}..."
                        ),
                    )
                )

        if errors:
            return SnapshotValidationResult.invalid(
                snapshot.snapshot_id, tuple(errors)
            )
        return SnapshotValidationResult.valid(snapshot.snapshot_id)

    def get_or_capture(
        self,
        snapshot_id: UUID | None,
        request: SnapshotRequest,
    ) -> ReferenceSnapshot:
        """
        Get existing snapshot or capture new one.

        Convenience method for idempotent snapshot handling.

        Args:
            snapshot_id: Optional existing snapshot ID.
            request: Request to use if capturing new snapshot.

        Returns:
            Existing or newly captured ReferenceSnapshot.
        """
        if snapshot_id:
            existing = self.get(snapshot_id)
            if existing:
                return existing

        return self.capture(request)

"""
Reference Snapshot Service - Captures and retrieves frozen economic reality.

The ReferenceSnapshotService is responsible for:
- Capturing current state of all reference data components
- Computing content hashes for integrity
- Issuing immutable snapshot_ids
- Retrieving snapshots for replay

This service bridges the database layer and the pure domain layer.
All snapshot data is immutable once captured.

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
from datetime import datetime
from typing import TYPE_CHECKING, Any
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

    Captures the current state of reference data and creates immutable
    snapshots that can be used for posting and replay.

    All operations are transactional within the caller's transaction boundary.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
    ):
        """
        Initialize the service.

        Args:
            session: SQLAlchemy session for database access.
            clock: Clock for timestamps. Defaults to SystemClock.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._snapshot_cache: dict[UUID, ReferenceSnapshot] = {}

    def capture(self, request: SnapshotRequest) -> ReferenceSnapshot:
        """
        Capture a new reference snapshot.

        Reads current state of all requested components, computes content
        hashes, and creates an immutable snapshot.

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

        snapshot = ReferenceSnapshot(
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            captured_by=request.requested_by,
            component_versions=tuple(component_versions),
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
        """Capture tax rules state."""
        # Tax rules not yet implemented - return placeholder
        return ComponentVersion(
            component_type=SnapshotComponentType.TAX_RULES,
            version=1,
            content_hash=self._compute_hash({"rules": []}),
            effective_from=as_of,
        )

    def _capture_policy_registry(self, as_of: datetime) -> ComponentVersion:
        """Capture policy registry state."""
        # Policy registry not yet implemented - return placeholder
        return ComponentVersion(
            component_type=SnapshotComponentType.POLICY_REGISTRY,
            version=1,
            content_hash=self._compute_hash({"policies": []}),
            effective_from=as_of,
        )

    def _capture_account_roles(self, as_of: datetime) -> ComponentVersion:
        """Capture account role mappings state."""
        # Account roles not yet implemented - return placeholder
        return ComponentVersion(
            component_type=SnapshotComponentType.ACCOUNT_ROLES,
            version=1,
            content_hash=self._compute_hash({"roles": []}),
            effective_from=as_of,
        )

    def _get_coa_version(self) -> int:
        """Get current COA version."""
        # TODO: Read from version tracking table when implemented
        return 1

    def _get_dimension_schema_version(self) -> int:
        """Get current dimension schema version."""
        # TODO: Read from version tracking table when implemented
        return 1

    def _get_fx_rates_version(self) -> int:
        """Get current FX rates version."""
        # TODO: Read from version tracking table when implemented
        return 1

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

        Args:
            snapshot: The snapshot to validate.

        Returns:
            SnapshotValidationResult with any integrity errors.
        """
        errors: list[SnapshotIntegrityError] = []

        for cv in snapshot.component_versions:
            current = self._capture_component(cv.component_type, snapshot.captured_at)

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

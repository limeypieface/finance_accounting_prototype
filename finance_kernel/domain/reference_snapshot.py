"""ReferenceSnapshot -- Frozen, named version of economic reality."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class SnapshotComponentType(str, Enum):
    """Types of components that can be captured in a snapshot."""

    COA = "coa"  # Chart of Accounts
    DIMENSION_SCHEMA = "dimension_schema"  # Dimension definitions
    FX_RATES = "fx_rates"  # Exchange rate table
    TAX_RULES = "tax_rules"  # Tax rule definitions
    POLICY_REGISTRY = "policy_registry"  # Economic policies
    ROUNDING_POLICY = "rounding_policy"  # Rounding rules
    ACCOUNT_ROLES = "account_roles"  # Account role mappings
    CONFIGURATION_SET = "configuration_set"  # AccountingConfigurationSet ID + checksum


@dataclass(frozen=True, slots=True)
class ComponentVersion:
    """Version information for a single snapshot component."""

    component_type: SnapshotComponentType
    version: int
    content_hash: str  # SHA-256 of component state
    effective_from: datetime
    effective_to: datetime | None = None  # None = current

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError(f"Version must be >= 1, got {self.version}")
        if not self.content_hash:
            raise ValueError("Content hash is required")


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    """Immutable snapshot of all reference data at a point in time (R21, L4)."""

    snapshot_id: UUID
    captured_at: datetime
    captured_by: UUID  # Actor who captured this snapshot
    component_versions: tuple[ComponentVersion, ...]

    # Derived version accessors for backward compatibility
    @property
    def coa_version(self) -> int:
        """Get COA version from component versions."""
        return self._get_version(SnapshotComponentType.COA)

    @property
    def dimension_schema_version(self) -> int:
        """Get dimension schema version from component versions."""
        return self._get_version(SnapshotComponentType.DIMENSION_SCHEMA)

    @property
    def fx_rates_version(self) -> int:
        """Get FX rates version from component versions."""
        return self._get_version(SnapshotComponentType.FX_RATES)

    @property
    def tax_rules_version(self) -> int:
        """Get tax rules version from component versions."""
        return self._get_version(SnapshotComponentType.TAX_RULES)

    @property
    def policy_registry_version(self) -> int:
        """Get policy registry version from component versions."""
        return self._get_version(SnapshotComponentType.POLICY_REGISTRY)

    @property
    def rounding_policy_version(self) -> int:
        """Get rounding policy version from component versions."""
        return self._get_version(SnapshotComponentType.ROUNDING_POLICY)

    @property
    def currency_registry_version(self) -> int:
        """Alias for fx_rates_version for R21 compatibility."""
        return self.fx_rates_version

    def _get_version(self, component_type: SnapshotComponentType) -> int:
        """Get version for a specific component type."""
        for cv in self.component_versions:
            if cv.component_type == component_type:
                return cv.version
        # Default to 1 if component not present (backward compatibility)
        return 1

    def get_component(
        self, component_type: SnapshotComponentType
    ) -> ComponentVersion | None:
        """Get full component version info."""
        for cv in self.component_versions:
            if cv.component_type == component_type:
                return cv
        return None

    @property
    def version_dict(self) -> dict[str, int]:
        """Get all versions as a dictionary for R21 persistence."""
        return {
            "coa_version": self.coa_version,
            "dimension_schema_version": self.dimension_schema_version,
            "rounding_policy_version": self.rounding_policy_version,
            "currency_registry_version": self.currency_registry_version,
        }

    def is_compatible_with(self, other: ReferenceSnapshot) -> bool:
        """Check if this snapshot is compatible with another for replay."""
        return self.version_dict == other.version_dict


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    """Request to capture a new reference snapshot."""

    requested_by: UUID
    include_components: frozenset[SnapshotComponentType] = field(
        default_factory=lambda: frozenset(SnapshotComponentType)
    )
    as_of: datetime | None = None  # None = now

    @classmethod
    def all_components(cls, requested_by: UUID) -> SnapshotRequest:
        """Request snapshot of all components."""
        return cls(
            requested_by=requested_by,
            include_components=frozenset(SnapshotComponentType),
        )

    @classmethod
    def minimal(cls, requested_by: UUID) -> SnapshotRequest:
        """Request minimal snapshot (COA + dimensions + rounding only)."""
        return cls(
            requested_by=requested_by,
            include_components=frozenset({
                SnapshotComponentType.COA,
                SnapshotComponentType.DIMENSION_SCHEMA,
                SnapshotComponentType.ROUNDING_POLICY,
            }),
        )


@dataclass(frozen=True, slots=True)
class SnapshotIntegrityError:
    """Error when snapshot integrity check fails."""

    snapshot_id: UUID
    component_type: SnapshotComponentType
    expected_hash: str
    actual_hash: str
    message: str


class SnapshotValidationResult:
    """Result of validating a snapshot's integrity."""

    def __init__(
        self,
        snapshot_id: UUID,
        is_valid: bool,
        errors: tuple[SnapshotIntegrityError, ...] = (),
    ):
        self.snapshot_id = snapshot_id
        self.is_valid = is_valid
        self.errors = errors

    @classmethod
    def valid(cls, snapshot_id: UUID) -> SnapshotValidationResult:
        return cls(snapshot_id=snapshot_id, is_valid=True)

    @classmethod
    def invalid(
        cls, snapshot_id: UUID, errors: tuple[SnapshotIntegrityError, ...]
    ) -> SnapshotValidationResult:
        return cls(snapshot_id=snapshot_id, is_valid=False, errors=errors)

"""
Tests for Reference Snapshot Service.

Tests the foundational reference snapshot system that captures
frozen versions of economic reality for deterministic replay.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.reference_snapshot import (
    ComponentVersion,
    ReferenceSnapshot,
    SnapshotComponentType,
    SnapshotIntegrityError,
    SnapshotRequest,
    SnapshotValidationResult,
)


class TestComponentVersion:
    """Tests for ComponentVersion value object."""

    def test_create_valid_component_version(self):
        """Should create valid component version."""
        cv = ComponentVersion(
            component_type=SnapshotComponentType.COA,
            version=1,
            content_hash="abc123def456",
            effective_from=datetime.now(),
        )

        assert cv.component_type == SnapshotComponentType.COA
        assert cv.version == 1
        assert cv.content_hash == "abc123def456"
        assert cv.effective_to is None

    def test_reject_invalid_version(self):
        """Should reject version < 1."""
        with pytest.raises(ValueError, match="Version must be >= 1"):
            ComponentVersion(
                component_type=SnapshotComponentType.COA,
                version=0,
                content_hash="abc123",
                effective_from=datetime.now(),
            )

    def test_reject_empty_hash(self):
        """Should reject empty content hash."""
        with pytest.raises(ValueError, match="Content hash is required"):
            ComponentVersion(
                component_type=SnapshotComponentType.COA,
                version=1,
                content_hash="",
                effective_from=datetime.now(),
            )

    def test_component_version_immutable(self):
        """Should be immutable (frozen dataclass)."""
        cv = ComponentVersion(
            component_type=SnapshotComponentType.COA,
            version=1,
            content_hash="abc123",
            effective_from=datetime.now(),
        )

        with pytest.raises(AttributeError):
            cv.version = 2


class TestReferenceSnapshot:
    """Tests for ReferenceSnapshot value object."""

    @pytest.fixture
    def sample_components(self) -> tuple[ComponentVersion, ...]:
        """Create sample component versions."""
        now = datetime.now()
        return (
            ComponentVersion(
                component_type=SnapshotComponentType.COA,
                version=3,
                content_hash="coa_hash_123",
                effective_from=now,
            ),
            ComponentVersion(
                component_type=SnapshotComponentType.DIMENSION_SCHEMA,
                version=2,
                content_hash="dim_hash_456",
                effective_from=now,
            ),
            ComponentVersion(
                component_type=SnapshotComponentType.FX_RATES,
                version=5,
                content_hash="fx_hash_789",
                effective_from=now,
            ),
            ComponentVersion(
                component_type=SnapshotComponentType.ROUNDING_POLICY,
                version=1,
                content_hash="round_hash_abc",
                effective_from=now,
            ),
        )

    def test_create_snapshot(self, sample_components):
        """Should create snapshot with all components."""
        snapshot_id = uuid4()
        captured_by = uuid4()
        now = datetime.now()

        snapshot = ReferenceSnapshot(
            snapshot_id=snapshot_id,
            captured_at=now,
            captured_by=captured_by,
            component_versions=sample_components,
        )

        assert snapshot.snapshot_id == snapshot_id
        assert snapshot.captured_by == captured_by
        assert len(snapshot.component_versions) == 4

    def test_version_accessors(self, sample_components):
        """Should provide version accessors for each component type."""
        snapshot = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        assert snapshot.coa_version == 3
        assert snapshot.dimension_schema_version == 2
        assert snapshot.fx_rates_version == 5
        assert snapshot.rounding_policy_version == 1
        assert snapshot.currency_registry_version == 5  # Alias for fx_rates

    def test_missing_component_defaults_to_1(self):
        """Should return version 1 for missing components."""
        snapshot = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=(),  # No components
        )

        assert snapshot.coa_version == 1
        assert snapshot.dimension_schema_version == 1
        assert snapshot.tax_rules_version == 1

    def test_version_dict(self, sample_components):
        """Should produce version dict for R21 compliance."""
        snapshot = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        version_dict = snapshot.version_dict

        assert version_dict["coa_version"] == 3
        assert version_dict["dimension_schema_version"] == 2
        assert version_dict["rounding_policy_version"] == 1
        assert version_dict["currency_registry_version"] == 5

    def test_get_component(self, sample_components):
        """Should retrieve specific component version."""
        snapshot = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        coa = snapshot.get_component(SnapshotComponentType.COA)
        assert coa is not None
        assert coa.version == 3
        assert coa.content_hash == "coa_hash_123"

        missing = snapshot.get_component(SnapshotComponentType.TAX_RULES)
        assert missing is None

    def test_is_compatible_with_same_versions(self, sample_components):
        """Should be compatible when versions match."""
        snapshot1 = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        snapshot2 = ReferenceSnapshot(
            snapshot_id=uuid4(),  # Different ID
            captured_at=datetime.now() + timedelta(hours=1),  # Different time
            captured_by=uuid4(),  # Different actor
            component_versions=sample_components,  # Same versions
        )

        assert snapshot1.is_compatible_with(snapshot2)

    def test_is_not_compatible_with_different_versions(self, sample_components):
        """Should not be compatible when versions differ."""
        now = datetime.now()
        different_components = (
            ComponentVersion(
                component_type=SnapshotComponentType.COA,
                version=4,  # Different version
                content_hash="different_hash",
                effective_from=now,
            ),
        )

        snapshot1 = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=now,
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        snapshot2 = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=now,
            captured_by=uuid4(),
            component_versions=different_components,
        )

        assert not snapshot1.is_compatible_with(snapshot2)

    def test_snapshot_immutable(self, sample_components):
        """Should be immutable."""
        snapshot = ReferenceSnapshot(
            snapshot_id=uuid4(),
            captured_at=datetime.now(),
            captured_by=uuid4(),
            component_versions=sample_components,
        )

        with pytest.raises(AttributeError):
            snapshot.captured_at = datetime.now()


class TestSnapshotRequest:
    """Tests for SnapshotRequest."""

    def test_all_components_request(self):
        """Should request all component types."""
        actor_id = uuid4()
        request = SnapshotRequest.all_components(actor_id)

        assert request.requested_by == actor_id
        assert len(request.include_components) == len(SnapshotComponentType)
        assert request.as_of is None

    def test_minimal_request(self):
        """Should request minimal components."""
        actor_id = uuid4()
        request = SnapshotRequest.minimal(actor_id)

        assert request.requested_by == actor_id
        assert SnapshotComponentType.COA in request.include_components
        assert SnapshotComponentType.DIMENSION_SCHEMA in request.include_components
        assert SnapshotComponentType.ROUNDING_POLICY in request.include_components
        assert SnapshotComponentType.FX_RATES not in request.include_components

    def test_custom_request(self):
        """Should allow custom component selection."""
        actor_id = uuid4()
        as_of = datetime.now()

        request = SnapshotRequest(
            requested_by=actor_id,
            include_components=frozenset({
                SnapshotComponentType.COA,
                SnapshotComponentType.FX_RATES,
            }),
            as_of=as_of,
        )

        assert len(request.include_components) == 2
        assert request.as_of == as_of


class TestSnapshotValidationResult:
    """Tests for SnapshotValidationResult."""

    def test_valid_result(self):
        """Should create valid result."""
        snapshot_id = uuid4()
        result = SnapshotValidationResult.valid(snapshot_id)

        assert result.is_valid
        assert result.snapshot_id == snapshot_id
        assert len(result.errors) == 0

    def test_invalid_result(self):
        """Should create invalid result with errors."""
        snapshot_id = uuid4()
        error = SnapshotIntegrityError(
            snapshot_id=snapshot_id,
            component_type=SnapshotComponentType.COA,
            expected_hash="abc123",
            actual_hash="def456",
            message="COA has changed",
        )

        result = SnapshotValidationResult.invalid(snapshot_id, (error,))

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].component_type == SnapshotComponentType.COA


class TestSnapshotComponentTypes:
    """Tests for SnapshotComponentType enum."""

    def test_all_component_types_defined(self):
        """Should have all expected component types."""
        expected = {
            "coa",
            "dimension_schema",
            "fx_rates",
            "tax_rules",
            "policy_registry",
            "rounding_policy",
            "account_roles",
            "configuration_set",
        }

        actual = {ct.value for ct in SnapshotComponentType}
        assert actual == expected

    def test_component_types_unique(self):
        """Should have unique values."""
        values = [ct.value for ct in SnapshotComponentType]
        assert len(values) == len(set(values))

"""
Dimension integrity tests.

Tests for dimension referential integrity and validation:
- DimensionValue must reference a valid Dimension (FK enforced)
- Dimension codes are unique
- DimensionValue name and code are immutable
- Inactive dimensions cannot be used for posting
"""

import pytest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from finance_kernel.models.dimensions import Dimension, DimensionValue
from finance_kernel.exceptions import (
    ImmutabilityViolationError,
    InactiveDimensionError,
    InactiveDimensionValueError,
    InvalidDimensionValueError,
    DimensionNotFoundError,
)


class TestDimensionValueForeignKey:
    """Tests for DimensionValue FK constraint to Dimension."""

    def test_dimension_value_with_nonexistent_dimension_code_fails(
        self,
        session,
        test_actor_id,
    ):
        """
        Attempt to insert a DimensionValue with a dimension_code that does not
        exist in the Dimension table.

        The FK constraint should prevent this.
        """
        # Create only the "project" dimension
        project_dimension = Dimension(
            code="project",
            name="Project",
            description="Project dimension for cost allocation",
            is_required=False,
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(project_dimension)
        session.flush()

        # Verify "project" exists but "department" does NOT
        existing_dimensions = session.execute(
            select(Dimension.code)
        ).scalars().all()
        assert "project" in existing_dimensions
        assert "department" not in existing_dimensions

        # Attempt to create a DimensionValue for non-existent "department"
        orphan_value = DimensionValue(
            dimension_code="department",  # This dimension does NOT exist!
            code="DEPT001",
            name="Engineering Department",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(orphan_value)

        # FK constraint should prevent this
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()

        # Verify it's a FK violation
        assert "fk_dimension_value_dimension" in str(exc_info.value).lower() or \
               "foreign key" in str(exc_info.value).lower()

        session.rollback()

    def test_dimension_value_with_valid_dimension_code_succeeds(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that DimensionValue with a valid dimension_code succeeds.
        """
        # Create the dimension first
        dimension = Dimension(
            code="org_unit",
            name="Organization Unit",
            is_required=True,
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        # Create a value for the valid dimension
        value = DimensionValue(
            dimension_code="org_unit",  # Valid - dimension exists
            code="OU001",
            name="Corporate Headquarters",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Verify it was created successfully
        saved = session.execute(
            select(DimensionValue).where(DimensionValue.code == "OU001")
        ).scalar_one()

        assert saved.dimension_code == "org_unit"
        assert saved.name == "Corporate Headquarters"

    def test_cannot_delete_dimension_with_values(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that deleting a Dimension with values is prevented by FK constraint.
        """
        # Create dimension and value
        dimension = Dimension(
            code="contract",
            name="Contract",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        value = DimensionValue(
            dimension_code="contract",
            code="CONTRACT001",
            name="Government Contract A",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Attempt to delete dimension (should fail due to FK RESTRICT)
        session.delete(dimension)

        with pytest.raises(IntegrityError):
            session.flush()

        session.rollback()


class TestDimensionCodeUniqueness:
    """Tests for Dimension.code uniqueness constraint."""

    def test_cannot_create_duplicate_dimension_code(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that creating two Dimensions with the same code fails.
        """
        # Create first dimension
        dim1 = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dim1)
        session.flush()

        # Attempt to create second dimension with same code
        dim2 = Dimension(
            code="project",  # Same code!
            name="Different Name",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dim2)

        with pytest.raises(IntegrityError) as exc_info:
            session.flush()

        assert "uq_dimension_code" in str(exc_info.value).lower() or \
               "unique" in str(exc_info.value).lower()

        session.rollback()


class TestDimensionValueImmutability:
    """Tests for DimensionValue immutability."""

    def test_cannot_change_dimension_value_name(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that DimensionValue.name is immutable after creation.
        """
        # Create dimension and value
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Original Project Name",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Attempt to change the name
        value.name = "Changed Project Name"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "DimensionValue" in str(exc_info.value)
        assert "name" in str(exc_info.value)

        session.rollback()

    def test_cannot_change_dimension_value_code(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that DimensionValue.code is immutable after creation.
        """
        # Create dimension and value
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Project One",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Attempt to change the code
        value.code = "PROJ999"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "DimensionValue" in str(exc_info.value)
        assert "code" in str(exc_info.value)

        session.rollback()

    def test_can_change_dimension_value_is_active(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that DimensionValue.is_active CAN be changed (not immutable).
        """
        # Create dimension and value
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Project One",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Change is_active (should be allowed)
        value.is_active = False
        session.flush()

        # Verify the change
        refreshed = session.get(DimensionValue, value.id)
        assert refreshed.is_active is False


class TestDimensionCodeImmutability:
    """Tests for Dimension.code immutability when values exist."""

    def test_cannot_change_dimension_code_with_values(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that Dimension.code cannot be changed when values exist.
        """
        # Create dimension
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        # Add a value
        value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Project One",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value)
        session.flush()

        # Attempt to change dimension code
        dimension.code = "project_renamed"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "Dimension" in str(exc_info.value)
        assert "code" in str(exc_info.value).lower()

        session.rollback()

    def test_can_change_dimension_code_without_values(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that Dimension.code CAN be changed when no values exist.
        """
        # Create dimension without values
        dimension = Dimension(
            code="old_code",
            name="Dimension",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        # Change dimension code (should be allowed - no values)
        dimension.code = "new_code"
        session.flush()

        # Verify the change
        refreshed = session.get(Dimension, dimension.id)
        assert refreshed.code == "new_code"


class TestInactiveDimensionValidation:
    """Tests for inactive dimension validation during posting."""

    def test_inactive_dimension_error_attributes(self):
        """Verify InactiveDimensionError has correct attributes."""
        error = InactiveDimensionError("project")

        assert error.code == "INACTIVE_DIMENSION"
        assert error.dimension_code == "project"
        assert "project" in str(error)
        assert "inactive" in str(error).lower()

    def test_inactive_dimension_value_error_attributes(self):
        """Verify InactiveDimensionValueError has correct attributes."""
        error = InactiveDimensionValueError("project", "PROJ001")

        assert error.code == "INACTIVE_DIMENSION_VALUE"
        assert error.dimension_code == "project"
        assert error.value == "PROJ001"
        assert "project" in str(error)
        assert "PROJ001" in str(error)
        assert "inactive" in str(error).lower()

    def test_dimension_not_found_error_attributes(self):
        """Verify DimensionNotFoundError has correct attributes."""
        error = DimensionNotFoundError("nonexistent")

        assert error.code == "DIMENSION_NOT_FOUND"
        assert error.dimension_code == "nonexistent"
        assert "nonexistent" in str(error)

    def test_reference_data_validates_inactive_dimension(
        self,
        session,
        test_actor_id,
        reference_data_loader,
    ):
        """
        Verify that ReferenceData correctly identifies inactive dimensions.
        """
        # Create active and inactive dimensions
        active_dim = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        inactive_dim = Dimension(
            code="department",
            name="Department",
            is_active=False,  # Inactive!
            created_by_id=test_actor_id,
        )
        session.add_all([active_dim, inactive_dim])
        session.flush()

        # Load reference data
        ref_data = reference_data_loader.load()

        # Verify active dimension is recognized
        assert ref_data.is_dimension_active("project") is True

        # Verify inactive dimension is NOT in active set
        assert ref_data.is_dimension_active("department") is False

    def test_reference_data_validates_inactive_dimension_value(
        self,
        session,
        test_actor_id,
        reference_data_loader,
    ):
        """
        Verify that ReferenceData correctly identifies inactive dimension values.
        """
        # Create dimension
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        # Create active and inactive values
        active_value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Active Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        inactive_value = DimensionValue(
            dimension_code="project",
            code="PROJ002",
            name="Inactive Project",
            is_active=False,  # Inactive!
            created_by_id=test_actor_id,
        )
        session.add_all([active_value, inactive_value])
        session.flush()

        # Load reference data
        ref_data = reference_data_loader.load()

        # Verify active value is recognized
        assert ref_data.is_dimension_value_active("project", "PROJ001") is True

        # Verify inactive value is NOT active
        assert ref_data.is_dimension_value_active("project", "PROJ002") is False

    def test_reference_data_validate_dimensions_method(
        self,
        session,
        test_actor_id,
        reference_data_loader,
    ):
        """
        Verify the validate_dimensions method on ReferenceData.
        """
        # Setup dimensions and values
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        inactive_dim = Dimension(
            code="cost_center",
            name="Cost Center",
            is_active=False,
            created_by_id=test_actor_id,
        )
        session.add_all([dimension, inactive_dim])
        session.flush()

        active_value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Project One",
            is_active=True,
            created_by_id=test_actor_id,
        )
        inactive_value = DimensionValue(
            dimension_code="project",
            code="PROJ002",
            name="Project Two",
            is_active=False,
            created_by_id=test_actor_id,
        )
        session.add_all([active_value, inactive_value])
        session.flush()

        ref_data = reference_data_loader.load()

        # Valid dimensions should pass
        errors = ref_data.validate_dimensions({"project": "PROJ001"})
        assert errors == []

        # Inactive dimension should fail
        errors = ref_data.validate_dimensions({"cost_center": "CC001"})
        assert len(errors) == 1
        assert "cost_center" in errors[0]
        assert "inactive" in errors[0].lower()

        # Inactive value should fail
        errors = ref_data.validate_dimensions({"project": "PROJ002"})
        assert len(errors) == 1
        assert "PROJ002" in errors[0]

        # None dimensions should pass
        errors = ref_data.validate_dimensions(None)
        assert errors == []


class TestDimensionValueCodeUniqueness:
    """Tests for DimensionValue code uniqueness within a dimension."""

    def test_dimension_value_code_unique_within_dimension(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that (dimension_code, code) must be unique.
        Cannot have two values with same code in same dimension.
        """
        # Create dimension
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        # Create first value
        value1 = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Project Alpha",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value1)
        session.flush()

        # Attempt to create duplicate code in same dimension
        value2 = DimensionValue(
            dimension_code="project",
            code="PROJ001",  # Same code!
            name="Project Beta",  # Different name doesn't matter
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(value2)

        with pytest.raises(IntegrityError) as exc_info:
            session.flush()

        # Should fail on unique constraint
        assert "uq_dimension_value" in str(exc_info.value).lower() or \
               "unique" in str(exc_info.value).lower()

        session.rollback()

    def test_same_code_allowed_in_different_dimensions(
        self,
        session,
        test_actor_id,
    ):
        """
        Verify that the same code can exist in different dimensions.
        Code uniqueness is per-dimension, not global.
        """
        # Create two dimensions
        dim1 = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        dim2 = Dimension(
            code="cost_center",
            name="Cost Center",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add_all([dim1, dim2])
        session.flush()

        # Create values with same code in different dimensions
        val1 = DimensionValue(
            dimension_code="project",
            code="001",  # Same code
            name="Project 001",
            is_active=True,
            created_by_id=test_actor_id,
        )
        val2 = DimensionValue(
            dimension_code="cost_center",
            code="001",  # Same code, different dimension - should be allowed
            name="Cost Center 001",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add_all([val1, val2])
        session.flush()  # Should succeed

        # Verify both exist
        all_001 = session.execute(
            select(DimensionValue).where(DimensionValue.code == "001")
        ).scalars().all()

        assert len(all_001) == 2
        dimension_codes = {v.dimension_code for v in all_001}
        assert dimension_codes == {"project", "cost_center"}


class TestInvalidDimensionValueException:
    """Tests for InvalidDimensionValueError exception."""

    def test_invalid_dimension_value_error_attributes(self):
        """
        Verify InvalidDimensionValueError has correct attributes.
        """
        error = InvalidDimensionValueError(
            dimension_code="project",
            value="INVALID_PROJECT_123",
        )

        assert error.code == "INVALID_DIMENSION_VALUE"
        assert error.dimension_code == "project"
        assert error.value == "INVALID_PROJECT_123"
        assert "project" in str(error)
        assert "INVALID_PROJECT_123" in str(error)

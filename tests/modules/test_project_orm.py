"""ORM round-trip tests for the Project Accounting module.

Verifies that all three Project ORM models can be persisted and queried,
that parent-child relationships load correctly, that FK constraints are
enforced, and that unique constraints reject duplicates.

Models under test:
    - ProjectModel
    - ProjectPhaseModel  (WBS element)
    - ProjectCostEntryModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.project.orm import (
    ProjectCostEntryModel,
    ProjectModel,
    ProjectPhaseModel,
)


# ---------------------------------------------------------------------------
# Helper: create a valid ProjectModel for FK-parent needs
# ---------------------------------------------------------------------------


def _make_project(session, test_actor_id, *, name="Test Project", **overrides):
    """Create and flush a ProjectModel with sensible defaults."""
    fields = dict(
        name=name,
        project_type="fixed_price",
        status="active",
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        total_budget=Decimal("500000.00"),
        currency="USD",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    proj = ProjectModel(**fields)
    session.add(proj)
    session.flush()
    return proj


# ---------------------------------------------------------------------------
# ProjectModel
# ---------------------------------------------------------------------------


class TestProjectModelORM:
    """Round-trip persistence tests for ProjectModel."""

    def test_create_and_query(self, session, test_actor_id):
        """Insert a project and read it back -- all fields must match."""
        proj = ProjectModel(
            name="Alpha Satellite",
            project_type="cost_plus",
            status="active",
            start_date=date(2024, 1, 1),
            end_date=date(2025, 6, 30),
            total_budget=Decimal("1200000.00"),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(proj)
        session.flush()

        queried = session.get(ProjectModel, proj.id)
        assert queried is not None
        assert queried.name == "Alpha Satellite"
        assert queried.project_type == "cost_plus"
        assert queried.status == "active"
        assert queried.start_date == date(2024, 1, 1)
        assert queried.end_date == date(2025, 6, 30)
        assert queried.total_budget == Decimal("1200000.00")
        assert queried.currency == "USD"

    def test_unique_name(self, session, test_actor_id):
        """Duplicate project name must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            proj = ProjectModel(
                name="Unique Name Violation",
                project_type="time_and_materials",
                status="active",
                total_budget=Decimal("0"),
                currency="USD",
                created_by_id=test_actor_id,
            )
            session.add(proj)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_nullable_dates(self, session, test_actor_id):
        """Project with no start/end dates persists correctly."""
        proj = ProjectModel(
            name="Dateless Project",
            project_type="fixed_price",
            status="active",
            start_date=None,
            end_date=None,
            total_budget=Decimal("10000.00"),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(proj)
        session.flush()

        queried = session.get(ProjectModel, proj.id)
        assert queried.start_date is None
        assert queried.end_date is None

    def test_relationship_phases(self, session, test_actor_id):
        """Project loads its phases through the relationship."""
        proj = _make_project(session, test_actor_id, name="Phase Rel Project")

        phase = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-1",
            name="Design Phase",
            budget_amount=Decimal("100000.00"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        session.flush()

        session.expire_all()
        refreshed = session.get(ProjectModel, proj.id)
        assert len(refreshed.phases) == 1
        assert refreshed.phases[0].code == "WBS-1"

    def test_relationship_cost_entries(self, session, test_actor_id):
        """Project loads its cost entries through the relationship."""
        proj = _make_project(session, test_actor_id, name="Cost Rel Project")

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            cost_type="labor",
            description="Engineering hours",
            amount=Decimal("5000.00"),
            currency="USD",
            period="2024-01",
            entry_date=date(2024, 1, 31),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        session.expire_all()
        refreshed = session.get(ProjectModel, proj.id)
        assert len(refreshed.cost_entries) == 1
        assert refreshed.cost_entries[0].cost_type == "labor"


# ---------------------------------------------------------------------------
# ProjectPhaseModel (WBS Element)
# ---------------------------------------------------------------------------


class TestProjectPhaseModelORM:
    """Round-trip persistence tests for ProjectPhaseModel."""

    def test_create_and_query(self, session, test_actor_id):
        """Insert a phase and read it back -- all fields must match."""
        proj = _make_project(session, test_actor_id, name="Phase Query Project")

        phase = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-2.1",
            name="Implementation",
            parent_id=None,
            budget_amount=Decimal("200000.00"),
            actual_cost=Decimal("15000.00"),
            level=2,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        session.flush()

        queried = session.get(ProjectPhaseModel, phase.id)
        assert queried is not None
        assert queried.project_id == proj.id
        assert queried.code == "WBS-2.1"
        assert queried.name == "Implementation"
        assert queried.parent_id is None
        assert queried.budget_amount == Decimal("200000.00")
        assert queried.actual_cost == Decimal("15000.00")
        assert queried.level == 2

    def test_unique_project_code(self, session, test_actor_id):
        """Duplicate (project_id, code) must raise IntegrityError."""
        proj = _make_project(session, test_actor_id, name="Phase Dup Project")

        for tag in ("first", "duplicate"):
            phase = ProjectPhaseModel(
                project_id=proj.id,
                code="WBS-DUP",
                name=f"Phase {tag}",
                budget_amount=Decimal("0"),
                actual_cost=Decimal("0"),
                level=1,
                created_by_id=test_actor_id,
            )
            session.add(phase)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_project_id_constraint(self, session, test_actor_id):
        """Nonexistent project_id must raise IntegrityError."""
        phase = ProjectPhaseModel(
            project_id=uuid4(),
            code="WBS-ORPHAN",
            name="Orphan Phase",
            budget_amount=Decimal("0"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_self_referential_parent(self, session, test_actor_id):
        """Phase can reference another phase as parent (hierarchy)."""
        proj = _make_project(session, test_actor_id, name="Hierarchy Project")

        parent = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-ROOT",
            name="Root Phase",
            budget_amount=Decimal("500000.00"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(parent)
        session.flush()

        child = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-ROOT.1",
            name="Child Phase",
            parent_id=parent.id,
            budget_amount=Decimal("100000.00"),
            actual_cost=Decimal("0"),
            level=2,
            created_by_id=test_actor_id,
        )
        session.add(child)
        session.flush()

        session.expire_all()
        refreshed_child = session.get(ProjectPhaseModel, child.id)
        assert refreshed_child.parent_id == parent.id
        assert refreshed_child.parent is not None
        assert refreshed_child.parent.code == "WBS-ROOT"

    def test_parent_relationship_loads_project(self, session, test_actor_id):
        """Phase's back_populates 'project' relationship loads the parent project."""
        proj = _make_project(session, test_actor_id, name="Phase Parent Rel")

        phase = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-REL",
            name="Relationship Phase",
            budget_amount=Decimal("0"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        session.flush()

        session.expire_all()
        refreshed = session.get(ProjectPhaseModel, phase.id)
        assert refreshed.project is not None
        assert refreshed.project.name == "Phase Parent Rel"


# ---------------------------------------------------------------------------
# ProjectCostEntryModel
# ---------------------------------------------------------------------------


class TestProjectCostEntryModelORM:
    """Round-trip persistence tests for ProjectCostEntryModel."""

    def test_create_and_query(self, session, test_actor_id):
        """Insert a cost entry and read it back -- all fields must match."""
        proj = _make_project(session, test_actor_id, name="Cost Entry Project")

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            phase_id=None,
            cost_type="material",
            description="Raw materials procurement",
            amount=Decimal("12500.75"),
            currency="USD",
            period="2024-03",
            entry_date=date(2024, 3, 15),
            source_event_id=None,
            gl_account_code="5000-000",
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        queried = session.get(ProjectCostEntryModel, entry.id)
        assert queried is not None
        assert queried.project_id == proj.id
        assert queried.phase_id is None
        assert queried.cost_type == "material"
        assert queried.description == "Raw materials procurement"
        assert queried.amount == Decimal("12500.75")
        assert queried.currency == "USD"
        assert queried.period == "2024-03"
        assert queried.entry_date == date(2024, 3, 15)
        assert queried.source_event_id is None
        assert queried.gl_account_code == "5000-000"

    def test_with_phase_id(self, session, test_actor_id):
        """Cost entry with a valid phase FK persists correctly."""
        proj = _make_project(session, test_actor_id, name="Cost Phase Project")

        phase = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-COST",
            name="Cost Phase",
            budget_amount=Decimal("50000.00"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        session.flush()

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            phase_id=phase.id,
            cost_type="labor",
            amount=Decimal("3000.00"),
            currency="USD",
            period="2024-02",
            entry_date=date(2024, 2, 28),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        queried = session.get(ProjectCostEntryModel, entry.id)
        assert queried.phase_id == phase.id

    def test_fk_project_id_constraint(self, session, test_actor_id):
        """Nonexistent project_id must raise IntegrityError."""
        entry = ProjectCostEntryModel(
            project_id=uuid4(),
            cost_type="labor",
            amount=Decimal("100.00"),
            currency="USD",
            period="2024-01",
            entry_date=date(2024, 1, 15),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_phase_id_constraint(self, session, test_actor_id):
        """Nonexistent phase_id must raise IntegrityError."""
        proj = _make_project(session, test_actor_id, name="Bad Phase FK Project")

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            phase_id=uuid4(),
            cost_type="travel",
            amount=Decimal("500.00"),
            currency="USD",
            period="2024-04",
            entry_date=date(2024, 4, 10),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_project(self, session, test_actor_id):
        """Cost entry's 'project' relationship loads the parent project."""
        proj = _make_project(session, test_actor_id, name="Cost Rel Load Project")

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            cost_type="subcontract",
            amount=Decimal("25000.00"),
            currency="USD",
            period="2024-05",
            entry_date=date(2024, 5, 31),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        session.expire_all()
        refreshed = session.get(ProjectCostEntryModel, entry.id)
        assert refreshed.project is not None
        assert refreshed.project.name == "Cost Rel Load Project"

    def test_relationship_phase(self, session, test_actor_id):
        """Cost entry's 'phase' relationship loads the associated phase."""
        proj = _make_project(session, test_actor_id, name="Cost Phase Rel Project")

        phase = ProjectPhaseModel(
            project_id=proj.id,
            code="WBS-CREL",
            name="Phase for Cost Rel",
            budget_amount=Decimal("0"),
            actual_cost=Decimal("0"),
            level=1,
            created_by_id=test_actor_id,
        )
        session.add(phase)
        session.flush()

        entry = ProjectCostEntryModel(
            project_id=proj.id,
            phase_id=phase.id,
            cost_type="odc",
            amount=Decimal("750.00"),
            currency="USD",
            period="2024-06",
            entry_date=date(2024, 6, 15),
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        session.expire_all()
        refreshed = session.get(ProjectCostEntryModel, entry.id)
        assert refreshed.phase is not None
        assert refreshed.phase.code == "WBS-CREL"

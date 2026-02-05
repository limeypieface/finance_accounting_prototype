"""End-to-end tests for QuickBooks Online import mappings and test CSVs."""

from pathlib import Path

import pytest

from finance_config import get_active_config
from finance_ingestion.promoters import default_promoter_registry
from finance_ingestion.services import (
    ImportService,
    PromotionService,
    build_mapping_registry_from_defs,
)
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.services.auditor_service import AuditorService

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "fixtures"


@pytest.fixture
def qbo_config():
    """Load ENTERPRISE config with QBO import mappings."""
    from datetime import date

    return get_active_config("ENTERPRISE", date(2026, 1, 1))


@pytest.fixture
def qbo_mapping_registry(qbo_config):
    return build_mapping_registry_from_defs(qbo_config.import_mappings)


def test_qbo_mappings_present(qbo_config):
    """QuickBooks Online mappings are loaded from ENTERPRISE config."""
    names = sorted(m.name for m in qbo_config.import_mappings)
    assert "qbo_chart_of_accounts" in names
    assert "qbo_vendors" in names
    assert "qbo_customers" in names


def test_qbo_chart_of_accounts_e2e(session, qbo_mapping_registry):
    """Load, validate, and promote QBO chart of accounts test CSV."""
    from datetime import datetime, timezone
    from uuid import uuid4

    csv_path = FIXTURES_DIR / "qbo_chart_of_accounts_test.csv"
    if not csv_path.exists():
        pytest.skip("scripts/fixtures/qbo_chart_of_accounts_test.csv not found")

    mapping = qbo_mapping_registry.get("qbo_chart_of_accounts")
    assert mapping is not None

    clock = DeterministicClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc))
    auditor = AuditorService(session, clock)
    import_svc = ImportService(session, clock=clock, mapping_registry=qbo_mapping_registry)
    promotion_svc = PromotionService(
        session,
        promoters=default_promoter_registry(),
        clock=clock,
        auditor_service=auditor,
    )
    actor_id = uuid4()

    batch = import_svc.load_batch(csv_path, mapping, actor_id)
    session.commit()
    assert batch.total_records == 8

    validated = import_svc.validate_batch(batch.batch_id)
    session.commit()
    assert validated.valid_records == 8
    assert validated.invalid_records == 0

    result = promotion_svc.promote_batch(batch.batch_id, actor_id)
    session.commit()
    assert result.failed == 0
    assert result.promoted + result.skipped == result.total_attempted


def test_qbo_vendors_e2e(session, qbo_mapping_registry):
    """Load, validate, and promote QBO vendors test CSV."""
    from datetime import datetime, timezone
    from uuid import uuid4

    csv_path = FIXTURES_DIR / "qbo_vendors_test.csv"
    if not csv_path.exists():
        pytest.skip("scripts/fixtures/qbo_vendors_test.csv not found")

    mapping = qbo_mapping_registry.get("qbo_vendors")
    assert mapping is not None

    clock = DeterministicClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc))
    auditor = AuditorService(session, clock)
    import_svc = ImportService(session, clock=clock, mapping_registry=qbo_mapping_registry)
    promotion_svc = PromotionService(
        session,
        promoters=default_promoter_registry(),
        clock=clock,
        auditor_service=auditor,
    )
    actor_id = uuid4()

    batch = import_svc.load_batch(csv_path, mapping, actor_id)
    session.commit()
    assert batch.total_records == 3

    validated = import_svc.validate_batch(batch.batch_id)
    session.commit()
    assert validated.valid_records == 3
    assert validated.invalid_records == 0

    result = promotion_svc.promote_batch(batch.batch_id, actor_id)
    session.commit()
    assert result.failed == 0
    assert result.promoted + result.skipped == result.total_attempted


def test_qbo_customers_e2e(session, qbo_mapping_registry):
    """Load, validate, and promote QBO customers test CSV."""
    from datetime import datetime, timezone
    from uuid import uuid4

    csv_path = FIXTURES_DIR / "qbo_customers_test.csv"
    if not csv_path.exists():
        pytest.skip("scripts/fixtures/qbo_customers_test.csv not found")

    mapping = qbo_mapping_registry.get("qbo_customers")
    assert mapping is not None

    clock = DeterministicClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc))
    auditor = AuditorService(session, clock)
    import_svc = ImportService(session, clock=clock, mapping_registry=qbo_mapping_registry)
    promotion_svc = PromotionService(
        session,
        promoters=default_promoter_registry(),
        clock=clock,
        auditor_service=auditor,
    )
    actor_id = uuid4()

    batch = import_svc.load_batch(csv_path, mapping, actor_id)
    session.commit()
    assert batch.total_records == 3

    validated = import_svc.validate_batch(batch.batch_id)
    session.commit()
    assert validated.valid_records == 3
    assert validated.invalid_records == 0

    result = promotion_svc.promote_batch(batch.batch_id, actor_id)
    session.commit()
    assert result.failed == 0
    assert result.promoted + result.skipped == result.total_attempted

"""Per-entity-type promotion: Party, Account, Vendor, Customer, stubs (Phase 9)."""

from uuid import uuid4

import pytest

from finance_kernel.models.account import Account
from finance_kernel.models.party import Party

from finance_ingestion.promoters.account import AccountPromoter
from finance_ingestion.promoters.ar import CustomerPromoter
from finance_ingestion.promoters.ap import VendorPromoter
from finance_ingestion.promoters.base import PromoteResult
from finance_ingestion.promoters.inventory import ItemPromoter, LocationPromoter
from finance_ingestion.promoters.journal import OpeningBalancePromoter
from finance_ingestion.promoters.party import PartyPromoter

from finance_modules.ap.orm import VendorProfileModel
from finance_modules.ar.orm import CustomerProfileModel


# ---------------------------------------------------------------------------
# PartyPromoter
# ---------------------------------------------------------------------------


class TestPartyPromoter:
    def test_promote_creates_party_row(self, session, test_actor_id, deterministic_clock):
        promoter = PartyPromoter()
        mapped = {"code": "P001", "name": "Acme Corp"}
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert isinstance(result, PromoteResult)
        assert result.success is True
        assert result.entity_id is not None
        assert result.error is None
        session.flush()
        party = session.get(Party, result.entity_id)
        assert party is not None
        assert party.party_code == "P001"
        assert party.name == "Acme Corp"

    def test_party_accepts_party_code_alias(self, session, test_actor_id, deterministic_clock):
        promoter = PartyPromoter()
        mapped = {"party_code": "P002", "name": "Beta"}
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert result.success is True
        session.flush()
        party = session.get(Party, result.entity_id)
        assert party.party_code == "P002"

    def test_party_check_duplicate_by_code(self, session, test_actor_id, deterministic_clock):
        promoter = PartyPromoter()
        mapped = {"code": "P003", "name": "Gamma"}
        promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        session.flush()
        assert promoter.check_duplicate(mapped, session) is True
        assert promoter.check_duplicate({"code": "P999"}, session) is False

    def test_party_missing_code_fails(self, session, test_actor_id, deterministic_clock):
        promoter = PartyPromoter()
        result = promoter.promote({"name": "No Code"}, session, test_actor_id, deterministic_clock)
        assert result.success is False
        assert "Missing" in (result.error or "")


# ---------------------------------------------------------------------------
# AccountPromoter
# ---------------------------------------------------------------------------


class TestAccountPromoter:
    def test_promote_creates_account_row(self, session, test_actor_id, deterministic_clock):
        promoter = AccountPromoter()
        code = f"ACC-{uuid4().hex[:8]}"
        mapped = {"code": code, "name": "Cash", "account_type": "asset", "normal_balance": "debit"}
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert result.success is True
        assert result.entity_id is not None
        session.flush()
        account = session.get(Account, result.entity_id)
        assert account is not None
        assert account.code == code
        assert account.name == "Cash"

    def test_account_optional_fields_tags_and_currency(self, session, test_actor_id, deterministic_clock):
        promoter = AccountPromoter()
        code = f"ACC-{uuid4().hex[:8]}"
        mapped = {
            "code": code,
            "name": "Petty Cash",
            "account_type": "asset",
            "normal_balance": "debit",
            "tags": ["direct", "rounding"],
            "currency": "USD",
        }
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert result.success is True
        session.flush()
        account = session.get(Account, result.entity_id)
        assert account is not None
        assert account.tags == ["direct", "rounding"]
        assert account.currency == "USD"

    def test_account_check_duplicate_by_code(self, session, test_actor_id, deterministic_clock):
        promoter = AccountPromoter()
        code = f"ACC-{uuid4().hex[:8]}"
        mapped = {"code": code, "name": "AR", "account_type": "asset", "normal_balance": "debit"}
        promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        session.flush()
        assert promoter.check_duplicate(mapped, session) is True
        assert promoter.check_duplicate({"code": f"OTHER-{uuid4().hex[:8]}"}, session) is False

    def test_account_missing_code_fails(self, session, test_actor_id, deterministic_clock):
        promoter = AccountPromoter()
        result = promoter.promote({"name": "No Code"}, session, test_actor_id, deterministic_clock)
        assert result.success is False


# ---------------------------------------------------------------------------
# VendorPromoter
# ---------------------------------------------------------------------------


class TestVendorPromoter:
    def test_promote_creates_party_and_vendor_profile(self, session, test_actor_id, deterministic_clock):
        promoter = VendorPromoter()
        mapped = {"code": "V001", "name": "Vendor One", "payment_terms_days": 30}
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert result.success is True
        assert result.entity_id is not None
        session.flush()
        profile = session.get(VendorProfileModel, result.entity_id)
        assert profile is not None
        assert profile.code == "V001"
        assert profile.name == "Vendor One"
        party = session.get(Party, profile.vendor_id)
        assert party is not None
        assert party.party_code == "V001"

    def test_vendor_check_duplicate_by_code(self, session, test_actor_id, deterministic_clock):
        promoter = VendorPromoter()
        mapped = {"code": "V002", "name": "Vendor Two"}
        promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        session.flush()
        assert promoter.check_duplicate(mapped, session) is True
        assert promoter.check_duplicate({"code": "V999"}, session) is False

    def test_vendor_reuses_existing_party_same_code(self, session, test_actor_id, deterministic_clock):
        promoter = VendorPromoter()
        mapped = {"code": "V003", "name": "Vendor Three"}
        r1 = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        session.flush()
        # Second vendor with same code is duplicate (profile code)
        assert promoter.check_duplicate(mapped, session) is True


# ---------------------------------------------------------------------------
# CustomerPromoter
# ---------------------------------------------------------------------------


class TestCustomerPromoter:
    def test_promote_creates_party_and_customer_profile(self, session, test_actor_id, deterministic_clock):
        promoter = CustomerPromoter()
        mapped = {"code": "C001", "name": "Customer One", "payment_terms_days": 30}
        result = promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        assert result.success is True
        assert result.entity_id is not None
        session.flush()
        profile = session.get(CustomerProfileModel, result.entity_id)
        assert profile is not None
        assert profile.code == "C001"
        party = session.get(Party, profile.customer_id)
        assert party is not None
        assert party.party_code == "C001"

    def test_customer_check_duplicate_by_code(self, session, test_actor_id, deterministic_clock):
        promoter = CustomerPromoter()
        mapped = {"code": "C002", "name": "Customer Two"}
        promoter.promote(mapped, session, test_actor_id, deterministic_clock)
        session.flush()
        assert promoter.check_duplicate(mapped, session) is True


# ---------------------------------------------------------------------------
# Stub promoters (item, location, opening_balance)
# ---------------------------------------------------------------------------


class TestItemPromoterStub:
    def test_promote_returns_not_implemented(self, session, test_actor_id, deterministic_clock):
        promoter = ItemPromoter()
        result = promoter.promote({"code": "ITEM1"}, session, test_actor_id, deterministic_clock)
        assert result.success is False
        assert "Not implemented" in (result.error or "")
        assert "InventoryItemModel" in (result.error or "") or "item" in (result.error or "").lower()

    def test_check_duplicate_returns_false(self, session):
        assert ItemPromoter().check_duplicate({"code": "X"}, session) is False


class TestLocationPromoterStub:
    def test_promote_returns_not_implemented(self, session, test_actor_id, deterministic_clock):
        promoter = LocationPromoter()
        result = promoter.promote({"code": "WH1"}, session, test_actor_id, deterministic_clock)
        assert result.success is False
        assert "Not implemented" in (result.error or "")


class TestOpeningBalancePromoterStub:
    def test_promote_returns_not_implemented(self, session, test_actor_id, deterministic_clock):
        promoter = OpeningBalancePromoter()
        result = promoter.promote({}, session, test_actor_id, deterministic_clock)
        assert result.success is False
        assert "Not implemented" in (result.error or "") or "opening balance" in (result.error or "").lower()

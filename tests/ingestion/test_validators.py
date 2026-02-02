"""Tests for domain validators (Phase 5)."""

from datetime import date
from decimal import Decimal

import pytest

from finance_ingestion.domain.types import FieldMapping
from finance_ingestion.domain.validators import (
    ENTITY_VALIDATORS,
    validate_batch_uniqueness,
    validate_currency_codes,
    validate_date_ranges,
    validate_decimal_precision,
    validate_field_types,
    validate_required_fields,
)
from finance_kernel.domain.schemas.base import EventFieldType


class TestValidateRequiredFields:
    def test_missing_required(self):
        mappings = (FieldMapping(source="a", target="x", field_type=EventFieldType.STRING, required=True),)
        errors = validate_required_fields({}, mappings)
        assert len(errors) == 1 and errors[0].code == "MISSING_REQUIRED_FIELD"

    def test_present_required(self):
        mappings = (FieldMapping(source="a", target="x", field_type=EventFieldType.STRING, required=True),)
        errors = validate_required_fields({"x": "ok"}, mappings)
        assert len(errors) == 0


class TestValidateFieldTypes:
    def test_valid_types(self):
        mappings = (
            FieldMapping(source="a", target="x", field_type=EventFieldType.STRING),
            FieldMapping(source="b", target="y", field_type=EventFieldType.INTEGER),
        )
        errors = validate_field_types({"x": "hi", "y": 42}, mappings)
        assert len(errors) == 0

    def test_invalid_type(self):
        mappings = (FieldMapping(source="b", target="y", field_type=EventFieldType.INTEGER),)
        errors = validate_field_types({"y": "not_an_int"}, mappings)
        assert len(errors) >= 1 and any(e.code == "INVALID_TYPE" for e in errors)


class TestValidateCurrencyCodes:
    def test_valid_currency(self):
        errors = validate_currency_codes({"currency": "USD"}, ("currency",))
        assert len(errors) == 0

    def test_invalid_currency(self):
        # XXX is valid (ISO 4217 "No currency"); use a code not in registry
        errors = validate_currency_codes({"currency": "NOTVALID"}, ("currency",))
        assert len(errors) == 1 and errors[0].code == "INVALID_CURRENCY"


class TestValidateDecimalPrecision:
    def test_within_precision(self):
        errors = validate_decimal_precision({"amt": Decimal("123.45")}, ("amt",))
        assert len(errors) == 0

    def test_exceeds_scale(self):
        # 10 decimal places
        errors = validate_decimal_precision({"amt": Decimal("1.1234567890")}, ("amt",))
        assert len(errors) >= 1 and any(e.code == "DECIMAL_SCALE_EXCEEDED" for e in errors)


class TestValidateDateRanges:
    def test_within_range(self):
        errors = validate_date_ranges({"d": date(2026, 2, 1)}, ("d",))
        assert len(errors) == 0

    def test_out_of_range(self):
        errors = validate_date_ranges({"d": date(1800, 1, 1)}, ("d",), min_date=date(1900, 1, 1))
        assert len(errors) == 1 and errors[0].code == "DATE_OUT_OF_RANGE"


class TestValidateBatchUniqueness:
    def test_unique_no_errors(self):
        records = [{"code": "A"}, {"code": "B"}]
        result = validate_batch_uniqueness(records, ("code",))
        assert result == {}

    def test_duplicate_adds_errors(self):
        records = [{"code": "A"}, {"code": "A"}, {"code": "B"}]
        result = validate_batch_uniqueness(records, ("code",))
        assert 0 in result and 1 in result
        assert all(e.code == "DUPLICATE_VALUE_IN_BATCH" for errs in result.values() for e in errs)


class TestEntityValidators:
    def test_validate_party_code_missing(self):
        from finance_ingestion.domain.validators import validate_party_code

        errors = validate_party_code({})
        assert len(errors) == 1 and errors[0].code == "MISSING_REQUIRED_FIELD"

    def test_validate_party_code_present(self):
        from finance_ingestion.domain.validators import validate_party_code

        errors = validate_party_code({"code": "P001"})
        assert len(errors) == 0

    def test_entity_validators_registry(self):
        assert "party" in ENTITY_VALIDATORS
        assert "vendor" in ENTITY_VALIDATORS
        assert "account" in ENTITY_VALIDATORS
        assert "item" in ENTITY_VALIDATORS

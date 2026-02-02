"""Tests for mapping engine (Phase 4)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_ingestion.domain.types import FieldMapping
from finance_ingestion.mapping.engine import (
    CoercionResult,
    MappingResult,
    apply_mapping,
    apply_transform,
    coerce_from_string,
)
from finance_kernel.domain.schemas.base import EventFieldType


class TestApplyTransform:
    def test_strip(self):
        assert apply_transform("  x  ", "strip") == "x"
        assert apply_transform("  x  ", "trim") == "x"

    def test_upper_lower(self):
        assert apply_transform("Abc", "upper") == "ABC"
        assert apply_transform("Abc", "lower") == "abc"

    def test_to_decimal(self):
        assert apply_transform("12.5", "to_decimal") == Decimal("12.5")
        assert apply_transform(10, "to_decimal") == Decimal("10")

    def test_normalize_date(self):
        assert apply_transform(date(2026, 2, 1), "normalize_date") == "2026-02-01"
        assert apply_transform("2026-02-01", "normalize_date") == "2026-02-01"

    def test_none_returns_none(self):
        assert apply_transform(None, "strip") is None


class TestCoerceFromString:
    def test_string_passthrough(self):
        r = coerce_from_string("hello", EventFieldType.STRING)
        assert r.success and r.value == "hello"

    def test_integer(self):
        r = coerce_from_string("42", EventFieldType.INTEGER)
        assert r.success and r.value == 42
        r2 = coerce_from_string("bad", EventFieldType.INTEGER)
        assert not r2.success and r2.error is not None

    def test_decimal(self):
        r = coerce_from_string("12.50", EventFieldType.DECIMAL)
        assert r.success and r.value == Decimal("12.50")

    def test_boolean(self):
        assert coerce_from_string("true", EventFieldType.BOOLEAN).value is True
        assert coerce_from_string("yes", EventFieldType.BOOLEAN).value is True
        assert coerce_from_string("false", EventFieldType.BOOLEAN).value is False
        assert coerce_from_string("no", EventFieldType.BOOLEAN).value is False

    def test_date(self):
        r = coerce_from_string("2026-02-01", EventFieldType.DATE)
        assert r.success and r.value == date(2026, 2, 1)

    def test_uuid(self):
        u = uuid4()
        r = coerce_from_string(str(u), EventFieldType.UUID)
        assert r.success and r.value == u

    def test_empty_string_non_string_type_fails(self):
        r = coerce_from_string("", EventFieldType.INTEGER)
        assert not r.success


class TestApplyMapping:
    def test_simple_mapping(self):
        mappings = (
            FieldMapping(source="a", target="x", field_type=EventFieldType.STRING),
            FieldMapping(source="b", target="y", field_type=EventFieldType.INTEGER),
        )
        raw = {"a": "hello", "b": "42"}
        result = apply_mapping(raw, mappings)
        assert result.success
        assert result.mapped_data == {"x": "hello", "y": 42}
        assert result.errors == ()

    def test_missing_required_fails(self):
        mappings = (FieldMapping(source="a", target="x", field_type=EventFieldType.STRING, required=True),)
        result = apply_mapping({}, mappings)
        assert not result.success
        assert any(e.code == "MISSING_REQUIRED_FIELD" for e in result.errors)

    def test_missing_optional_uses_default(self):
        mappings = (FieldMapping(source="a", target="x", field_type=EventFieldType.INTEGER, default=10),)
        result = apply_mapping({}, mappings)
        assert result.success
        assert result.mapped_data == {"x": 10}

    def test_transform_then_coerce(self):
        mappings = (
            FieldMapping(source="amt", target="amount", field_type=EventFieldType.DECIMAL, transform="to_decimal"),
        )
        result = apply_mapping({"amt": " 99.99 "}, mappings)
        assert result.success
        assert result.mapped_data["amount"] == Decimal("99.99")

    def test_invalid_type_collects_error(self):
        mappings = (FieldMapping(source="b", target="y", field_type=EventFieldType.INTEGER),)
        result = apply_mapping({"b": "not_a_number"}, mappings)
        assert not result.success
        assert len(result.errors) >= 1
        assert result.mapped_data.get("y") is None

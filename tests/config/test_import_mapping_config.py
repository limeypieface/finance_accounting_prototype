"""Tests for import mapping config (ERP_INGESTION_PLAN Phase 3)."""

import pytest

from finance_config.loader import parse_import_field_def, parse_import_mapping, parse_import_validation_def
from finance_config.schema import ImportFieldDef, ImportMappingDef, ImportValidationDef
from finance_kernel.domain.schemas.base import EventFieldType


class TestImportFieldDef:
    def test_parse_field_def_defaults(self):
        data = {"source": "LIFNR", "target": "code"}
        f = parse_import_field_def(data)
        assert f.source == "LIFNR" and f.target == "code"
        assert f.field_type == EventFieldType.STRING and f.required is False

    def test_parse_field_def_with_type_and_transform(self):
        data = {
            "source": "ZTERM",
            "target": "payment_terms_days",
            "field_type": "integer",
            "default": 30,
            "transform": "strip",
        }
        f = parse_import_field_def(data)
        assert f.field_type == EventFieldType.INTEGER and f.default == 30
        assert f.transform == "strip"  # ImportFieldDef has transform


class TestImportValidationDef:
    def test_parse_validation_def(self):
        data = {
            "rule_type": "unique",
            "fields": ["code"],
            "scope": "batch",
            "message": "Duplicate vendor code",
        }
        v = parse_import_validation_def(data)
        assert v.rule_type == "unique" and v.fields == ("code",)
        assert v.scope == "batch" and v.message == "Duplicate vendor code"


class TestParseImportMapping:
    def test_parse_minimal_mapping(self):
        data = {"name": "sap_vendors", "entity_type": "vendor"}
        m = parse_import_mapping(data)
        assert m.name == "sap_vendors" and m.entity_type == "vendor"
        assert m.version == 1 and m.source_format == "csv"
        assert m.field_mappings == () and m.validations == ()
        assert m.dependency_tier == 0

    def test_parse_full_mapping(self):
        data = {
            "name": "sap_vendors",
            "version": 1,
            "entity_type": "vendor",
            "source_format": "csv",
            "source_options": {"delimiter": ",", "encoding": "utf-8", "has_header": True},
            "dependency_tier": 2,
            "field_mappings": [
                {"source": "LIFNR", "target": "code", "field_type": "string", "required": True},
                {"source": "ZTERM", "target": "payment_terms_days", "field_type": "integer", "default": 30},
            ],
            "validations": [
                {"rule_type": "unique", "fields": ["code"], "scope": "batch", "message": "Duplicate"},
            ],
        }
        m = parse_import_mapping(data)
        assert m.name == "sap_vendors" and m.dependency_tier == 2
        assert len(m.field_mappings) == 2
        assert m.field_mappings[0].source == "LIFNR" and m.field_mappings[0].field_type == EventFieldType.STRING
        assert m.field_mappings[1].field_type == EventFieldType.INTEGER and m.field_mappings[1].default == 30
        assert len(m.validations) == 1 and m.validations[0].rule_type == "unique"
        assert m.source_options == {"delimiter": ",", "encoding": "utf-8", "has_header": True}

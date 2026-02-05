"""
Tests for QBO CoA extraction and recommendation (Phase 1 of QBO Loader).

Covers: extract_input_coa, load_config_coa_options, score_config_coa, recommend_coa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.qbo.coa_config import ConfigCoAOption, load_config_coa_options
from scripts.qbo.coa_extract import (
    InputCoARecord,
    _extract_code_from_name,
    extract_input_coa,
    load_qbo_accounts_json,
)
from scripts.qbo.coa_recommend import recommend_coa, score_config_coa


class TestExtractCodeFromName:
    def test_parenthesized_number(self) -> None:
        assert _extract_code_from_name("Mercury Checking (3820) - 1") == "3820"
        assert _extract_code_from_name("Account (1200)") == "1200"

    def test_parenthesized_alphanumeric(self) -> None:
        assert _extract_code_from_name("Something (SL-1001)") == "SL-1001"

    def test_no_parentheses(self) -> None:
        assert _extract_code_from_name("Mercury Payroll") is None
        assert _extract_code_from_name("") is None

    def test_empty_parentheses(self) -> None:
        assert _extract_code_from_name("Name ()") is None


class TestLoadQboAccountsJson:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_qbo_accounts_json(tmp_path / "nonexistent.json")

    def test_valid_structure(self, tmp_path: Path) -> None:
        path = tmp_path / "accounts.json"
        path.write_text('{"rows": [{"name": "Cash", "account_type": "Bank"}]}', encoding="utf-8")
        rows = load_qbo_accounts_json(path)
        assert len(rows) == 1
        assert rows[0]["name"] == "Cash"
        assert rows[0]["account_type"] == "Bank"

    def test_missing_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="no 'rows' key"):
            load_qbo_accounts_json(path)


class TestExtractInputCoa:
    def test_extract_records(self, tmp_path: Path) -> None:
        path = tmp_path / "accounts.json"
        path.write_text(
            '{"rows": ['
            '{"name": "Mercury Checking (3820) - 1", "account_type": "Bank", "_import_row": 1},'
            '{"name": "Direct Costs", "account_type": "Cost of Goods Sold"}'
            "]}",
            encoding="utf-8",
        )
        records = extract_input_coa(path)
        assert len(records) == 2
        assert records[0].name == "Mercury Checking (3820) - 1"
        assert records[0].account_type == "Bank"
        assert records[0].code == "3820"
        assert records[0].import_row == 1
        assert records[1].name == "Direct Costs"
        assert records[1].account_type == "Cost of Goods Sold"
        assert records[1].code is None

    def test_skip_empty_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "accounts.json"
        path.write_text('{"rows": [{"name": "", "account_type": ""}, {"name": "Only", "account_type": "Bank"}]}', encoding="utf-8")
        records = extract_input_coa(path)
        assert len(records) == 1
        assert records[0].name == "Only"


class TestScoreConfigCoa:
    def test_full_coverage(self) -> None:
        input_types = {"Bank", "Expenses", "Equity"}
        config_roles = frozenset({"CASH", "EXPENSE", "RETAINED_EARNINGS"})
        assert score_config_coa(input_types, config_roles) == 1.0

    def test_partial_coverage(self) -> None:
        input_types = {"Bank", "Expenses", "UnknownType"}
        config_roles = frozenset({"CASH"})  # only Bank covered
        # UnknownType gets covered if EXPENSE or REVENUE in config (we don't have)
        assert score_config_coa(input_types, config_roles) == pytest.approx(1.0 / 3.0)

    def test_empty_input(self) -> None:
        assert score_config_coa(set(), frozenset({"CASH"})) == 0.0

    def test_unknown_type_fallback(self) -> None:
        input_types = {"WeirdType"}
        config_roles = frozenset({"EXPENSE"})
        assert score_config_coa(input_types, config_roles) == 1.0


class TestRecommendCoa:
    def test_recommend_returns_sorted(self) -> None:
        input_records = [
            InputCoARecord("A", "Bank", None, None, 1),
            InputCoARecord("B", "Expenses", None, None, 2),
        ]
        options = [
            ConfigCoAOption("minimal", frozenset({"CASH"}), frozenset([("CASH", "1000")])),
            ConfigCoAOption(
                "full",
                frozenset({"CASH", "EXPENSE", "RETAINED_EARNINGS"}),
                frozenset([("CASH", "1000"), ("EXPENSE", "6000"), ("RETAINED_EARNINGS", "3000")]),
            ),
        ]
        result = recommend_coa(input_records, options)
        assert len(result) == 2
        assert result[0].config_id == "full"
        assert result[0].score == 1.0
        assert result[1].config_id == "minimal"
        assert result[1].score == pytest.approx(0.5)  # only Bank covered (CASH), Expenses not in minimal

    def test_recommend_empty_options(self) -> None:
        input_records = [InputCoARecord("A", "Bank", None, None, 1)]
        result = recommend_coa(input_records, [])
        assert result == []


class TestRecommendAccountMapping:
    def test_recommends_target_code_from_config(self) -> None:
        from scripts.qbo.coa_map import recommend_account_mapping

        input_records = [
            InputCoARecord("Cash", "Bank", None, None, 1),
            InputCoARecord("Unknown Type", "Weird", None, None, 2),
        ]
        config = ConfigCoAOption(
            "test",
            frozenset({"CASH", "EXPENSE"}),
            frozenset([("CASH", "1000"), ("EXPENSE", "6000")]),
        )
        recs = recommend_account_mapping(input_records, config)
        assert len(recs) == 2
        assert recs[0].input_name == "Cash"
        assert recs[0].recommended_role == "CASH"
        assert recs[0].recommended_code == "1000"
        assert recs[0].target_code == "1010"
        assert recs[1].recommended_code is None
        assert recs[1].suggested_new_code is not None
        assert recs[1].target_code == recs[1].suggested_new_code
        assert recs[1].target_name == "Unknown Type"

    def test_suggest_new_code_uses_numbering_scheme(self) -> None:
        from scripts.qbo.coa_map import suggest_new_code

        config_codes = {"1000", "1010", "6000"}
        used = set(config_codes)
        assert suggest_new_code("Bank", used, config_codes=config_codes) == "1020"
        assert suggest_new_code("Expenses", used, config_codes=config_codes) == "6010"
        assert suggest_new_code("Unknown", used, config_codes=config_codes) == "6900"
        used.add("6600")
        assert suggest_new_code("Other Expense", used, config_codes=config_codes) == "6610"

    def test_mapping_to_yaml_roundtrip(self) -> None:
        from scripts.qbo.coa_map import AccountMappingRecommendation, mapping_to_yaml

        recs = [
            AccountMappingRecommendation(
                "Test Account", "123", "Bank", 1, "CASH", "1000", None, "1000", "Test Account"
            ),
        ]
        yaml = mapping_to_yaml(recs, "US-GAAP-2026-v1")
        assert "config_id: US-GAAP-2026-v1" in yaml
        assert "target_code: '1000'" in yaml
        assert "target_name: 'Test Account'" in yaml
        assert "recommended_role: 'CASH'" in yaml


class TestLoadConfigCoaOptions:
    def test_loads_from_project_sets(self) -> None:
        # Use project's finance_config/sets if present
        options = load_config_coa_options()
        # We expect at least one set (US-GAAP-2026-v1, etc.)
        assert len(options) >= 1
        config_ids = [o.config_id for o in options]
        assert any("US-GAAP" in c for c in config_ids)
        for opt in options:
            assert len(opt.roles) > 0

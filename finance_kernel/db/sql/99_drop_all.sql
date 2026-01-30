-- =============================================================================
-- Drop All Immutability Triggers
-- =============================================================================
-- WARNING: Only use this for migrations that need to modify historical data.
-- Re-install triggers immediately after the migration completes.
--
-- Usage: Run this script, perform migration, then re-run install.
-- =============================================================================

-- Drop all triggers
DROP TRIGGER IF EXISTS trg_journal_entry_immutability_update ON journal_entries;
DROP TRIGGER IF EXISTS trg_journal_entry_immutability_delete ON journal_entries;
DROP TRIGGER IF EXISTS trg_journal_line_immutability_update ON journal_lines;
DROP TRIGGER IF EXISTS trg_journal_line_immutability_delete ON journal_lines;
DROP TRIGGER IF EXISTS trg_audit_event_immutability_update ON audit_events;
DROP TRIGGER IF EXISTS trg_audit_event_immutability_delete ON audit_events;
DROP TRIGGER IF EXISTS trg_account_structural_immutability_update ON accounts;
DROP TRIGGER IF EXISTS trg_account_last_rounding_delete ON accounts;
DROP TRIGGER IF EXISTS trg_fiscal_period_immutability_update ON fiscal_periods;
DROP TRIGGER IF EXISTS trg_fiscal_period_immutability_delete ON fiscal_periods;
DROP TRIGGER IF EXISTS trg_journal_line_single_rounding ON journal_lines;
DROP TRIGGER IF EXISTS trg_journal_line_rounding_threshold ON journal_lines;
DROP TRIGGER IF EXISTS trg_dimension_code_immutability ON dimensions;
DROP TRIGGER IF EXISTS trg_dimension_deletion_protection ON dimensions;
DROP TRIGGER IF EXISTS trg_dimension_value_structural_immutability ON dimension_values;
DROP TRIGGER IF EXISTS trg_dimension_value_deletion_protection ON dimension_values;
DROP TRIGGER IF EXISTS trg_exchange_rate_validate ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_immutability ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_delete ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_arbitrage ON exchange_rates;
DROP TRIGGER IF EXISTS trg_event_immutability_update ON events;
DROP TRIGGER IF EXISTS trg_event_immutability_delete ON events;
DROP TRIGGER IF EXISTS trg_journal_entry_balance_check ON journal_entries;
DROP TRIGGER IF EXISTS trg_journal_line_no_insert_posted ON journal_lines;

-- Drop all functions
DROP FUNCTION IF EXISTS prevent_posted_journal_entry_modification();
DROP FUNCTION IF EXISTS prevent_posted_journal_entry_deletion();
DROP FUNCTION IF EXISTS prevent_posted_journal_line_modification();
DROP FUNCTION IF EXISTS prevent_posted_journal_line_deletion();
DROP FUNCTION IF EXISTS prevent_audit_event_modification();
DROP FUNCTION IF EXISTS prevent_audit_event_deletion();
DROP FUNCTION IF EXISTS prevent_referenced_account_structural_modification();
DROP FUNCTION IF EXISTS prevent_last_rounding_account_deletion();
DROP FUNCTION IF EXISTS prevent_closed_fiscal_period_modification();
DROP FUNCTION IF EXISTS prevent_fiscal_period_deletion();
DROP FUNCTION IF EXISTS enforce_single_rounding_line();
DROP FUNCTION IF EXISTS enforce_rounding_threshold();
DROP FUNCTION IF EXISTS prevent_dimension_code_modification();
DROP FUNCTION IF EXISTS prevent_dimension_deletion_with_values();
DROP FUNCTION IF EXISTS prevent_dimension_value_structural_modification();
DROP FUNCTION IF EXISTS prevent_referenced_dimension_value_deletion();
DROP FUNCTION IF EXISTS validate_exchange_rate_value();
DROP FUNCTION IF EXISTS prevent_referenced_exchange_rate_modification();
DROP FUNCTION IF EXISTS prevent_referenced_exchange_rate_deletion();
DROP FUNCTION IF EXISTS check_exchange_rate_arbitrage();
DROP FUNCTION IF EXISTS prevent_event_modification();
DROP FUNCTION IF EXISTS prevent_event_deletion();
DROP FUNCTION IF EXISTS enforce_balanced_journal_entry();
DROP FUNCTION IF EXISTS prevent_line_insert_on_posted_entry();

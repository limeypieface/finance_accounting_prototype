-- =============================================================================
-- Journal Line Immutability Triggers
-- =============================================================================
-- R10 Compliance: Journal lines are immutable once parent entry is posted.
--
-- Invariants enforced:
--   1. Lines cannot be modified when parent entry is posted
--   2. Lines cannot be deleted when parent entry is posted
--
-- This ensures the detailed breakdown of an entry cannot be altered
-- after the entry becomes part of the official ledger.
-- =============================================================================

-- Function: Prevent modifications to journal lines when parent is posted
CREATE OR REPLACE FUNCTION prevent_posted_journal_line_modification()
RETURNS TRIGGER AS $$
DECLARE
    parent_status VARCHAR(20);
BEGIN
    -- Get parent entry status
    SELECT status INTO parent_status
    FROM journal_entries
    WHERE id = OLD.journal_entry_id;

    IF parent_status = 'posted' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify journal line % - parent entry is posted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of journal lines when parent is posted
CREATE OR REPLACE FUNCTION prevent_posted_journal_line_deletion()
RETURNS TRIGGER AS $$
DECLARE
    parent_status VARCHAR(20);
BEGIN
    -- Get parent entry status
    SELECT status INTO parent_status
    FROM journal_entries
    WHERE id = OLD.journal_entry_id;

    IF parent_status = 'posted' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete journal line % - parent entry is posted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_journal_line_immutability_update ON journal_lines;
DROP TRIGGER IF EXISTS trg_journal_line_immutability_delete ON journal_lines;

-- Create triggers
CREATE TRIGGER trg_journal_line_immutability_update
    BEFORE UPDATE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_line_modification();

CREATE TRIGGER trg_journal_line_immutability_delete
    BEFORE DELETE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_line_deletion();

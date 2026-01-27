-- =============================================================================
-- Journal Entry Immutability Triggers
-- =============================================================================
-- R10 Compliance: Posted journal entries cannot be modified or deleted.
--
-- Invariants enforced:
--   1. Posted entries cannot be modified (except initial posting transition)
--   2. Posted entries cannot be deleted
--
-- The only allowed state change is: draft/pending -> posted (one-time)
-- =============================================================================

-- Function: Prevent modifications to posted journal entries
-- Allows: draft -> posted transition (the posting itself)
-- Blocks: Any modification after status = 'posted'
CREATE OR REPLACE FUNCTION prevent_posted_journal_entry_modification()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow the initial posting (transition TO posted status)
    IF TG_OP = 'UPDATE' AND OLD.status != 'posted' AND NEW.status = 'posted' THEN
        RETURN NEW;
    END IF;

    -- Block all other modifications to posted entries
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify posted journal entry %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of posted journal entries
CREATE OR REPLACE FUNCTION prevent_posted_journal_entry_deletion()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete posted journal entry %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_journal_entry_immutability_update ON journal_entries;
DROP TRIGGER IF EXISTS trg_journal_entry_immutability_delete ON journal_entries;

-- Create triggers
CREATE TRIGGER trg_journal_entry_immutability_update
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_entry_modification();

CREATE TRIGGER trg_journal_entry_immutability_delete
    BEFORE DELETE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_entry_deletion();

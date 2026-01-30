-- =============================================================================
-- Journal Entry Balance Enforcement Trigger
-- =============================================================================
-- R12 Compliance: Posted journal entries must be balanced.
--
-- Invariant enforced:
--   SUM(debits) = SUM(credits) for all posted journal entries
--
-- This trigger fires BEFORE the status transitions to 'posted' and verifies
-- that the entry is balanced. Unbalanced entries cannot be posted.
--
-- Defense-in-depth: This complements application-level validation by enforcing
-- the balance constraint at the database level, preventing bypass via raw SQL.
-- =============================================================================

-- Function: Enforce balanced entries on posting
-- Checks that total debits equal total credits before allowing posting
CREATE OR REPLACE FUNCTION enforce_balanced_journal_entry()
RETURNS TRIGGER AS $$
DECLARE
    total_debits NUMERIC(38, 9);
    total_credits NUMERIC(38, 9);
    difference NUMERIC(38, 9);
    line_count INTEGER;
BEGIN
    -- Only check when transitioning TO posted status
    IF TG_OP = 'UPDATE' AND OLD.status != 'posted' AND NEW.status = 'posted' THEN

        -- Get the totals for this entry
        SELECT
            COALESCE(SUM(CASE WHEN side = 'debit' THEN amount ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN side = 'credit' THEN amount ELSE 0 END), 0),
            COUNT(*)
        INTO total_debits, total_credits, line_count
        FROM journal_lines
        WHERE journal_entry_id = NEW.id;

        -- Must have at least 2 lines
        IF line_count < 2 THEN
            RAISE EXCEPTION 'R12 Violation: Cannot post journal entry % with fewer than 2 lines (has %)',
                NEW.id, line_count
                USING ERRCODE = 'check_violation';
        END IF;

        -- Calculate difference (should be exactly zero)
        difference := total_debits - total_credits;

        -- Check balance (using exact comparison for NUMERIC)
        IF difference != 0 THEN
            RAISE EXCEPTION 'R12 Violation: Cannot post unbalanced journal entry %. Debits=%, Credits=%, Difference=%',
                NEW.id, total_debits, total_credits, difference
                USING ERRCODE = 'check_violation';
        END IF;

        -- Entries must have positive amounts
        IF total_debits <= 0 THEN
            RAISE EXCEPTION 'R12 Violation: Cannot post journal entry % with zero or negative total (Debits=%)',
                NEW.id, total_debits
                USING ERRCODE = 'check_violation';
        END IF;

    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop existing trigger (idempotent installation)
DROP TRIGGER IF EXISTS trg_journal_entry_balance_check ON journal_entries;

-- Create trigger - fires BEFORE the immutability trigger
-- Priority: Balance check must happen before immutability locks the entry
CREATE TRIGGER trg_journal_entry_balance_check
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION enforce_balanced_journal_entry();


-- =============================================================================
-- Additional: Prevent modifications to lines after entry is posted
-- This is defense-in-depth - also enforced in 02_journal_line.sql
-- =============================================================================

-- Function: Prevent adding lines to posted entries
CREATE OR REPLACE FUNCTION prevent_line_insert_on_posted_entry()
RETURNS TRIGGER AS $$
DECLARE
    entry_status TEXT;
BEGIN
    -- Check if the parent entry is already posted
    SELECT status INTO entry_status
    FROM journal_entries
    WHERE id = NEW.journal_entry_id;

    IF entry_status = 'posted' THEN
        RAISE EXCEPTION 'R12 Violation: Cannot add lines to posted journal entry %',
            NEW.journal_entry_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop existing trigger (idempotent installation)
DROP TRIGGER IF EXISTS trg_journal_line_no_insert_posted ON journal_lines;

-- Create trigger
CREATE TRIGGER trg_journal_line_no_insert_posted
    BEFORE INSERT ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_line_insert_on_posted_entry();

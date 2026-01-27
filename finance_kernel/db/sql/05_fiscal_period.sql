-- =============================================================================
-- Fiscal Period Immutability Triggers
-- =============================================================================
-- R12/R13 Compliance: Closed periods are immutable; period lifecycle is one-way.
--
-- Invariants enforced:
--   1. Closed periods cannot be modified (except the closing transition itself)
--   2. Closed periods cannot be deleted
--   3. Periods with journal entries cannot be deleted (even if open)
--
-- Fiscal periods define the accounting calendar. Once closed, they represent
-- a finalized reporting period that cannot be altered.
-- =============================================================================

-- Function: Prevent modifications to closed fiscal periods
-- Allows: open -> closed transition (closing the period)
-- Blocks: Any modification after status = 'closed'
CREATE OR REPLACE FUNCTION prevent_closed_fiscal_period_modification()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow the closing transition (OPEN -> CLOSED)
    IF TG_OP = 'UPDATE' AND OLD.status = 'open' AND NEW.status = 'closed' THEN
        RETURN NEW;
    END IF;

    -- Block all other modifications to closed periods
    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify closed fiscal period %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of closed fiscal periods or periods with entries
CREATE OR REPLACE FUNCTION prevent_fiscal_period_deletion()
RETURNS TRIGGER AS $$
DECLARE
    has_entries BOOLEAN;
BEGIN
    -- Rule 1: Closed periods cannot be deleted
    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete closed fiscal period %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Rule 2: Periods with journal entries cannot be deleted
    -- (even open periods, to prevent data orphaning)
    SELECT EXISTS (
        SELECT 1 FROM journal_entries
        WHERE effective_date >= OLD.start_date
        AND effective_date <= OLD.end_date
    ) INTO has_entries;

    IF has_entries THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete fiscal period % - has journal entries', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_fiscal_period_immutability_update ON fiscal_periods;
DROP TRIGGER IF EXISTS trg_fiscal_period_immutability_delete ON fiscal_periods;

-- Create triggers
CREATE TRIGGER trg_fiscal_period_immutability_update
    BEFORE UPDATE ON fiscal_periods
    FOR EACH ROW
    EXECUTE FUNCTION prevent_closed_fiscal_period_modification();

CREATE TRIGGER trg_fiscal_period_immutability_delete
    BEFORE DELETE ON fiscal_periods
    FOR EACH ROW
    EXECUTE FUNCTION prevent_fiscal_period_deletion();

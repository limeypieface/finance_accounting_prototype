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

-- Function: Prevent modifications to closed/locked fiscal periods
-- Allowed transitions (period close lifecycle):
--   OPEN -> CLOSING      (begin_closing — R25 close lock)
--   CLOSING -> OPEN      (cancel_closing — release lock)
--   CLOSING -> CLOSED    (close_period — orchestrated close)
--   OPEN -> CLOSED       (close_period — direct close without orchestrator)
--   CLOSED -> LOCKED     (lock_period — year-end permanent seal)
-- Blocks: Any modification after status = 'closed' or 'locked' (except above)
CREATE OR REPLACE FUNCTION prevent_closed_fiscal_period_modification()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- Allow explicitly defined status transitions
        IF OLD.status = 'open' AND NEW.status IN ('closing', 'closed') THEN
            RETURN NEW;
        END IF;
        IF OLD.status = 'closing' AND NEW.status IN ('open', 'closed') THEN
            RETURN NEW;
        END IF;
        IF OLD.status = 'closed' AND NEW.status = 'locked' THEN
            RETURN NEW;
        END IF;
    END IF;

    -- Block all other modifications to closed or locked periods
    IF OLD.status IN ('closed', 'locked') THEN
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
    -- Rule 1: Closed or locked periods cannot be deleted
    IF OLD.status IN ('closed', 'locked') THEN
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

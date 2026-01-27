-- =============================================================================
-- Rounding Line Fraud Prevention Triggers
-- =============================================================================
-- These triggers prevent abuse of rounding lines to hide manipulation.
--
-- Invariants enforced:
--   1. At most ONE rounding line per journal entry
--   2. Rounding amount must be small (< 0.01 per non-rounding line)
--
-- Background: Currency conversion can produce sub-penny remainders that must
-- be captured in a rounding line. However, malicious actors could abuse this
-- to hide larger amounts. These triggers detect such attempts.
-- =============================================================================

-- Function: Enforce at most ONE rounding line per entry
-- Multiple rounding lines could be used to hide manipulation by spreading
-- illicit amounts across several "rounding" entries.
CREATE OR REPLACE FUNCTION enforce_single_rounding_line()
RETURNS TRIGGER AS $$
DECLARE
    rounding_count INTEGER;
BEGIN
    -- Only check if this line is marked as rounding
    IF NEW.is_rounding = false THEN
        RETURN NEW;
    END IF;

    -- Count existing rounding lines for this entry (excluding current row on UPDATE)
    IF TG_OP = 'INSERT' THEN
        SELECT COUNT(*) INTO rounding_count
        FROM journal_lines
        WHERE journal_entry_id = NEW.journal_entry_id
        AND is_rounding = true;
    ELSE
        -- UPDATE case: exclude the current row
        SELECT COUNT(*) INTO rounding_count
        FROM journal_lines
        WHERE journal_entry_id = NEW.journal_entry_id
        AND is_rounding = true
        AND id != NEW.id;
    END IF;

    -- If there's already a rounding line, reject this one
    IF rounding_count >= 1 THEN
        RAISE EXCEPTION 'ROUNDING_INVARIANT_VIOLATION: Entry % cannot have more than one rounding line. Found % existing rounding line(s).',
            NEW.journal_entry_id, rounding_count
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Enforce rounding amount threshold
-- Legitimate rounding from currency conversion should be < 0.01 per line.
-- Large "rounding" amounts are suspicious and may indicate fraud.
CREATE OR REPLACE FUNCTION enforce_rounding_threshold()
RETURNS TRIGGER AS $$
DECLARE
    non_rounding_line_count INTEGER;
    max_allowed_rounding NUMERIC(38, 9);
BEGIN
    -- Only check if this line is marked as rounding
    IF NEW.is_rounding = false THEN
        RETURN NEW;
    END IF;

    -- Count non-rounding lines for this entry
    SELECT COUNT(*) INTO non_rounding_line_count
    FROM journal_lines
    WHERE journal_entry_id = NEW.journal_entry_id
    AND is_rounding = false;

    -- If no non-rounding lines yet, allow a small base threshold
    IF non_rounding_line_count = 0 THEN
        max_allowed_rounding := 0.01;
    ELSE
        -- Allow 0.01 per non-rounding line (sub-penny conversion remainders)
        max_allowed_rounding := non_rounding_line_count * 0.01;
    END IF;

    -- Check if rounding exceeds threshold
    IF NEW.amount > max_allowed_rounding THEN
        RAISE EXCEPTION 'ROUNDING_THRESHOLD_VIOLATION: Rounding amount % exceeds maximum allowed % (0.01 per line) for entry %. Large "rounding" is not rounding - it may indicate fraud.',
            NEW.amount, max_allowed_rounding, NEW.journal_entry_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_journal_line_single_rounding ON journal_lines;
DROP TRIGGER IF EXISTS trg_journal_line_rounding_threshold ON journal_lines;

-- Create triggers (run BEFORE INSERT/UPDATE)
CREATE TRIGGER trg_journal_line_single_rounding
    BEFORE INSERT OR UPDATE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION enforce_single_rounding_line();

CREATE TRIGGER trg_journal_line_rounding_threshold
    BEFORE INSERT OR UPDATE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION enforce_rounding_threshold();

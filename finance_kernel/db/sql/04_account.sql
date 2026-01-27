-- =============================================================================
-- Account Immutability Triggers
-- =============================================================================
-- R10 Compliance: Account structural fields are immutable once referenced.
--
-- Invariants enforced:
--   1. Structural fields (account_type, normal_balance, code) cannot change
--      once the account OR ANY DESCENDANT has posted journal lines
--   2. The last rounding account per currency cannot be deleted
--
-- Structural fields define the accounting behavior of an account.
-- Changing them after transactions exist would corrupt historical reports.
-- =============================================================================

-- Function: Prevent structural field changes on accounts with posted references
-- Note: Checks the ENTIRE account hierarchy (account + all descendants)
CREATE OR REPLACE FUNCTION prevent_referenced_account_structural_modification()
RETURNS TRIGGER AS $$
DECLARE
    has_posted_refs BOOLEAN;
BEGIN
    -- Only check if structural fields changed
    IF (OLD.account_type = NEW.account_type
        AND OLD.normal_balance = NEW.normal_balance
        AND OLD.code = NEW.code) THEN
        -- No structural changes, allow update
        RETURN NEW;
    END IF;

    -- Check if account OR ANY DESCENDANT is referenced by posted journal lines
    -- This protects the financial integrity of the entire account hierarchy
    WITH RECURSIVE account_tree AS (
        -- Base case: the account itself
        SELECT id FROM accounts WHERE id = OLD.id
        UNION ALL
        -- Recursive case: all children
        SELECT a.id
        FROM accounts a
        JOIN account_tree t ON a.parent_id = t.id
    )
    SELECT EXISTS (
        SELECT 1 FROM journal_lines jl
        JOIN journal_entries je ON jl.journal_entry_id = je.id
        WHERE jl.account_id IN (SELECT id FROM account_tree)
        AND je.status = 'posted'
    ) INTO has_posted_refs;

    IF has_posted_refs THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify structural fields (account_type, normal_balance, code) on account % - account or descendants have posted journal entries', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of the last rounding account per currency
-- At least one rounding account must exist to handle currency conversion remainders
CREATE OR REPLACE FUNCTION prevent_last_rounding_account_deletion()
RETURNS TRIGGER AS $$
DECLARE
    other_rounding_count INTEGER;
BEGIN
    -- Check if this is a rounding account
    IF OLD.tags::text NOT LIKE '%rounding%' THEN
        -- Not a rounding account, allow deletion
        RETURN OLD;
    END IF;

    -- Count other rounding accounts for the same currency
    IF OLD.currency IS NOT NULL THEN
        SELECT COUNT(*) INTO other_rounding_count
        FROM accounts
        WHERE tags::text LIKE '%rounding%'
        AND currency = OLD.currency
        AND id != OLD.id;
    ELSE
        -- Count other global/multi-currency rounding accounts
        SELECT COUNT(*) INTO other_rounding_count
        FROM accounts
        WHERE tags::text LIKE '%rounding%'
        AND currency IS NULL
        AND id != OLD.id;
    END IF;

    -- If no other rounding accounts exist, block deletion
    IF other_rounding_count = 0 THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete the last rounding account for currency %. At least one rounding account must exist per currency.', COALESCE(OLD.currency, 'global')
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_account_structural_immutability_update ON accounts;
DROP TRIGGER IF EXISTS trg_account_last_rounding_delete ON accounts;

-- Create triggers
CREATE TRIGGER trg_account_structural_immutability_update
    BEFORE UPDATE ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION prevent_referenced_account_structural_modification();

CREATE TRIGGER trg_account_last_rounding_delete
    BEFORE DELETE ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION prevent_last_rounding_account_deletion();

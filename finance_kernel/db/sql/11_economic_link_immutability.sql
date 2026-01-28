-- =============================================================================
-- Economic Link Immutability Triggers
-- =============================================================================
-- L1 Compliance: Economic links are immutable after creation.
--
-- Invariants enforced:
--   1. Links can NEVER be modified (no exceptions)
--   2. Links can NEVER be deleted (no exceptions)
--
-- Economic links represent the permanent relationships between artifacts.
-- Once created, they must remain unchanged to ensure:
--   - Graph traversal consistency (ancestry never changes)
--   - Audit integrity (proof of economic relationships)
--   - Reversal correctness (compensating links, not deletions)
--
-- To "undo" a relationship, create a compensating link (e.g., REVERSED_BY)
-- rather than deleting the original.
-- =============================================================================

-- Function: Prevent ANY modifications to economic links
CREATE OR REPLACE FUNCTION prevent_economic_link_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'L1 Violation: Economic links are immutable - cannot modify link %', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent ANY deletion of economic links
CREATE OR REPLACE FUNCTION prevent_economic_link_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'L1 Violation: Economic links are immutable - cannot delete link %', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_economic_link_immutability_update ON economic_links;
DROP TRIGGER IF EXISTS trg_economic_link_immutability_delete ON economic_links;

-- Create triggers
CREATE TRIGGER trg_economic_link_immutability_update
    BEFORE UPDATE ON economic_links
    FOR EACH ROW
    EXECUTE FUNCTION prevent_economic_link_modification();

CREATE TRIGGER trg_economic_link_immutability_delete
    BEFORE DELETE ON economic_links
    FOR EACH ROW
    EXECUTE FUNCTION prevent_economic_link_deletion();

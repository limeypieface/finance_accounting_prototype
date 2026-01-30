-- =============================================================================
-- Event Immutability Triggers
-- =============================================================================
-- R10 Compliance: Events are immutable after ingestion.
--
-- Invariants enforced:
--   1. Events can NEVER be modified (no exceptions)
--   2. Events can NEVER be deleted (no exceptions)
--
-- Events are the source of truth for all financial postings. Once ingested,
-- they must remain unchanged to ensure:
--   - Replay consistency (same event always produces same result)
--   - Audit integrity (proof of what actually happened)
--   - Tamper detection (payload_hash verification)
-- =============================================================================

-- Function: Prevent ANY modifications to events
CREATE OR REPLACE FUNCTION prevent_event_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'R10 Violation: Events are immutable - cannot modify event %', OLD.event_id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent ANY deletion of events
CREATE OR REPLACE FUNCTION prevent_event_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'R10 Violation: Events are immutable - cannot delete event %', OLD.event_id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_event_immutability_update ON events;
DROP TRIGGER IF EXISTS trg_event_immutability_delete ON events;

-- Create triggers
CREATE TRIGGER trg_event_immutability_update
    BEFORE UPDATE ON events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_event_modification();

CREATE TRIGGER trg_event_immutability_delete
    BEFORE DELETE ON events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_event_deletion();

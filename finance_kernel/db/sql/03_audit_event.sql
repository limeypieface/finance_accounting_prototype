-- =============================================================================
-- Audit Event Immutability Triggers
-- =============================================================================
-- R10/R11 Compliance: Audit events are ALWAYS immutable.
--
-- Invariants enforced:
--   1. Audit events can NEVER be modified (no exceptions)
--   2. Audit events can NEVER be deleted (no exceptions)
--
-- The audit trail is sacred. Unlike journal entries (which allow the
-- posting transition), audit events are immutable from the moment of
-- creation. This enables hash chain verification and tamper detection.
-- =============================================================================

-- Function: Prevent ANY modifications to audit events
-- Note: No exceptions - audit events are unconditionally immutable
CREATE OR REPLACE FUNCTION prevent_audit_event_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'R10 Violation: Audit events are immutable - cannot modify event seq %', OLD.seq
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent ANY deletion of audit events
CREATE OR REPLACE FUNCTION prevent_audit_event_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'R10 Violation: Audit events are immutable - cannot delete event seq %', OLD.seq
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_audit_event_immutability_update ON audit_events;
DROP TRIGGER IF EXISTS trg_audit_event_immutability_delete ON audit_events;

-- Create triggers
CREATE TRIGGER trg_audit_event_immutability_update
    BEFORE UPDATE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_event_modification();

CREATE TRIGGER trg_audit_event_immutability_delete
    BEFORE DELETE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_event_deletion();

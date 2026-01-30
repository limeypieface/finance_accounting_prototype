-- 13_outcome_exception_lifecycle.sql
--
-- Phase 9: Financial Exception Lifecycle — extends interpretation_outcomes
-- with failure context fields for work queue routing, retry tracking, and
-- durable failure recording.
--
-- These columns are added to the existing interpretation_outcomes table
-- created by ORM (Base.metadata.create_all). This script provides the
-- explicit DDL for production deployments.

-- New columns for failure context
ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    failure_type VARCHAR(30);

ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    failure_message TEXT;

ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    engine_traces_ref VARCHAR(36);

ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    payload_fingerprint VARCHAR(64);

ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    actor_id VARCHAR(36);

ALTER TABLE interpretation_outcomes ADD COLUMN IF NOT EXISTS
    retry_count INTEGER NOT NULL DEFAULT 0;

-- Work queue indexes
CREATE INDEX IF NOT EXISTS idx_outcome_failure_type
    ON interpretation_outcomes (failure_type);

CREATE INDEX IF NOT EXISTS idx_outcome_actor
    ON interpretation_outcomes (actor_id);

CREATE INDEX IF NOT EXISTS idx_outcome_failure_status
    ON interpretation_outcomes (status, failure_type);

-- CHECK constraint: failure_type must be a known value when set
-- (guard, engine, reconciliation, snapshot, authority, contract, system)
ALTER TABLE interpretation_outcomes ADD CONSTRAINT chk_failure_type
    CHECK (
        failure_type IS NULL
        OR failure_type IN (
            'guard', 'engine', 'reconciliation',
            'snapshot', 'authority', 'contract', 'system'
        )
    );

-- CHECK constraint: status must be a known value
ALTER TABLE interpretation_outcomes ADD CONSTRAINT chk_outcome_status
    CHECK (
        status IN (
            'posted', 'blocked', 'rejected', 'provisional',
            'non_posting', 'failed', 'retrying', 'abandoned'
        )
    );

-- CHECK constraint: retry_count >= 0
ALTER TABLE interpretation_outcomes ADD CONSTRAINT chk_retry_count_non_negative
    CHECK (retry_count >= 0);

-- CHECK constraint: FAILED/RETRYING/ABANDONED must have failure_type
ALTER TABLE interpretation_outcomes ADD CONSTRAINT chk_failure_type_required
    CHECK (
        status NOT IN ('failed', 'retrying', 'abandoned')
        OR failure_type IS NOT NULL
    );

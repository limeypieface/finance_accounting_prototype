-- =============================================================================
-- Cost Lot Table
-- =============================================================================
-- G13 Compliance: Cost lots persisted to database for restart safety,
-- concurrent access, and audit trail.
--
-- Invariants enforced:
--   C1: original_quantity > 0
--   C2: original_cost >= 0
--   C3: source_event_id NOT NULL (every lot is traceable)
--   C4: item_id + lot_date support FIFO/LIFO ordering
--
-- Remaining quantity is NOT stored here — it is derived from CONSUMED_BY
-- links in the economic_links table via LinkGraphService.
-- =============================================================================

CREATE TABLE IF NOT EXISTS cost_lots (
    id              VARCHAR(36) PRIMARY KEY,
    item_id         VARCHAR(100) NOT NULL,
    location_id     VARCHAR(100),
    lot_date        DATE NOT NULL,
    original_quantity NUMERIC(38, 9) NOT NULL CHECK (original_quantity > 0),
    quantity_unit   VARCHAR(20) NOT NULL DEFAULT 'EA',
    original_cost   NUMERIC(38, 9) NOT NULL CHECK (original_cost >= 0),
    currency        VARCHAR(3) NOT NULL,
    cost_method     VARCHAR(20) NOT NULL,
    source_event_id VARCHAR(36) NOT NULL,
    source_artifact_type VARCHAR(50) NOT NULL,
    source_artifact_id   VARCHAR(36) NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    lot_metadata    JSONB
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_cost_lot_item_date
    ON cost_lots (item_id, lot_date);

CREATE INDEX IF NOT EXISTS idx_cost_lot_item_location
    ON cost_lots (item_id, location_id);

CREATE INDEX IF NOT EXISTS idx_cost_lot_source_event
    ON cost_lots (source_event_id);

CREATE INDEX IF NOT EXISTS idx_cost_lot_method
    ON cost_lots (cost_method);

CREATE INDEX IF NOT EXISTS idx_cost_lot_created_at
    ON cost_lots (created_at);

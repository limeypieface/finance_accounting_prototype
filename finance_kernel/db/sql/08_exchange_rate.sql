-- =============================================================================
-- Exchange Rate Immutability & Validation Triggers
-- =============================================================================
-- R10 Compliance: Exchange rates are immutable once used in journal lines.
--
-- Invariants enforced:
--   1. Rate must be positive and non-zero (mathematically valid)
--   2. Rate must be <= 1,000,000 (sanity check for data entry errors)
--   3. Referenced rates cannot be modified
--   4. Referenced rates cannot be deleted
--   5. Rate and inverse rate must be consistent (no arbitrage opportunities)
--
-- Exchange rates determine the conversion between currencies. Changing them
-- after use would silently alter the value of historical transactions.
-- =============================================================================

-- Function: Validate exchange rate value
-- Ensures rates are mathematically valid and reasonable
CREATE OR REPLACE FUNCTION validate_exchange_rate_value()
RETURNS TRIGGER AS $$
BEGIN
    -- Check for null
    IF NEW.rate IS NULL THEN
        RAISE EXCEPTION 'EXCHANGE_RATE_INVALID: Rate cannot be null for % to %',
            NEW.from_currency, NEW.to_currency
            USING ERRCODE = 'check_violation';
    END IF;

    -- Check for zero or negative
    IF NEW.rate <= 0 THEN
        RAISE EXCEPTION 'EXCHANGE_RATE_INVALID: Rate must be positive (got %) for % to %. Zero or negative rates are mathematically invalid.',
            NEW.rate, NEW.from_currency, NEW.to_currency
            USING ERRCODE = 'check_violation';
    END IF;

    -- Check for unreasonably large rates (potential data entry error)
    IF NEW.rate > 1000000 THEN
        RAISE EXCEPTION 'EXCHANGE_RATE_INVALID: Rate % exceeds maximum allowed value (1,000,000) for % to %. This may indicate a data entry error.',
            NEW.rate, NEW.from_currency, NEW.to_currency
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent modification of exchange rates that have been used
CREATE OR REPLACE FUNCTION prevent_referenced_exchange_rate_modification()
RETURNS TRIGGER AS $$
DECLARE
    reference_count INTEGER;
BEGIN
    -- Only check if rate value is changing
    IF OLD.rate = NEW.rate THEN
        RETURN NEW;  -- Rate not changing, allow other updates
    END IF;

    -- Check if this rate has been used in any journal line
    SELECT COUNT(*) INTO reference_count
    FROM journal_lines
    WHERE exchange_rate_id = OLD.id;

    IF reference_count > 0 THEN
        RAISE EXCEPTION 'EXCHANGE_RATE_IMMUTABLE: Cannot modify rate for % (% to %) - it has been used in % journal line(s). Historical rates are frozen to preserve audit trail.',
            OLD.id, OLD.from_currency, OLD.to_currency, reference_count
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of exchange rates that are referenced
CREATE OR REPLACE FUNCTION prevent_referenced_exchange_rate_deletion()
RETURNS TRIGGER AS $$
DECLARE
    reference_count INTEGER;
BEGIN
    -- Check if this rate has been used in any journal line
    SELECT COUNT(*) INTO reference_count
    FROM journal_lines
    WHERE exchange_rate_id = OLD.id;

    IF reference_count > 0 THEN
        RAISE EXCEPTION 'EXCHANGE_RATE_REFERENCED: Cannot delete rate % (% to %) - it is referenced by % journal line(s). Deleting would break audit trail.',
            OLD.id, OLD.from_currency, OLD.to_currency, reference_count
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Function: Check for arbitrage with inverse rates
-- If rate A->B exists and rate B->A exists for the same effective_at,
-- then A->B * B->A should equal 1 (within tolerance).
-- Inconsistent rates could be used to manipulate values.
CREATE OR REPLACE FUNCTION check_exchange_rate_arbitrage()
RETURNS TRIGGER AS $$
DECLARE
    inverse_rate NUMERIC(38, 18);
    expected_inverse NUMERIC(38, 18);
    tolerance NUMERIC(38, 18) := 0.0001;  -- 0.01% tolerance for floating point
BEGIN
    -- Skip arbitrage check for invalid rates (they'll be caught by validate_exchange_rate_value)
    -- This prevents division by zero errors when triggers run in alphabetical order
    IF NEW.rate IS NULL OR NEW.rate <= 0 THEN
        RETURN NEW;
    END IF;

    -- Calculate expected inverse
    expected_inverse := 1.0 / NEW.rate;

    -- Look for an existing inverse rate (same effective_at, reverse currencies)
    SELECT rate INTO inverse_rate
    FROM exchange_rates
    WHERE from_currency = NEW.to_currency
    AND to_currency = NEW.from_currency
    AND effective_at = NEW.effective_at
    AND id != NEW.id
    LIMIT 1;

    -- If inverse exists, check for arbitrage
    IF inverse_rate IS NOT NULL THEN
        -- Check if rate * inverse_rate is approximately 1
        -- Allows for small floating point tolerance
        IF ABS((NEW.rate * inverse_rate) - 1.0) > tolerance THEN
            RAISE EXCEPTION 'EXCHANGE_RATE_ARBITRAGE: Rate % for %/% with inverse % for %/% creates arbitrage opportunity. Product is % (should be ~1.0). This inconsistency could hide value manipulation.',
                NEW.rate, NEW.from_currency, NEW.to_currency,
                inverse_rate, NEW.to_currency, NEW.from_currency,
                (NEW.rate * inverse_rate)
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_exchange_rate_validate ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_immutability ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_delete ON exchange_rates;
DROP TRIGGER IF EXISTS trg_exchange_rate_arbitrage ON exchange_rates;

-- Create triggers
CREATE TRIGGER trg_exchange_rate_validate
    BEFORE INSERT OR UPDATE ON exchange_rates
    FOR EACH ROW
    EXECUTE FUNCTION validate_exchange_rate_value();

CREATE TRIGGER trg_exchange_rate_immutability
    BEFORE UPDATE ON exchange_rates
    FOR EACH ROW
    EXECUTE FUNCTION prevent_referenced_exchange_rate_modification();

CREATE TRIGGER trg_exchange_rate_delete
    BEFORE DELETE ON exchange_rates
    FOR EACH ROW
    EXECUTE FUNCTION prevent_referenced_exchange_rate_deletion();

CREATE TRIGGER trg_exchange_rate_arbitrage
    BEFORE INSERT OR UPDATE ON exchange_rates
    FOR EACH ROW
    EXECUTE FUNCTION check_exchange_rate_arbitrage();

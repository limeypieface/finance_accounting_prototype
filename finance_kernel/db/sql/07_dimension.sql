-- =============================================================================
-- Dimension Immutability Triggers
-- =============================================================================
-- R10 Compliance: Dimension reference data is immutable once in use.
--
-- Invariants enforced:
--   1. Dimension.code cannot change when dimension values exist
--   2. Dimension cannot be deleted when dimension values exist
--   3. DimensionValue structural fields (code, name, dimension_code) are always immutable
--   4. DimensionValue cannot be deleted when referenced by posted journal lines
--
-- Dimensions provide the analytical breakdown of transactions (cost centers,
-- projects, etc.). Changing them after use would corrupt historical reporting.
-- =============================================================================

-- Function: Prevent modification of dimension code when values exist
-- Changing the dimension code would orphan all associated dimension values
CREATE OR REPLACE FUNCTION prevent_dimension_code_modification()
RETURNS TRIGGER AS $$
DECLARE
    value_count INTEGER;
BEGIN
    -- Only check if code is changing
    IF OLD.code = NEW.code THEN
        RETURN NEW;
    END IF;

    -- Check if this dimension has any values
    SELECT COUNT(*) INTO value_count
    FROM dimension_values
    WHERE dimension_code = OLD.code;

    IF value_count > 0 THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify dimension code % - it has % dimension value(s). Changing codes would break references.',
            OLD.code, value_count
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of dimension when values exist
CREATE OR REPLACE FUNCTION prevent_dimension_deletion_with_values()
RETURNS TRIGGER AS $$
DECLARE
    value_count INTEGER;
BEGIN
    -- Check if this dimension has any values
    SELECT COUNT(*) INTO value_count
    FROM dimension_values
    WHERE dimension_code = OLD.code;

    IF value_count > 0 THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete dimension % - it has % dimension value(s). Delete the values first.',
            OLD.code, value_count
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent modification of dimension value structural fields
-- These fields define the identity of the dimension value and must remain stable
CREATE OR REPLACE FUNCTION prevent_dimension_value_structural_modification()
RETURNS TRIGGER AS $$
BEGIN
    -- Check if any structural field changed
    IF OLD.code != NEW.code THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify dimension value code % to % - codes are immutable.',
            OLD.code, NEW.code
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.name != NEW.name THEN
        RAISE EXCEPTION 'R10 Violation: Cannot modify dimension value name % to % - names are immutable for audit trail.',
            OLD.name, NEW.name
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.dimension_code != NEW.dimension_code THEN
        RAISE EXCEPTION 'R10 Violation: Cannot move dimension value % from dimension % to % - dimension assignment is immutable.',
            OLD.code, OLD.dimension_code, NEW.dimension_code
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Function: Prevent deletion of dimension values referenced by posted journal lines
-- Journal lines store dimensions as JSONB, so we check if the value appears
CREATE OR REPLACE FUNCTION prevent_referenced_dimension_value_deletion()
RETURNS TRIGGER AS $$
DECLARE
    reference_count INTEGER;
BEGIN
    -- Check if this dimension value is referenced by any posted journal lines
    SELECT COUNT(*) INTO reference_count
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_entry_id = je.id
    WHERE je.status = 'posted'
    AND jl.dimensions->OLD.dimension_code = to_jsonb(OLD.code::text);

    IF reference_count > 0 THEN
        RAISE EXCEPTION 'R10 Violation: Cannot delete dimension value %:% - it is referenced by % posted journal line(s).',
            OLD.dimension_code, OLD.code, reference_count
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- Drop existing triggers (idempotent installation)
DROP TRIGGER IF EXISTS trg_dimension_code_immutability ON dimensions;
DROP TRIGGER IF EXISTS trg_dimension_deletion_protection ON dimensions;
DROP TRIGGER IF EXISTS trg_dimension_value_structural_immutability ON dimension_values;
DROP TRIGGER IF EXISTS trg_dimension_value_deletion_protection ON dimension_values;

-- Create dimension triggers
CREATE TRIGGER trg_dimension_code_immutability
    BEFORE UPDATE ON dimensions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_dimension_code_modification();

CREATE TRIGGER trg_dimension_deletion_protection
    BEFORE DELETE ON dimensions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_dimension_deletion_with_values();

CREATE TRIGGER trg_dimension_value_structural_immutability
    BEFORE UPDATE ON dimension_values
    FOR EACH ROW
    EXECUTE FUNCTION prevent_dimension_value_structural_modification();

CREATE TRIGGER trg_dimension_value_deletion_protection
    BEFORE DELETE ON dimension_values
    FOR EACH ROW
    EXECUTE FUNCTION prevent_referenced_dimension_value_deletion();

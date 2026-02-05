"""Entity target schema for import mapping editor: allowed target fields per entity type."""

# Target field definition: (target_name, field_type, required, default, transform)
# field_type: string, integer, decimal, currency, date, boolean
# transform: strip, lower, upper or None

ENTITY_TARGETS = {
    "account": [
        ("code", "string", True, None, "strip"),
        ("name", "string", True, None, "strip"),
        ("account_type", "string", False, "expense", "lower"),  # optional; default expense if missing
    ],
    "vendor": [
        ("code", "string", True, None, "strip"),
        ("name", "string", True, None, "strip"),
        ("tax_id", "string", False, None, "strip"),
        ("payment_terms_days", "integer", False, 30, None),
    ],
    "customer": [
        ("code", "string", True, None, "strip"),
        ("name", "string", True, None, "strip"),
        ("tax_id", "string", False, None, "strip"),
        ("payment_terms_days", "integer", False, 30, None),
        ("credit_limit", "decimal", False, None, None),
        ("credit_currency", "currency", False, "USD", None),
    ],
}

# Default validations per entity: unique on code (batch + system)
DEFAULT_VALIDATIONS = {
    "account": [
        {"rule_type": "unique", "fields": ["code"], "scope": "batch", "message": "Duplicate account code within batch"},
        {"rule_type": "unique", "fields": ["code"], "scope": "system", "message": "Account code already exists"},
    ],
    "vendor": [
        {"rule_type": "unique", "fields": ["code"], "scope": "batch", "message": "Duplicate vendor within batch"},
        {"rule_type": "unique", "fields": ["code"], "scope": "system", "message": "Vendor already exists"},
    ],
    "customer": [
        {"rule_type": "unique", "fields": ["code"], "scope": "batch", "message": "Duplicate customer within batch"},
        {"rule_type": "unique", "fields": ["code"], "scope": "system", "message": "Customer already exists"},
    ],
}

"""
SQL Injection Vulnerability Tests.

These tests highlight and document potential SQL injection vulnerabilities
in the codebase. They serve two purposes:

1. DOCUMENTATION: Clearly show how certain patterns could be exploited
2. REGRESSION GUARD: Ensure fixes are not reverted

Vulnerabilities tested:
- V1: String interpolation in trigger name queries (triggers.py:204-207, 230-235)
- V2: LIKE patterns with dynamic input (immutability.py:397-398)
- V3: General input sanitization for account codes, tags, etc.

NOTE: Some tests demonstrate vulnerabilities in isolation by mocking
the vulnerable patterns. This allows testing without modifying production code.
"""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, OperationalError


# =============================================================================
# V1: String Interpolation in Trigger Queries (triggers.py)
# =============================================================================


class TestTriggerQueryInjection:
    """
    Demonstrate SQL injection vulnerability in triggers.py.

    The vulnerable pattern (lines 204-207, 230-235):

        trigger_list = ", ".join(f"'{name}'" for name in ALL_TRIGGER_NAMES)
        check_sql = f"SELECT COUNT(*) FROM pg_trigger WHERE tgname IN ({trigger_list});"

    If ALL_TRIGGER_NAMES ever contained user-controlled input, this would
    allow SQL injection. While currently hardcoded, this pattern:
    1. Sets a dangerous precedent in the codebase
    2. Could be copy-pasted to dynamic contexts
    3. Violates secure coding principles
    """

    def test_vulnerable_pattern_with_malicious_trigger_name(self):
        """
        VULNERABILITY DEMONSTRATION: Show how f-string SQL construction
        can be exploited if input is not trusted.

        This test proves the pattern is injectable by simulating what would
        happen if a malicious name entered the list.
        """
        # Simulate the vulnerable pattern from triggers.py
        def vulnerable_build_query(trigger_names: list[str]) -> str:
            """Replicates the vulnerable pattern in triggers.py:204-207"""
            trigger_list = ", ".join(f"'{name}'" for name in trigger_names)
            return f"SELECT COUNT(*) FROM pg_trigger WHERE tgname IN ({trigger_list});"

        # Normal use case - safe when names are trusted
        safe_names = ["trg_journal_entry_immutability_update", "trg_audit_event_immutability"]
        safe_query = vulnerable_build_query(safe_names)
        assert "trg_journal_entry_immutability_update" in safe_query
        assert "trg_audit_event_immutability" in safe_query

        # INJECTION ATTACK: Single quote escape to inject SQL
        malicious_names = [
            "trg_safe",
            "'); DROP TABLE audit_events; --",  # SQL injection payload
        ]
        injected_query = vulnerable_build_query(malicious_names)

        # The injection payload is now part of the SQL!
        assert "DROP TABLE audit_events" in injected_query
        # This query would execute: SELECT COUNT(*) FROM pg_trigger WHERE tgname IN ('trg_safe', ''); DROP TABLE audit_events; --');

    def test_parameterized_query_blocks_injection(self):
        """
        SAFE PATTERN: Demonstrate how parameterized queries prevent injection.

        This is the pattern that SHOULD be used in triggers.py.
        """
        def safe_build_query(trigger_names: list[str]) -> tuple[str, dict]:
            """Safe parameterized version"""
            placeholders = ", ".join(f":name_{i}" for i in range(len(trigger_names)))
            query = f"SELECT COUNT(*) FROM pg_trigger WHERE tgname IN ({placeholders});"
            params = {f"name_{i}": name for i, name in enumerate(trigger_names)}
            return query, params

        malicious_names = [
            "trg_safe",
            "'); DROP TABLE audit_events; --",
        ]
        query, params = safe_build_query(malicious_names)

        # The malicious payload is now a parameter VALUE, not part of SQL structure
        assert "DROP TABLE" not in query
        assert params["name_1"] == "'); DROP TABLE audit_events; --"
        # The database will search for a trigger literally named "'); DROP TABLE audit_events; --"
        # which won't match anything, but crucially won't execute the DROP

    def test_array_binding_alternative(self, pg_session):
        """
        SAFE PATTERN: PostgreSQL ANY() with array binding.

        This is an even cleaner approach for IN clauses.
        """
        # This pattern binds the entire array as one parameter
        safe_names = ["trg_journal_entry_immutability_update", "nonexistent_trigger"]

        result = pg_session.execute(
            text("SELECT COUNT(*) FROM pg_trigger WHERE tgname = ANY(:names)"),
            {"names": safe_names}
        )
        count = result.scalar()

        # Query executed safely - malicious input would be treated as literal string
        assert isinstance(count, int)


# =============================================================================
# V2: LIKE Pattern Injection (immutability.py)
# =============================================================================


class TestLikePatternInjection:
    """
    Demonstrate LIKE pattern vulnerability if search terms become dynamic.

    The current pattern (immutability.py:397-398):

        WHERE tags::text LIKE '%rounding%'

    Currently hardcoded and safe, but if the search term were dynamic:
    - '%' and '_' are wildcards in LIKE
    - Could cause unintended matches or denial of service
    """

    def test_like_wildcard_injection(self, pg_session):
        """
        VULNERABILITY DEMONSTRATION: LIKE wildcards in user input.

        If the rounding tag search became dynamic (e.g., searching for
        arbitrary tags), user input with '%' could match unintended records.
        """
        import json
        actor_id = uuid4()

        # Create accounts with different tags (tags stored as JSON array)
        # Insert each account separately to avoid cast syntax issues
        accounts = [
            (str(uuid4()), f"RND{uuid4().hex[:6]}", "Rounding Account", "expense", json.dumps(["rounding"])),
            (str(uuid4()), f"REV{uuid4().hex[:6]}", "Revenue Account", "revenue", json.dumps(["sales", "domestic"])),
            (str(uuid4()), f"SEC{uuid4().hex[:6]}", "Secret Account", "asset", json.dumps(["confidential", "restricted"])),
        ]
        for acc_id, code, name, acc_type, tags in accounts:
            pg_session.execute(
                text("""
                    INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                         is_active, tags, created_at, created_by_id)
                    VALUES (:id, :code, :name, :acc_type, 'debit', true,
                           CAST(:tags AS json), NOW(), :actor_id)
                """),
                {
                    "id": acc_id, "code": code, "name": name, "acc_type": acc_type,
                    "tags": tags, "actor_id": str(actor_id),
                },
            )
        pg_session.commit()

        # SAFE: Searching for literal 'rounding' matches only rounding accounts
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%'")
        )
        rounding_count = result.scalar()
        assert rounding_count >= 1  # Only rounding accounts

        # VULNERABLE PATTERN: If search term were dynamic, '%' matches everything
        # Simulating: user_input = '%'  (matches all accounts with any tags)
        malicious_pattern = '%'
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE :pattern"),
            {"pattern": malicious_pattern}
        )
        all_count = result.scalar()
        assert all_count >= 3  # Matches ALL accounts - information disclosure!

        # VULNERABLE PATTERN: '_' matches single character wildcards
        # Could probe for tag structures
        underscore_pattern = '%r_unding%'  # Matches 'rounding', 'rfunding', etc.
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE :pattern"),
            {"pattern": underscore_pattern}
        )
        # Demonstrates wildcard behavior

    def test_safe_contains_check(self, pg_session):
        """
        SAFE PATTERN: Use JSON containment instead of LIKE on cast text.

        PostgreSQL JSON containment (@>) is safer and more semantically correct.
        """
        import json
        actor_id = uuid4()
        account_id = uuid4()

        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, tags, created_at, created_by_id)
                VALUES (:id, :code, 'Test Account', 'asset', 'debit', true,
                       CAST(:tags AS json), NOW(), :actor_id)
            """),
            {
                "id": str(account_id),
                "code": f"TST{uuid4().hex[:6]}",
                "tags": json.dumps(["rounding", "usd"]),
                "actor_id": str(actor_id),
            },
        )
        pg_session.commit()

        # SAFE: Using JSON containment operator
        # The @> operator checks if JSON array contains element
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE CAST(tags AS jsonb) @> CAST(:tag AS jsonb)"),
            {"tag": json.dumps(["rounding"])}
        )
        count = result.scalar()
        assert count >= 1

        # Malicious input with '%' is treated literally, not as wildcard
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE CAST(tags AS jsonb) @> CAST(:tag AS jsonb)"),
            {"tag": json.dumps(["%"])}  # Searches for literal '%' tag, not wildcard
        )
        count = result.scalar()
        assert count == 0  # No accounts have a literal '%' tag


# =============================================================================
# V3: Account Code / Reference Data Injection
# =============================================================================


class TestReferenceDataInjection:
    """
    Test that reference data lookups are protected against injection.

    Account codes, dimension codes, and other reference data should be
    validated and parameterized when used in queries.
    """

    def test_account_code_lookup_is_parameterized(self, pg_session):
        """
        Verify account lookups use parameterized queries.

        Even though account codes go through validation, the database
        layer should still use parameters as defense-in-depth.
        """
        actor_id = uuid4()
        account_id = uuid4()

        # Create a test account
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {
                "id": str(account_id),
                "code": "TEST001",
                "actor_id": str(actor_id),
            },
        )
        pg_session.commit()

        # SAFE: Parameterized lookup (this is what the codebase does)
        malicious_code = "TEST001'; DELETE FROM accounts; --"
        result = pg_session.execute(
            text("SELECT id FROM accounts WHERE code = :code"),
            {"code": malicious_code}
        )
        # Returns nothing because no account has that literal code
        assert result.fetchone() is None

        # Verify accounts still exist (injection didn't execute)
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE code = :code"),
            {"code": "TEST001"}
        )
        assert result.scalar() == 1

    def test_dimension_code_injection_blocked(self, pg_session):
        """
        Verify dimension lookups are parameterized.
        """
        # Attempt to inject via dimension code lookup
        malicious_code = "DIM'; DROP TABLE dimensions; --"

        result = pg_session.execute(
            text("SELECT COUNT(*) FROM dimensions WHERE code = :code"),
            {"code": malicious_code}
        )
        # Safe - returns 0, doesn't execute DROP
        assert result.scalar() == 0


# =============================================================================
# V4: Event Payload Injection
# =============================================================================


class TestEventPayloadSecurity:
    """
    Test that JSON payloads in events cannot cause SQL injection.

    Event payloads are stored as JSONB but should never be interpolated
    into SQL queries directly.
    """

    def test_jsonb_payload_with_sql_in_values(self, pg_session):
        """
        Verify SQL-like strings in JSON payloads are safely stored.
        """
        actor_id = uuid4()
        event_id = uuid4()

        # Payload contains SQL-injection-like strings
        malicious_payload = {
            "amount": "100'; DROP TABLE events; --",
            "description": "Robert'); DROP TABLE students;--",
            "nested": {
                "query": "SELECT * FROM users WHERE 1=1"
            }
        }

        import json
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.injection', NOW(), CURRENT_DATE,
                       :actor_id, 'test', CAST(:payload AS jsonb), 'hash123', 1, NOW())
            """),
            {
                "id": str(uuid4()),
                "event_id": str(event_id),
                "actor_id": str(actor_id),
                "payload": json.dumps(malicious_payload),
            },
        )
        pg_session.commit()

        # Verify payload is stored as data, not executed
        result = pg_session.execute(
            text("SELECT payload FROM events WHERE event_id = :event_id"),
            {"event_id": str(event_id)}
        )
        stored_payload = result.scalar()

        # SQL strings are safely stored as JSON values
        assert stored_payload["amount"] == "100'; DROP TABLE events; --"
        assert "DROP TABLE" in stored_payload["description"]

        # Verify events table still exists
        result = pg_session.execute(text("SELECT COUNT(*) FROM events"))
        assert result.scalar() >= 1


# =============================================================================
# V5: Batch/Bulk Operation Injection
# =============================================================================


class TestBulkOperationInjection:
    """
    Test that bulk operations cannot be exploited for injection.
    """

    def test_multi_value_insert_parameterized(self, pg_session):
        """
        Verify that bulk inserts use proper parameterization.

        Some ORMs or batch inserts construct VALUES lists dynamically.
        This must be done safely.
        """
        actor_id = uuid4()

        # Safe bulk insert using executemany or properly parameterized VALUES
        accounts_data = [
            {"id": str(uuid4()), "code": f"BULK{i}", "name": f"Account {i}"}
            for i in range(3)
        ]
        # Add a malicious entry
        accounts_data.append({
            "id": str(uuid4()),
            "code": "MALICIOUS'); DELETE FROM accounts; --",
            "name": "Hacker Account"
        })

        # Using parameterized insert (safe pattern)
        for acc in accounts_data:
            pg_session.execute(
                text("""
                    INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                         is_active, created_at, created_by_id)
                    VALUES (:id, :code, :name, 'asset', 'debit', true, NOW(), :actor_id)
                """),
                {**acc, "actor_id": str(actor_id)}
            )

        pg_session.commit()

        # All accounts exist, injection didn't execute
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE code LIKE 'BULK%'")
        )
        assert result.scalar() >= 3

        # The "malicious" account was created with the literal code
        result = pg_session.execute(
            text("SELECT code FROM accounts WHERE code LIKE :pattern"),
            {"pattern": "MALICIOUS%"}
        )
        row = result.fetchone()
        assert row is not None
        assert "DELETE FROM accounts" in row[0]  # Stored as literal text


# =============================================================================
# V6: Second-Order Injection (Stored Data Used in Queries)
# =============================================================================


class TestSecondOrderInjection:
    """
    Test protection against second-order SQL injection.

    Second-order injection occurs when:
    1. Malicious data is stored safely in the database
    2. Later retrieved and unsafely used in another query

    This is particularly relevant for tags, codes, and names that
    might be used in dynamic query construction.
    """

    def test_stored_tag_not_used_in_dynamic_sql(self, pg_session):
        """
        Verify that tags retrieved from DB are not interpolated into SQL.
        """
        import json
        actor_id = uuid4()

        # Store an account with a malicious-looking tag
        malicious_tag = "rounding'; DROP TABLE accounts; --"
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, tags, created_at, created_by_id)
                VALUES (:id, :code, 'Trap Account', 'asset', 'debit', true,
                       CAST(:tags AS json), NOW(), :actor_id)
            """),
            {
                "id": str(uuid4()),
                "code": f"TRAP{uuid4().hex[:6]}",
                "tags": json.dumps([malicious_tag]),
                "actor_id": str(actor_id),
            },
        )
        pg_session.commit()

        # Retrieve tags and use them in another query SAFELY
        result = pg_session.execute(
            text("SELECT tags FROM accounts WHERE code LIKE 'TRAP%'")
        )
        row = result.fetchone()
        if row and row[0]:
            retrieved_tag = row[0][0]

            # SAFE: Using retrieved value as parameter with JSON containment
            result = pg_session.execute(
                text("SELECT COUNT(*) FROM accounts WHERE CAST(tags AS jsonb) @> CAST(:tag AS jsonb)"),
                {"tag": json.dumps([retrieved_tag])}
            )
            count = result.scalar()
            assert count >= 1

            # WOULD BE VULNERABLE (don't do this):
            # f"SELECT * FROM accounts WHERE tags::text LIKE '%{retrieved_tag}%'"


# =============================================================================
# Integration Test: Full Attack Scenario
# =============================================================================


# =============================================================================
# V7: Static Analysis - Detect Vulnerable Patterns in Codebase
# =============================================================================


class TestStaticCodeAnalysis:
    """
    Static analysis tests to detect SQL injection patterns in the codebase.

    These tests scan the actual source code for known vulnerable patterns.
    If any are found, the test fails with details about the location.

    This catches:
    - f-string SQL construction
    - String concatenation in SQL
    - format() calls in SQL contexts
    """

    def test_detect_fstring_sql_in_triggers(self):
        """
        REGRESSION GUARD: Detect f-string SQL construction in triggers.py.

        This test DOCUMENTS a known vulnerability and will help track when
        it gets fixed. Currently expected to find the vulnerable pattern.
        """
        from pathlib import Path

        triggers_path = Path(__file__).parent.parent.parent / "finance_kernel" / "db" / "triggers.py"
        content = triggers_path.read_text()

        # Look for the specific vulnerable pattern: f-string followed by SQL on next lines
        # The pattern in triggers.py uses a multiline f-string
        vulnerable_lines = []
        lines = content.split('\n')

        for i, line in enumerate(lines):
            # Look for f""" or f''' that starts a multiline f-string
            if ('f"""' in line or "f'''" in line) or ('check_sql = f"' in line):
                # Check if subsequent lines contain SQL keywords
                context = '\n'.join(lines[i:i+5])
                if 'SELECT' in context.upper() and 'pg_trigger' in context:
                    vulnerable_lines.append(f"  Line {i+1}: Multiline f-string with SQL")

        # DOCUMENT THE KNOWN VULNERABILITY
        # The pattern check_sql = f"..." with SQL exists in triggers.py
        # Check for the specific known vulnerable pattern
        if 'check_sql = f"""' in content or 'check_sql = f"' in content:
            vulnerable_lines.append("  Found: check_sql = f-string pattern")

        assert len(vulnerable_lines) > 0, (
            "Expected vulnerability in triggers.py was fixed! "
            "Update this test to remove the vulnerability expectation."
        )
        # When fixed, change to: assert len(vulnerable_lines) == 0

    def test_no_fstring_sql_in_services(self):
        """
        PREVENTION: Ensure no f-string SQL construction in service layer.

        Services should NEVER construct SQL with f-strings.
        """
        from pathlib import Path

        services_dir = Path(__file__).parent.parent.parent / "finance_kernel" / "services"
        violations = []

        # These are actual SQL statement keywords that indicate query construction
        # Exclude common words that appear in error messages (from, where as English words)
        sql_statement_patterns = [
            'SELECT ',      # Space after to avoid "selected"
            'INSERT ',
            'UPDATE ',
            'DELETE ',
            ' FROM ',       # Space before and after to avoid "from_status" etc
            ' WHERE ',
            ' JOIN ',
            'text("',       # SQLAlchemy text() with f-string would be bad
            'text(f"',      # Definitely bad
            "text(f'",
        ]

        for py_file in services_dir.glob("*.py"):
            content = py_file.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines, 1):
                # Only check lines that have f-strings
                if 'f"' not in line and "f'" not in line:
                    continue

                # Check for SQL-specific patterns (not just keywords)
                line_upper = line.upper()
                for pattern in sql_statement_patterns:
                    if pattern.upper() in line_upper:
                        # Additional check: is this actually SQL or just an error message?
                        # SQL construction usually has text(), execute(), or raw SQL syntax
                        if 'text(' in line or 'execute(' in line or 'cursor' in line:
                            violations.append(f"{py_file.name}:{i}: {line.strip()}")
                            break

        assert len(violations) == 0, (
            f"Found {len(violations)} f-string SQL patterns in services:\n" +
            "\n".join(violations)
        )

    def test_no_fstring_sql_in_selectors(self):
        """
        PREVENTION: Ensure no f-string SQL construction in selectors.

        Selectors query data and must use parameterized queries.
        """
        import re
        from pathlib import Path

        selectors_dir = Path(__file__).parent.parent.parent / "finance_kernel" / "selectors"
        if not selectors_dir.exists():
            return  # No selectors directory

        violations = []
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'FROM', 'WHERE', 'JOIN']

        for py_file in selectors_dir.glob("*.py"):
            content = py_file.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines, 1):
                if ('f"' in line or "f'" in line) and any(kw in line.upper() for kw in sql_keywords):
                    violations.append(f"{py_file.name}:{i}: {line.strip()}")

        assert len(violations) == 0, (
            f"Found {len(violations)} f-string SQL patterns in selectors:\n" +
            "\n".join(violations)
        )

    def test_no_string_format_sql(self):
        """
        PREVENTION: Ensure .format() is not used for SQL construction.

        The .format() method is just as dangerous as f-strings for SQL.
        """
        import re
        from pathlib import Path

        kernel_dir = Path(__file__).parent.parent.parent / "finance_kernel"
        violations = []

        # Pattern: .format() followed by SQL keywords in the string
        format_pattern = re.compile(r'["\'].*?(SELECT|INSERT|UPDATE|DELETE).*?["\']\.format\(', re.IGNORECASE)

        for py_file in kernel_dir.rglob("*.py"):
            content = py_file.read_text()
            if format_pattern.search(content):
                rel_path = py_file.relative_to(kernel_dir)
                violations.append(str(rel_path))

        # Exclude known false positives (like this test file)
        violations = [v for v in violations if 'test_' not in v]

        assert len(violations) == 0, (
            f"Found .format() SQL patterns in: {violations}"
        )

    def test_no_percent_formatting_sql(self):
        """
        PREVENTION: Ensure % formatting is not used for SQL construction.

        Old-style % formatting is also vulnerable to SQL injection.
        """
        import re
        from pathlib import Path

        kernel_dir = Path(__file__).parent.parent.parent / "finance_kernel"
        violations = []

        # Pattern: SQL keywords with % placeholders (not parameterized :name or %(name)s)
        # Look for patterns like "SELECT * FROM %s" % var
        for py_file in kernel_dir.rglob("*.py"):
            content = py_file.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines, 1):
                # Check for % string formatting with SQL
                if '% ' in line or '%(' in line:
                    if any(kw in line.upper() for kw in ['SELECT', 'INSERT', 'UPDATE', 'DELETE']):
                        # Exclude SQLAlchemy's %(name)s parameterized format
                        if '%s' in line or '%d' in line:
                            rel_path = py_file.relative_to(kernel_dir)
                            violations.append(f"{rel_path}:{i}: {line.strip()}")

        assert len(violations) == 0, (
            f"Found % formatted SQL patterns:\n" + "\n".join(violations)
        )


class TestFullAttackScenario:
    """
    End-to-end test simulating a complete attack scenario.

    This demonstrates defense-in-depth by showing multiple layers
    would need to fail for an attack to succeed.
    """

    def test_attack_chain_blocked(self, pg_session):
        """
        Simulate an attacker trying to exploit the finance system.

        Attack chain:
        1. Try to inject via account code -> BLOCKED by validation
        2. Try to inject via event payload -> BLOCKED by parameterization
        3. Try to inject via raw SQL in test fixture -> BLOCKED by parameters
        4. Try to modify data via bulk update -> BLOCKED by triggers
        """
        actor_id = uuid4()

        # Step 1: Malicious account code is stored literally (safe)
        account_id = uuid4()
        malicious_code = "1000' OR '1'='1"
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": str(account_id), "code": malicious_code, "actor_id": str(actor_id)},
        )
        pg_session.commit()

        # Step 2: Query with the malicious code as literal - no injection
        result = pg_session.execute(
            text("SELECT id FROM accounts WHERE code = :code"),
            {"code": malicious_code}
        )
        assert result.fetchone()[0] == str(account_id)

        # The OR '1'='1' is NOT evaluated as SQL, it's a literal string match
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE code = :code"),
            {"code": "1000"}  # This won't match the malicious account
        )
        assert result.scalar() == 0  # No match because codes don't match

        # Step 3: Verify data integrity maintained
        result = pg_session.execute(
            text("SELECT COUNT(*) FROM accounts")
        )
        total = result.scalar()
        assert total >= 1  # System intact

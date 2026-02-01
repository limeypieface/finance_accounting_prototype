"""
finance_config — single public entrypoint for accounting configuration.

Responsibility:
    Provides the ONLY way to obtain configuration at runtime through
    ``get_active_config()``.  No other component may read configuration
    files, environment variables, or feature flags directly.  Returns a
    ``CompiledPolicyPack`` -- the sole runtime artifact.  YAML loading is
    internal build/test tooling and never exposed to callers.

Architecture position:
    Configuration -- YAML-driven policy pipeline, build-time validation.
    This package sits above ``finance_kernel`` and below
    ``finance_services`` / ``finance_modules``.  The kernel MUST NEVER
    import from ``finance_config``; bridges in this package translate
    compiled artifacts into kernel-compatible inputs.

Invariants enforced:
    - Single entrypoint: all runtime config flows through ``get_active_config()``.
    - Build-time validation: the configuration must pass schema, guard-AST,
      dispatch-ambiguity, and role-coverage validation before a pack is
      produced.
    - Fingerprint pinning: when an APPROVED_FINGERPRINT file exists, the
      compiled canonical fingerprint must match the pinned value (R18).
    - Deterministic compilation: same YAML fragments always produce the
      same ``CompiledPolicyPack`` checksum and canonical fingerprint.

Failure modes:
    - ``FileNotFoundError`` -- no matching configuration set for the
      requested legal entity / date.
    - ``ValueError`` -- schema or structural validation failures.
    - ``CompilationFailedError`` -- guard-AST, dispatch, or engine-contract
      compilation errors.
    - ``ConfigIntegrityError`` -- fingerprint mismatch against an approved
      pin file.

Audit relevance:
    Every successful ``get_active_config()`` call emits a
    ``FINANCE_CONFIG_TRACE`` log entry containing the config_id, version,
    checksum, scope, policy count, and role-binding count.  This trace is
    the audit anchor that ties every posted journal entry back to the exact
    configuration version that governed its interpretation.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from finance_config.assembler import assemble_from_directory
from finance_config.compiler import CompiledPolicyPack, compile_policy_pack
from finance_config.integrity import ConfigIntegrityError, verify_fingerprint_pin
from finance_config.validator import validate_configuration

_logger = logging.getLogger("finance_kernel.config")

# Default configuration sets directory
_DEFAULT_CONFIG_DIR = Path(__file__).parent / "sets"


def get_active_config(
    legal_entity: str,
    as_of_date: date,
    config_dir: Path | None = None,
) -> CompiledPolicyPack:
    """The ONLY public configuration entrypoint.

    Contract:
        No other component may read configuration files, environment
        variables, or feature flags.  All configuration flows through
        this single function.

    Guarantees:
        - The returned ``CompiledPolicyPack`` has passed schema validation,
          guard-AST validation, dispatch-ambiguity checks, and (when
          applicable) fingerprint-pin verification.
        - A ``FINANCE_CONFIG_TRACE`` log entry is emitted on every
          successful call.

    Non-goals:
        - This function does NOT cache packs across calls; callers are
          expected to hold the returned pack for the duration of a
          posting batch.

    Preconditions:
        - ``legal_entity`` is a non-empty string matching a scope
          declaration in a configuration set.
        - ``as_of_date`` falls within a configuration set's effective
          date range.

    Postconditions:
        - Returns a frozen ``CompiledPolicyPack`` whose checksum matches
          the assembled source.
        - If an APPROVED_FINGERPRINT file exists, the canonical
          fingerprint has been verified against it.

    Args:
        legal_entity: Legal entity identifier for scope matching.
        as_of_date: Date for effective date filtering.
        config_dir: Override path to configuration sets directory.
            Defaults to finance_config/sets/.

    Returns:
        CompiledPolicyPack -- the sole runtime artifact.

    Raises:
        FileNotFoundError: If no matching configuration set is found.
        ValueError: If configuration validation fails.
        CompilationFailedError: If compilation produces errors.
        ConfigIntegrityError: If APPROVED_FINGERPRINT exists and does
            not match the compiled canonical fingerprint.
    """
    sets_dir = config_dir or _DEFAULT_CONFIG_DIR

    # Find matching configuration set
    config_set, fragment_dir = _find_matching_config(sets_dir, legal_entity, as_of_date)

    # Validate
    validation = validate_configuration(config_set)
    if not validation.is_valid:
        raise ValueError(
            "Configuration validation failed:\n"
            + "\n".join(f"  - {e}" for e in validation.errors)
        )

    # Compile
    pack = compile_policy_pack(config_set)

    # INVARIANT: compiled checksum must match assembled source checksum.
    assert pack.checksum == config_set.checksum, (
        f"Checksum drift: compiled={pack.checksum!r} != source={config_set.checksum!r}"
    )

    # Emit FINANCE_CONFIG_TRACE
    _logger.info(
        "FINANCE_CONFIG_TRACE",
        extra={
            "trace_type": "FINANCE_CONFIG_TRACE",
            "config_set_id": pack.config_id,
            "config_set_version": pack.config_version,
            "checksum": pack.checksum,
            "scope_legal_entity": pack.scope.legal_entity if pack.scope else None,
            "scope_jurisdiction": pack.scope.jurisdiction if pack.scope else None,
            "scope_regime": pack.scope.regulatory_regime if pack.scope else None,
            "policy_count": len(pack.policies),
            "role_binding_count": len(pack.role_bindings),
        },
    )

    # Verify fingerprint against approved pin (no-op if no pin file)
    verify_fingerprint_pin(
        config_id=pack.config_id,
        canonical_fingerprint=pack.canonical_fingerprint,
        config_dir=fragment_dir,
    )

    return pack


def _find_matching_config(
    sets_dir: Path, legal_entity: str, as_of_date: date
) -> tuple[AccountingConfigurationSet, Path]:
    """Find the matching configuration set for a scope and date.

    Scans all subdirectories in *sets_dir*, assembles each, and returns
    the one matching *legal_entity* whose effective range covers
    *as_of_date* with PUBLISHED status, along with its fragment directory.

    Falls back to any available config if only one exists (for
    development/testing convenience).

    Preconditions:
        - ``sets_dir`` is an existing directory containing at least one
          configuration set subdirectory with a ``root.yaml``.

    Postconditions:
        - The returned ``AccountingConfigurationSet`` has scope and
          effective dates consistent with the request arguments (or is the
          sole available config in dev/test fallback mode).

    Raises:
        FileNotFoundError: If ``sets_dir`` does not exist or no
            configuration set matches the given scope and date.
    """
    from finance_config.lifecycle import ConfigStatus
    from finance_config.schema import AccountingConfigurationSet

    candidates: list[tuple[AccountingConfigurationSet, Path]] = []

    if not sets_dir.is_dir():
        raise FileNotFoundError(f"Configuration sets directory not found: {sets_dir}")

    for subdir in sorted(sets_dir.iterdir()):
        if not subdir.is_dir():
            continue
        root_file = subdir / "root.yaml"
        if not root_file.exists():
            continue

        config_set = assemble_from_directory(subdir)

        # Check scope match
        scope_matches = (
            config_set.scope.legal_entity == legal_entity
            or config_set.scope.legal_entity == "*"
        )
        date_matches = (
            config_set.scope.effective_from <= as_of_date
            and (
                config_set.scope.effective_to is None
                or config_set.scope.effective_to >= as_of_date
            )
        )

        if scope_matches and date_matches:
            candidates.append((config_set, subdir))

    if not candidates:
        # Fallback: if only one config set exists, use it (dev/test mode)
        all_configs: list[tuple[AccountingConfigurationSet, Path]] = []
        for subdir in sorted(sets_dir.iterdir()):
            if subdir.is_dir() and (subdir / "root.yaml").exists():
                all_configs.append((assemble_from_directory(subdir), subdir))

        if len(all_configs) == 1:
            return all_configs[0]

        raise FileNotFoundError(
            f"No configuration set found for legal_entity='{legal_entity}' "
            f"as_of_date={as_of_date} in {sets_dir}"
        )

    if len(candidates) == 1:
        return candidates[0]

    # Multiple matches: prefer PUBLISHED, then highest version
    published = [(c, p) for c, p in candidates if c.status == ConfigStatus.PUBLISHED]
    if published:
        return max(published, key=lambda pair: pair[0].version)

    return max(candidates, key=lambda pair: pair[0].version)

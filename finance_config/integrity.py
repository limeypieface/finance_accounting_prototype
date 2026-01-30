"""
Configuration Integrity — fingerprint pinning for approved configs.

When a config set directory contains an APPROVED_FINGERPRINT file,
the compiled canonical_fingerprint must match the pinned value.
This prevents unauthorized or accidental edits to approved configs.

The pin file is a single line: the SHA-256 hex string produced by
compile_policy_pack()'s canonical_fingerprint field.

If no APPROVED_FINGERPRINT file exists, the check is skipped
(draft/dev workflow).
"""

from __future__ import annotations

from pathlib import Path

PINFILE_NAME = "APPROVED_FINGERPRINT"


class ConfigIntegrityError(Exception):
    """Compiled config fingerprint does not match the approved pin.

    Raised when:
      - An APPROVED_FINGERPRINT file exists in the config set directory
      - The compiled canonical_fingerprint differs from the pinned value

    Attributes:
        config_id: The configuration set identifier.
        expected: The pinned (approved) fingerprint.
        actual: The computed canonical_fingerprint.
        pin_path: Path to the APPROVED_FINGERPRINT file.
    """

    code: str = "CONFIG_INTEGRITY_MISMATCH"

    def __init__(
        self,
        config_id: str,
        expected: str,
        actual: str,
        pin_path: Path,
    ):
        self.config_id = config_id
        self.expected = expected
        self.actual = actual
        self.pin_path = pin_path
        super().__init__(
            f"Config integrity check failed for '{config_id}': "
            f"pinned fingerprint {expected[:16]}... != "
            f"compiled fingerprint {actual[:16]}... "
            f"(pin file: {pin_path})"
        )


def read_pinned_fingerprint(config_dir: Path) -> str | None:
    """Read the APPROVED_FINGERPRINT file from a config set directory.

    Returns:
        The pinned SHA-256 hex string, or None if no pin file exists.
    """
    pin_path = config_dir / PINFILE_NAME
    if not pin_path.is_file():
        return None
    return pin_path.read_text().strip()


def verify_fingerprint_pin(
    config_id: str,
    canonical_fingerprint: str,
    config_dir: Path,
) -> None:
    """Verify that the compiled fingerprint matches the pin file.

    No-op if no APPROVED_FINGERPRINT file exists (draft/dev mode).

    Raises:
        ConfigIntegrityError: If pin exists and fingerprint does not match.
    """
    pinned = read_pinned_fingerprint(config_dir)
    if pinned is None:
        return

    if canonical_fingerprint != pinned:
        raise ConfigIntegrityError(
            config_id=config_id,
            expected=pinned,
            actual=canonical_fingerprint,
            pin_path=config_dir / PINFILE_NAME,
        )

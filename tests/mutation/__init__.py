"""Mutation kill-rate audit: apply one architectural seam mutation per run.

See tests/mutation/README.md for usage and mutations list.
"""

from tests.mutation.mutations import MUTATION_NAMES, apply_mutation

__all__ = ["MUTATION_NAMES", "apply_mutation"]

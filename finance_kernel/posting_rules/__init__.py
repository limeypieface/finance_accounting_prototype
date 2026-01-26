"""Posting rules for transforming events into journal lines."""

from finance_kernel.posting_rules.base import PostingRule, LineSpec
from finance_kernel.posting_rules.registry import PostingRuleRegistry

__all__ = [
    "PostingRule",
    "LineSpec",
    "PostingRuleRegistry",
]

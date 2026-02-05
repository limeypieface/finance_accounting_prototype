"""Entity promoters: stage -> live ORM (Phase 8)."""

from finance_ingestion.promoters.base import EntityPromoter, PromoteResult
from finance_ingestion.promoters.party import PartyPromoter
from finance_ingestion.promoters.account import AccountPromoter
from finance_ingestion.promoters.ap import VendorPromoter
from finance_ingestion.promoters.ar import CustomerPromoter
from finance_ingestion.promoters.inventory import ItemPromoter, LocationPromoter
from finance_ingestion.promoters.journal import JournalPromoter, OpeningBalancePromoter


def default_promoter_registry() -> dict[str, EntityPromoter]:
    """Return a dict of entity_type -> promoter for all implemented promoters."""
    return {
        "party": PartyPromoter(),
        "account": AccountPromoter(),
        "vendor": VendorPromoter(),
        "customer": CustomerPromoter(),
        "item": ItemPromoter(),
        "location": LocationPromoter(),
        "journal": JournalPromoter(),
        "opening_balance": OpeningBalancePromoter(),
    }


__all__ = [
    "EntityPromoter",
    "PromoteResult",
    "PartyPromoter",
    "AccountPromoter",
    "VendorPromoter",
    "CustomerPromoter",
    "ItemPromoter",
    "LocationPromoter",
    "JournalPromoter",
    "OpeningBalancePromoter",
    "default_promoter_registry",
]

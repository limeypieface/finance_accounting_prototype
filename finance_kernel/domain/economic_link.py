"""EconomicLink -- The 'Why' Pointer for Economic Ancestry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class ArtifactType(str, Enum):
    """Types of artifacts that can participate in economic links."""

    # Core kernel artifacts
    EVENT = "event"  # EconomicEvent or Event
    JOURNAL_ENTRY = "journal_entry"  # JournalEntry
    JOURNAL_LINE = "journal_line"  # JournalLine

    # Document artifacts (subledgers)
    PURCHASE_ORDER = "purchase_order"
    RECEIPT = "receipt"
    INVOICE = "invoice"
    PAYMENT = "payment"
    CREDIT_MEMO = "credit_memo"
    DEBIT_MEMO = "debit_memo"

    # Inventory artifacts
    COST_LOT = "cost_lot"  # ValuationLayer lot
    SHIPMENT = "shipment"
    INVENTORY_ADJUSTMENT = "inventory_adjustment"

    # Fixed asset artifacts
    ASSET = "asset"
    DEPRECIATION = "depreciation"
    DISPOSAL = "disposal"

    # Banking artifacts
    BANK_STATEMENT = "bank_statement"
    BANK_TRANSACTION = "bank_transaction"

    # Intercompany
    INTERCOMPANY_TRANSACTION = "intercompany_transaction"


class LinkType(str, Enum):
    """Semantic relationship types between artifacts."""

    # Fulfillment chain (PO → Receipt → Invoice)
    FULFILLED_BY = "fulfilled_by"  # Receipt fulfills PO, Invoice fulfills Receipt

    # Payment relationships
    PAID_BY = "paid_by"  # Invoice paid by Payment
    APPLIED_TO = "applied_to"  # Payment applied to Invoice (inverse view)

    # Correction/Reversal
    REVERSED_BY = "reversed_by"  # Entry reversed by ReversalEntry
    CORRECTED_BY = "corrected_by"  # Document corrected by new Document

    # Cost flow (Valuation)
    CONSUMED_BY = "consumed_by"  # Lot consumed by usage event
    SOURCED_FROM = "sourced_from"  # Cost sourced from acquisition

    # Allocation
    ALLOCATED_TO = "allocated_to"  # Amount allocated to target
    ALLOCATED_FROM = "allocated_from"  # Target received allocation from source

    # Derivation (general inheritance)
    DERIVED_FROM = "derived_from"  # Generic parent-child derivation

    # Matching
    MATCHED_WITH = "matched_with"  # Documents matched together (symmetric)

    # Adjustment
    ADJUSTED_BY = "adjusted_by"  # Original adjusted by adjustment


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Immutable reference to any economic artifact."""

    artifact_type: ArtifactType
    artifact_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_type, ArtifactType):
            raise ValueError(
                f"artifact_type must be ArtifactType, got {type(self.artifact_type)}"
            )

    def __str__(self) -> str:
        return f"{self.artifact_type.value}:{self.artifact_id}"

    @classmethod
    def parse(cls, ref_string: str) -> ArtifactRef:
        """Parse a string representation back to ArtifactRef."""
        try:
            type_str, id_str = ref_string.split(":", 1)
            return cls(
                artifact_type=ArtifactType(type_str),
                artifact_id=UUID(id_str),
            )
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid artifact ref string: {ref_string}") from e

    @classmethod
    def event(cls, event_id: UUID) -> ArtifactRef:
        """Create ref to an Event."""
        return cls(ArtifactType.EVENT, event_id)

    @classmethod
    def journal_entry(cls, entry_id: UUID) -> ArtifactRef:
        """Create ref to a JournalEntry."""
        return cls(ArtifactType.JOURNAL_ENTRY, entry_id)

    @classmethod
    def purchase_order(cls, po_id: UUID) -> ArtifactRef:
        """Create ref to a PurchaseOrder."""
        return cls(ArtifactType.PURCHASE_ORDER, po_id)

    @classmethod
    def receipt(cls, receipt_id: UUID) -> ArtifactRef:
        """Create ref to a Receipt."""
        return cls(ArtifactType.RECEIPT, receipt_id)

    @classmethod
    def invoice(cls, invoice_id: UUID) -> ArtifactRef:
        """Create ref to an Invoice."""
        return cls(ArtifactType.INVOICE, invoice_id)

    @classmethod
    def payment(cls, payment_id: UUID) -> ArtifactRef:
        """Create ref to a Payment."""
        return cls(ArtifactType.PAYMENT, payment_id)

    @classmethod
    def cost_lot(cls, lot_id: UUID) -> ArtifactRef:
        """Create ref to a CostLot."""
        return cls(ArtifactType.COST_LOT, lot_id)

    @classmethod
    def credit_memo(cls, memo_id: UUID) -> ArtifactRef:
        """Create ref to a CreditMemo."""
        return cls(ArtifactType.CREDIT_MEMO, memo_id)

    @classmethod
    def debit_memo(cls, memo_id: UUID) -> ArtifactRef:
        """Create ref to a DebitMemo."""
        return cls(ArtifactType.DEBIT_MEMO, memo_id)

    @classmethod
    def shipment(cls, shipment_id: UUID) -> ArtifactRef:
        """Create ref to a Shipment."""
        return cls(ArtifactType.SHIPMENT, shipment_id)


@dataclass(frozen=True, slots=True)
class LinkTypeSpec:
    """Specification of valid artifact types for a link type."""

    link_type: LinkType
    valid_parent_types: frozenset[ArtifactType]
    valid_child_types: frozenset[ArtifactType]
    is_symmetric: bool = False  # True for MATCHED_WITH
    max_children: int | None = None  # None = unlimited, 1 = one-to-one

    def validate(self, parent: ArtifactRef, child: ArtifactRef) -> list[str]:
        """Validate that parent/child types are valid for this link type."""
        errors: list[str] = []

        if parent.artifact_type not in self.valid_parent_types:
            errors.append(
                f"Parent type {parent.artifact_type.value} not valid for "
                f"{self.link_type.value}. Valid: {[t.value for t in self.valid_parent_types]}"
            )

        if child.artifact_type not in self.valid_child_types:
            errors.append(
                f"Child type {child.artifact_type.value} not valid for "
                f"{self.link_type.value}. Valid: {[t.value for t in self.valid_child_types]}"
            )

        return errors


# Link type specifications
LINK_TYPE_SPECS: Mapping[LinkType, LinkTypeSpec] = {
    LinkType.FULFILLED_BY: LinkTypeSpec(
        link_type=LinkType.FULFILLED_BY,
        valid_parent_types=frozenset({
            ArtifactType.PURCHASE_ORDER,
            ArtifactType.RECEIPT,
        }),
        valid_child_types=frozenset({
            ArtifactType.RECEIPT,
            ArtifactType.INVOICE,
        }),
    ),
    LinkType.PAID_BY: LinkTypeSpec(
        link_type=LinkType.PAID_BY,
        valid_parent_types=frozenset({
            ArtifactType.INVOICE,
            ArtifactType.CREDIT_MEMO,
            ArtifactType.DEBIT_MEMO,
        }),
        valid_child_types=frozenset({
            ArtifactType.PAYMENT,
        }),
    ),
    LinkType.REVERSED_BY: LinkTypeSpec(
        link_type=LinkType.REVERSED_BY,
        valid_parent_types=frozenset({
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.EVENT,
        }),
        valid_child_types=frozenset({
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.EVENT,
        }),
        max_children=1,  # An entry can only be reversed once
    ),
    LinkType.CORRECTED_BY: LinkTypeSpec(
        link_type=LinkType.CORRECTED_BY,
        valid_parent_types=frozenset({
            ArtifactType.INVOICE,
            ArtifactType.RECEIPT,
            ArtifactType.PAYMENT,
            ArtifactType.PURCHASE_ORDER,
            ArtifactType.JOURNAL_ENTRY,
        }),
        valid_child_types=frozenset({
            ArtifactType.INVOICE,
            ArtifactType.RECEIPT,
            ArtifactType.PAYMENT,
            ArtifactType.CREDIT_MEMO,
            ArtifactType.JOURNAL_ENTRY,
        }),
        max_children=1,
    ),
    LinkType.CONSUMED_BY: LinkTypeSpec(
        link_type=LinkType.CONSUMED_BY,
        valid_parent_types=frozenset({
            ArtifactType.COST_LOT,
        }),
        valid_child_types=frozenset({
            ArtifactType.EVENT,
            ArtifactType.SHIPMENT,
            ArtifactType.INVENTORY_ADJUSTMENT,
        }),
    ),
    LinkType.SOURCED_FROM: LinkTypeSpec(
        link_type=LinkType.SOURCED_FROM,
        valid_parent_types=frozenset({
            ArtifactType.EVENT,
            ArtifactType.RECEIPT,
        }),
        valid_child_types=frozenset({
            ArtifactType.COST_LOT,
        }),
    ),
    LinkType.ALLOCATED_TO: LinkTypeSpec(
        link_type=LinkType.ALLOCATED_TO,
        valid_parent_types=frozenset({
            ArtifactType.PAYMENT,
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.COST_LOT,
        }),
        valid_child_types=frozenset({
            ArtifactType.INVOICE,
            ArtifactType.JOURNAL_LINE,
            ArtifactType.EVENT,
        }),
    ),
    LinkType.DERIVED_FROM: LinkTypeSpec(
        link_type=LinkType.DERIVED_FROM,
        # Generic - allows any combination
        valid_parent_types=frozenset(ArtifactType),
        valid_child_types=frozenset(ArtifactType),
    ),
    LinkType.MATCHED_WITH: LinkTypeSpec(
        link_type=LinkType.MATCHED_WITH,
        valid_parent_types=frozenset({
            ArtifactType.PURCHASE_ORDER,
            ArtifactType.RECEIPT,
            ArtifactType.INVOICE,
            ArtifactType.BANK_STATEMENT,
            ArtifactType.BANK_TRANSACTION,
            ArtifactType.JOURNAL_ENTRY,
        }),
        valid_child_types=frozenset({
            ArtifactType.PURCHASE_ORDER,
            ArtifactType.RECEIPT,
            ArtifactType.INVOICE,
            ArtifactType.BANK_STATEMENT,
            ArtifactType.BANK_TRANSACTION,
            ArtifactType.JOURNAL_ENTRY,
        }),
        is_symmetric=True,
    ),
    LinkType.ADJUSTED_BY: LinkTypeSpec(
        link_type=LinkType.ADJUSTED_BY,
        valid_parent_types=frozenset({
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.COST_LOT,
            ArtifactType.ASSET,
        }),
        valid_child_types=frozenset({
            ArtifactType.JOURNAL_ENTRY,
            ArtifactType.INVENTORY_ADJUSTMENT,
        }),
    ),
}


@dataclass(frozen=True, slots=True)
class EconomicLink:
    """Immutable record of an economic relationship between artifacts (L1-L5)."""

    link_id: UUID
    link_type: LinkType
    parent_ref: ArtifactRef
    child_ref: ArtifactRef
    creating_event_id: UUID  # The event that established this link
    created_at: datetime
    metadata: Mapping[str, Any] | None = None  # Optional link-specific data

    def __post_init__(self) -> None:
        # INVARIANT: L2 -- no self-links (parent_ref != child_ref)
        if self.parent_ref == self.child_ref:
            raise ValueError(
                f"Self-link not allowed: parent and child are both {self.parent_ref}"
            )

        # INVARIANT: L5 -- parent/child types must be valid for this link_type
        spec = LINK_TYPE_SPECS.get(self.link_type)
        if spec:
            errors = spec.validate(self.parent_ref, self.child_ref)
            if errors:
                raise ValueError(
                    f"Invalid link type combination: {'; '.join(errors)}"
                )
        # INVARIANT: L4 -- creating_event_id must be present
        assert self.creating_event_id is not None, (
            "L4 violation: creating_event_id is required for all economic links"
        )

    @classmethod
    def create(
        cls,
        link_id: UUID,
        link_type: LinkType,
        parent_ref: ArtifactRef,
        child_ref: ArtifactRef,
        creating_event_id: UUID,
        created_at: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> EconomicLink:
        """Factory method to create a new EconomicLink."""
        return cls(
            link_id=link_id,
            link_type=link_type,
            parent_ref=parent_ref,
            child_ref=child_ref,
            creating_event_id=creating_event_id,
            created_at=created_at,
            metadata=metadata,
        )

    def is_reversal(self) -> bool:
        """True if this link represents a reversal relationship."""
        return self.link_type == LinkType.REVERSED_BY

    def is_payment(self) -> bool:
        """True if this link represents a payment relationship."""
        return self.link_type == LinkType.PAID_BY

    def is_fulfillment(self) -> bool:
        """True if this link represents a fulfillment relationship."""
        return self.link_type == LinkType.FULFILLED_BY

    def is_consumption(self) -> bool:
        """True if this link represents cost lot consumption."""
        return self.link_type == LinkType.CONSUMED_BY


@dataclass(frozen=True, slots=True)
class LinkQuery:
    """Query specification for traversing the link graph."""

    starting_ref: ArtifactRef
    link_types: frozenset[LinkType] | None = None  # None = all types
    direction: str = "children"  # "children", "parents", or "both"
    max_depth: int = 1  # 1 = direct links only, >1 = recursive
    include_metadata: bool = False


@dataclass(frozen=True, slots=True)
class LinkPath:
    """A path through the link graph."""

    artifacts: tuple[ArtifactRef, ...]
    links: tuple[EconomicLink, ...]

    @property
    def depth(self) -> int:
        return len(self.links)

    @property
    def start(self) -> ArtifactRef:
        return self.artifacts[0]

    @property
    def end(self) -> ArtifactRef:
        return self.artifacts[-1]

    def __post_init__(self) -> None:
        if len(self.artifacts) != len(self.links) + 1:
            raise ValueError(
                f"Invalid path: {len(self.artifacts)} artifacts requires "
                f"{len(self.artifacts) - 1} links, got {len(self.links)}"
            )

"""
Contract model for government contracts.

Contract represents agreements with customers, especially government contracts
subject to DCAA compliance requirements. Tracks:
- Contract identification (number, DUNS, CAGE)
- Contract type (cost-plus, FFP, T&M, etc.)
- Funding and ceiling amounts
- Period of performance
- DCAA compliance settings

Hard invariants:
- Contract numbers are immutable once costs are charged
- Funding cannot be reduced below incurred costs
- Ceiling amounts cannot be reduced below funded amounts
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString

if TYPE_CHECKING:
    from finance_kernel.models.party import Party


class ContractType(str, Enum):
    """
    FAR contract types for government contracting.

    Cost-reimbursement types (require DCAA timekeeping):
    - CPFF: Cost Plus Fixed Fee
    - CPIF: Cost Plus Incentive Fee
    - CPAF: Cost Plus Award Fee
    - T_AND_M: Time and Materials
    - LABOR_HOUR: Labor Hour

    Fixed-price types:
    - FFP: Firm Fixed Price
    - FPI: Fixed Price Incentive
    - FPAF: Fixed Price Award Fee
    """

    # Cost-reimbursement types
    COST_PLUS_FIXED_FEE = "CPFF"
    COST_PLUS_INCENTIVE_FEE = "CPIF"
    COST_PLUS_AWARD_FEE = "CPAF"
    TIME_AND_MATERIALS = "T&M"
    LABOR_HOUR = "LH"

    # Fixed-price types
    FIRM_FIXED_PRICE = "FFP"
    FIXED_PRICE_INCENTIVE = "FPI"
    FIXED_PRICE_AWARD_FEE = "FPAF"

    # Other
    COMMERCIAL = "COMMERCIAL"


class ContractStatus(str, Enum):
    """Contract lifecycle status."""

    DRAFT = "draft"          # Not yet active
    ACTIVE = "active"        # Accepting charges
    SUSPENDED = "suspended"  # Temporarily not accepting charges
    COMPLETED = "completed"  # Work done, pending closeout
    CLOSED = "closed"        # Final, no more changes


class ICEReportingFrequency(str, Enum):
    """
    Incurred Cost Electronically (ICE) reporting frequency.

    DCAA requires periodic ICE submissions for cost-reimbursement contracts.
    """

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    NONE = "none"  # For FFP contracts


class Contract(TrackedBase):
    """
    Government contract with DCAA compliance tracking.

    Used for:
    - Validating cost charges against contract type
    - Tracking funded vs ceiling amounts
    - Enforcing allowability per contract
    - ICE reporting requirements
    """

    __tablename__ = "contracts"

    __table_args__ = (
        UniqueConstraint("contract_number", name="uq_contract_number"),
        Index("idx_contract_status", "status"),
        Index("idx_contract_type", "contract_type"),
        Index("idx_contract_customer", "customer_party_id"),
        Index("idx_contract_active", "is_active"),
    )

    # =========================================================================
    # Contract Identification
    # =========================================================================

    contract_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="Unique contract number (e.g., FA8750-21-C-0001)",
    )

    contract_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Descriptive name for the contract",
    )

    duns_number: Mapped[str | None] = mapped_column(
        String(13),
        nullable=True,
        doc="DUNS number (9 or 13 digits)",
    )

    cage_code: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
        doc="Commercial and Government Entity (CAGE) code",
    )

    # =========================================================================
    # Contract Classification
    # =========================================================================

    contract_type: Mapped[ContractType] = mapped_column(
        String(20),
        nullable=False,
        doc="FAR contract type (CPFF, FFP, T&M, etc.)",
    )

    status: Mapped[ContractStatus] = mapped_column(
        String(20),
        nullable=False,
        default=ContractStatus.DRAFT,
        doc="Current lifecycle status",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        doc="Whether contract is active for new charges",
    )

    # =========================================================================
    # Customer Relationship
    # =========================================================================

    customer_party_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        ForeignKey("parties.id"),
        nullable=True,
        doc="Customer party (government agency or prime contractor)",
    )

    customer_party: Mapped["Party | None"] = relationship(
        "Party",
        foreign_keys=[customer_party_id],
        lazy="selectin",
    )

    # =========================================================================
    # Financial Controls
    # =========================================================================

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        doc="Contract currency (typically USD for government)",
    )

    funded_amount: Mapped[Decimal] = mapped_column(
        nullable=False,
        default=Decimal("0"),
        doc="Current funded amount (obligated)",
    )

    ceiling_amount: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="Contract ceiling (not to exceed)",
    )

    fee_rate: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="Fee rate for cost-plus contracts (e.g., 0.08 for 8%)",
    )

    ceiling_fee: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="Maximum fee amount for cost-plus contracts",
    )

    # =========================================================================
    # Period of Performance
    # =========================================================================

    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Contract start date",
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Contract end date",
    )

    period_of_performance_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Period of performance end (may differ from contract end)",
    )

    # =========================================================================
    # DCAA Compliance Settings
    # =========================================================================

    requires_timekeeping: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        doc="Whether DCAA-compliant timekeeping is required",
    )

    ice_reporting_frequency: Mapped[ICEReportingFrequency] = mapped_column(
        String(20),
        nullable=False,
        default=ICEReportingFrequency.ANNUAL,
        doc="ICE (Incurred Cost Electronically) submission frequency",
    )

    last_ice_submission_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Date of last ICE submission",
    )

    # =========================================================================
    # External References
    # =========================================================================

    prime_contract_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Prime contract number if this is a subcontract",
    )

    solicitation_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Original solicitation/RFP number",
    )

    external_ref: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="External system reference (e.g., CRM opportunity ID)",
    )

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def is_cost_reimbursement(self) -> bool:
        """Check if contract is cost-reimbursement type (DCAA intensive)."""
        return self.contract_type in {
            ContractType.COST_PLUS_FIXED_FEE,
            ContractType.COST_PLUS_INCENTIVE_FEE,
            ContractType.COST_PLUS_AWARD_FEE,
            ContractType.TIME_AND_MATERIALS,
            ContractType.LABOR_HOUR,
        }

    @property
    def is_fixed_price(self) -> bool:
        """Check if contract is fixed-price type."""
        return self.contract_type in {
            ContractType.FIRM_FIXED_PRICE,
            ContractType.FIXED_PRICE_INCENTIVE,
            ContractType.FIXED_PRICE_AWARD_FEE,
        }

    @property
    def can_accept_charges(self) -> bool:
        """Check if contract can accept new cost charges."""
        return (
            self.is_active
            and self.status == ContractStatus.ACTIVE
        )

    @property
    def is_within_pop(self) -> bool:
        """Check if current date is within period of performance."""
        today = date.today()
        if self.start_date and today < self.start_date:
            return False
        pop_end = self.period_of_performance_end or self.end_date
        if pop_end and today > pop_end:
            return False
        return True

    @property
    def available_funding(self) -> Decimal:
        """
        Calculate available funding (funded minus incurred).

        Note: This is a simple property. The actual calculation
        should query the ledger for incurred costs.
        """
        # TODO: Query ledger for incurred costs by contract_id
        return self.funded_amount

    def __repr__(self) -> str:
        return f"<Contract {self.contract_number}: {self.contract_name} ({self.contract_type.value})>"


class ContractLineItem(TrackedBase):
    """
    Contract Line Item (CLIN) for tracking costs at line-item level.

    CLINs break down contracts into billable work packages:
    - Direct labor by labor category
    - Materials
    - Travel
    - Subcontracts
    - Other direct costs (ODCs)

    Each CLIN has its own funded amount and ceiling.
    """

    __tablename__ = "contract_line_items"

    __table_args__ = (
        UniqueConstraint("contract_id", "line_number", name="uq_contract_clin"),
        Index("idx_clin_contract", "contract_id"),
        Index("idx_clin_type", "clin_type"),
    )

    # Parent contract
    contract_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("contracts.id"),
        nullable=False,
        doc="Parent contract ID",
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        foreign_keys=[contract_id],
        lazy="selectin",
    )

    # CLIN identification
    line_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc="CLIN number (e.g., 0001, 0002AA)",
    )

    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="CLIN description",
    )

    clin_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        doc="CLIN type: LABOR, MATERIAL, TRAVEL, SUBCONTRACT, ODC, FEE",
    )

    # Financial
    funded_amount: Mapped[Decimal] = mapped_column(
        nullable=False,
        default=Decimal("0"),
        doc="CLIN funded amount",
    )

    ceiling_amount: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="CLIN ceiling amount",
    )

    # Labor-specific fields
    labor_category: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Labor category code (for labor CLINs)",
    )

    hourly_rate: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="Contracted hourly rate (for T&M/LH)",
    )

    estimated_hours: Mapped[Decimal | None] = mapped_column(
        nullable=True,
        doc="Estimated hours for this CLIN",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        doc="Whether CLIN is active for charges",
    )

    def __repr__(self) -> str:
        return f"<ContractLineItem {self.line_number}: {self.description[:30]}>"

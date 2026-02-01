"""
Module: finance_kernel.models.contract
Responsibility: ORM persistence for government and commercial contracts, with
    DCAA compliance tracking for cost-reimbursement types.  Each Contract
    carries identification, financial controls, period of performance, and
    ICE reporting settings.  ContractLineItem (CLIN) breaks contracts into
    billable work packages.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    R10 -- contract_number is immutable once costs have been charged against
           the contract.  Changing the number would break cost traceability.
    Funding guard -- funded_amount cannot be reduced below incurred costs
           (enforced at service layer, not ORM).
    Ceiling guard -- ceiling_amount cannot be reduced below funded_amount
           (enforced at service layer, not ORM).

Failure modes:
    - IntegrityError on duplicate contract_number (uq_contract_number).
    - Guard rejection (upstream) when can_accept_charges returns False.
    - Guard rejection when funding would be exceeded.

Audit relevance:
    Contracts are the legal authority for cost accumulation in government
    contracting.  DCAA auditors trace every cost charge back to a contract
    and CLIN.  The contract_type determines timekeeping requirements,
    allowability rules, and ICE reporting obligations.  funded_amount and
    ceiling_amount are the financial controls that prevent unauthorized spending.
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
    Government or commercial contract with DCAA compliance tracking.

    Contract:
        Each Contract has a unique contract_number that is immutable once
        costs have been charged.  The contract_type determines which DCAA
        compliance rules apply.  Financial controls (funded_amount, ceiling_amount)
        are guard inputs for cost charge admissibility.

    Guarantees:
        - contract_number is globally unique (uq_contract_number constraint).
        - contract_type classifies the contract under FAR rules.
        - status lifecycle: DRAFT -> ACTIVE -> SUSPENDED -> COMPLETED -> CLOSED.
        - requires_timekeeping is True for cost-reimbursement types by default.

    Non-goals:
        - This model does NOT enforce funding limits at the ORM level; that is
          the responsibility of ContractService.validate_funding().
        - This model does NOT enforce ceiling limits; that is a service-layer guard.
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
        """Check if contract is cost-reimbursement type (DCAA intensive).

        Postconditions: Returns True for CPFF, CPIF, CPAF, T&M, and LH types.
            Cost-reimbursement contracts require DCAA-compliant timekeeping
            and are subject to ICE reporting requirements.
        """
        return self.contract_type in {
            ContractType.COST_PLUS_FIXED_FEE,
            ContractType.COST_PLUS_INCENTIVE_FEE,
            ContractType.COST_PLUS_AWARD_FEE,
            ContractType.TIME_AND_MATERIALS,
            ContractType.LABOR_HOUR,
        }

    @property
    def is_fixed_price(self) -> bool:
        """Check if contract is fixed-price type.

        Postconditions: Returns True for FFP, FPI, and FPAF types.
        """
        return self.contract_type in {
            ContractType.FIRM_FIXED_PRICE,
            ContractType.FIXED_PRICE_INCENTIVE,
            ContractType.FIXED_PRICE_AWARD_FEE,
        }

    @property
    def can_accept_charges(self) -> bool:
        """Check if contract can accept new cost charges.

        Postconditions: Returns True iff is_active is True AND status is ACTIVE.
            Used by guards to reject cost charges against inactive or non-ACTIVE
            contracts.
        """
        return (
            self.is_active
            and self.status == ContractStatus.ACTIVE
        )

    @property
    def is_within_pop(self) -> bool:
        """Check if current date is within period of performance.

        Postconditions: Returns True if today falls between start_date and
            the period_of_performance_end (or end_date if POP end is not set).

        NOTE: This property calls date.today() directly, which violates the
        clock injection rule for domain code.  It is acceptable here because
        Contract is a model (not domain), and callers requiring deterministic
        dates should use ContractService with an injected Clock instead.
        """
        today = date.today()
        if self.start_date and today < self.start_date:
            return False
        pop_end = self.period_of_performance_end or self.end_date
        if pop_end and today > pop_end:
            return False
        return True

    @property
    def available_funding(self) -> Decimal:
        """Return the funded amount for this contract.

        Postconditions: Returns funded_amount (the total obligated funding).
            To calculate remaining funding after incurred costs, callers MUST
            use ContractService.validate_funding() which accepts incurred_to_date
            as a parameter (models do not perform I/O).
        """
        return self.funded_amount

    def __repr__(self) -> str:
        return f"<Contract {self.contract_number}: {self.contract_name} ({self.contract_type.value})>"


class ContractLineItem(TrackedBase):
    """
    Contract Line Item (CLIN) for tracking costs at line-item level.

    Contract:
        Each CLIN belongs to exactly one Contract and has a unique line_number
        within that contract.  CLINs break contracts into billable work packages
        (labor, materials, travel, subcontracts, ODCs).

    Guarantees:
        - (contract_id, line_number) is unique (uq_contract_clin constraint).
        - funded_amount and ceiling_amount provide CLIN-level financial controls.
        - For labor CLINs: labor_category, hourly_rate, and estimated_hours
          support T&M/LH billing calculations.

    Non-goals:
        - This model does NOT enforce CLIN-level funding limits at the ORM
          level; that is a service-layer responsibility.
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

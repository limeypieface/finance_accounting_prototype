"""
ContractService -- government contract and CLIN management with DCAA compliance.

Responsibility:
    Provides CRUD operations for government contracts and Contract Line
    Items (CLINs), enforcing FAR/DCAA compliance rules at every charge
    boundary: active status, period of performance, funding limits,
    ceiling limits, and cost allowability.

Architecture position:
    Kernel > Services -- imperative shell.
    Called by the WIP and Project modules when posting cost charges;
    also used by interactive administration flows.

Invariants enforced:
    R3  -- Returns frozen ``ContractInfo`` / ``CLINInfo`` DTOs, never
           ORM entities (no leaked state).
    R7  -- Flush-only: never commits or rolls back the session.
    R16 -- Contract currency validated at creation time (ISO 4217).

Failure modes:
    - ContractNotFoundError: Contract lookup by ID or number fails.
    - ContractInactiveError: Charge attempted against non-ACTIVE contract.
    - ContractPOPExpiredError: Charge date outside period of performance.
    - ContractFundingExceededError: Charge would exceed funded amount.
    - ContractCeilingExceededError: Charge would exceed ceiling amount.
    - UnallowableCostToContractError: Unallowable cost charged to contract.
    - CLINNotFoundError / CLINInactiveError: CLIN lookup or status failure.

Audit relevance:
    Contract creation, activation, suspension, and closure are logged
    via the structured logger.  DCAA-relevant validation decisions
    (allowability, funding, ceiling) are logged with contract number
    and charge amounts for audit trail completeness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.exceptions import (
    CLINInactiveError,
    CLINNotFoundError,
    ContractCeilingExceededError,
    ContractFundingExceededError,
    ContractInactiveError,
    ContractNotFoundError,
    ContractPOPExpiredError,
    UnallowableCostToContractError,
)
from finance_kernel.models.contract import (
    Contract,
    ContractLineItem,
    ContractStatus,
    ContractType,
    ICEReportingFrequency,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.services.base import BaseService

logger = get_logger("services.contract")


@dataclass(frozen=True)
class ContractInfo:
    """
    Immutable DTO for contract data.

    R3 Compliance: Pure domain object, no ORM dependencies.
    """

    id: UUID
    contract_number: str
    contract_name: str
    contract_type: ContractType
    status: ContractStatus
    is_active: bool
    customer_party_id: UUID | None
    currency: str
    funded_amount: Decimal
    ceiling_amount: Decimal | None
    fee_rate: Decimal | None
    ceiling_fee: Decimal | None
    start_date: date | None
    end_date: date | None
    period_of_performance_end: date | None
    requires_timekeeping: bool
    ice_reporting_frequency: ICEReportingFrequency
    duns_number: str | None
    cage_code: str | None

    @property
    def is_cost_reimbursement(self) -> bool:
        """Check if contract is cost-reimbursement type."""
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
        return self.is_active and self.status == ContractStatus.ACTIVE


@dataclass(frozen=True)
class CLINInfo:
    """
    Immutable DTO for contract line item data.

    R3 Compliance: Pure domain object, no ORM dependencies.
    """

    id: UUID
    contract_id: UUID
    line_number: str
    description: str
    clin_type: str
    funded_amount: Decimal
    ceiling_amount: Decimal | None
    labor_category: str | None
    hourly_rate: Decimal | None
    estimated_hours: Decimal | None
    is_active: bool


class ContractService(BaseService[Contract]):
    """
    Service for managing government contracts and CLINs.

    Contract:
        Accepts contract identifiers (UUID or contract_number) and returns
        frozen ``ContractInfo`` / ``CLINInfo`` DTOs.  Mutation methods
        (create, activate, suspend, close, add_funding) flush changes
        within the caller's transaction.

    Guarantees:
        - R3: All public methods return immutable DTOs, not ORM entities.
        - DCAA compliance validation via ``validate_can_charge()`` checks
          active status, POP, funding, ceiling, and cost allowability.
        - R7: Session is flushed but never committed.

    Non-goals:
        - Does NOT manage the transaction boundary (caller's responsibility).
        - Does NOT enforce cost pool allocation rules (that is the
          allocation engine).
        - Does NOT compute incurred-cost-to-date (caller provides it).
    """

    def _to_dto(self, contract: Contract) -> ContractInfo:
        """Convert ORM Contract to ContractInfo DTO."""
        return ContractInfo(
            id=contract.id,
            contract_number=contract.contract_number,
            contract_name=contract.contract_name,
            contract_type=contract.contract_type,
            status=contract.status,
            is_active=contract.is_active,
            customer_party_id=contract.customer_party_id,
            currency=contract.currency,
            funded_amount=contract.funded_amount,
            ceiling_amount=contract.ceiling_amount,
            fee_rate=contract.fee_rate,
            ceiling_fee=contract.ceiling_fee,
            start_date=contract.start_date,
            end_date=contract.end_date,
            period_of_performance_end=contract.period_of_performance_end,
            requires_timekeeping=contract.requires_timekeeping,
            ice_reporting_frequency=contract.ice_reporting_frequency,
            duns_number=contract.duns_number,
            cage_code=contract.cage_code,
        )

    def _clin_to_dto(self, clin: ContractLineItem) -> CLINInfo:
        """Convert ORM ContractLineItem to CLINInfo DTO."""
        return CLINInfo(
            id=clin.id,
            contract_id=clin.contract_id,
            line_number=clin.line_number,
            description=clin.description,
            clin_type=clin.clin_type,
            funded_amount=clin.funded_amount,
            ceiling_amount=clin.ceiling_amount,
            labor_category=clin.labor_category,
            hourly_rate=clin.hourly_rate,
            estimated_hours=clin.estimated_hours,
            is_active=clin.is_active,
        )

    def _get_by_id(self, contract_id: UUID) -> Contract:
        """Get contract by ID, raising if not found."""
        contract = self.session.get(Contract, contract_id)
        if contract is None:
            raise ContractNotFoundError(str(contract_id))
        return contract

    def _get_by_number(self, contract_number: str) -> Contract:
        """Get contract by number, raising if not found."""
        stmt = select(Contract).where(Contract.contract_number == contract_number)
        contract = self.session.execute(stmt).scalar_one_or_none()
        if contract is None:
            raise ContractNotFoundError(contract_number)
        return contract

    def get_by_id(self, contract_id: UUID) -> ContractInfo:
        """
        Get contract by ID.

        Args:
            contract_id: UUID of the contract.

        Returns:
            ContractInfo DTO.

        Raises:
            ContractNotFoundError: If contract doesn't exist.
        """
        return self._to_dto(self._get_by_id(contract_id))

    def get_by_number(self, contract_number: str) -> ContractInfo:
        """
        Get contract by contract number.

        Args:
            contract_number: Contract number (e.g., "FA8750-21-C-0001").

        Returns:
            ContractInfo DTO.

        Raises:
            ContractNotFoundError: If contract doesn't exist.
        """
        return self._to_dto(self._get_by_number(contract_number))

    def find_by_number(self, contract_number: str) -> ContractInfo | None:
        """
        Find contract by number, returning None if not found.

        Args:
            contract_number: Contract number.

        Returns:
            ContractInfo DTO or None.
        """
        stmt = select(Contract).where(Contract.contract_number == contract_number)
        contract = self.session.execute(stmt).scalar_one_or_none()
        return self._to_dto(contract) if contract else None

    def list_active(
        self,
        contract_type: ContractType | None = None,
    ) -> list[ContractInfo]:
        """
        List active contracts, optionally filtered by type.

        Args:
            contract_type: Optional filter by contract type.

        Returns:
            List of ContractInfo DTOs.
        """
        stmt = select(Contract).where(
            Contract.is_active == True,
            Contract.status == ContractStatus.ACTIVE,
        )
        if contract_type:
            stmt = stmt.where(Contract.contract_type == contract_type)
        stmt = stmt.order_by(Contract.contract_number)

        contracts = self.session.execute(stmt).scalars().all()
        return [self._to_dto(c) for c in contracts]

    def list_cost_reimbursement(self) -> list[ContractInfo]:
        """
        List all active cost-reimbursement contracts.

        These require DCAA timekeeping compliance.

        Returns:
            List of ContractInfo DTOs.
        """
        cost_reimb_types = [
            ContractType.COST_PLUS_FIXED_FEE,
            ContractType.COST_PLUS_INCENTIVE_FEE,
            ContractType.COST_PLUS_AWARD_FEE,
            ContractType.TIME_AND_MATERIALS,
            ContractType.LABOR_HOUR,
        ]
        stmt = select(Contract).where(
            Contract.is_active == True,
            Contract.status == ContractStatus.ACTIVE,
            Contract.contract_type.in_(cost_reimb_types),
        ).order_by(Contract.contract_number)

        contracts = self.session.execute(stmt).scalars().all()
        return [self._to_dto(c) for c in contracts]

    def create_contract(
        self,
        contract_number: str,
        contract_name: str,
        contract_type: ContractType,
        actor_id: UUID,
        customer_party_id: UUID | None = None,
        currency: str = "USD",
        funded_amount: Decimal | None = None,
        ceiling_amount: Decimal | None = None,
        fee_rate: Decimal | None = None,
        ceiling_fee: Decimal | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        period_of_performance_end: date | None = None,
        requires_timekeeping: bool = True,
        ice_reporting_frequency: ICEReportingFrequency = ICEReportingFrequency.ANNUAL,
        duns_number: str | None = None,
        cage_code: str | None = None,
    ) -> ContractInfo:
        """
        Create a new contract.

        Preconditions:
            - ``contract_number`` is a unique, non-empty string.
            - ``currency`` is a valid ISO 4217 code (R16).
            - ``funded_amount``, if provided, is a non-negative ``Decimal``.
            - ``ceiling_amount``, if provided, is >= ``funded_amount``.

        Postconditions:
            - A new ``Contract`` row is flushed with status DRAFT and
              ``is_active=True``.
            - Returns a frozen ``ContractInfo`` DTO.

        Args:
            contract_number: Unique contract number.
            contract_name: Descriptive name.
            contract_type: FAR contract type.
            actor_id: UUID of user creating the contract.
            customer_party_id: Optional customer party.
            currency: Contract currency (default USD).
            funded_amount: Initial funded amount.
            ceiling_amount: Contract ceiling (NTE).
            fee_rate: Fee rate for cost-plus contracts.
            ceiling_fee: Maximum fee for cost-plus contracts.
            start_date: Contract start date.
            end_date: Contract end date.
            period_of_performance_end: POP end date.
            requires_timekeeping: Whether DCAA timekeeping required.
            ice_reporting_frequency: ICE submission frequency.
            duns_number: DUNS number.
            cage_code: CAGE code.

        Returns:
            Created ContractInfo DTO.
        """
        contract = Contract(
            contract_number=contract_number,
            contract_name=contract_name,
            contract_type=contract_type,
            customer_party_id=customer_party_id,
            currency=currency,
            funded_amount=funded_amount or Decimal("0"),
            ceiling_amount=ceiling_amount,
            fee_rate=fee_rate,
            ceiling_fee=ceiling_fee,
            start_date=start_date,
            end_date=end_date,
            period_of_performance_end=period_of_performance_end,
            requires_timekeeping=requires_timekeeping,
            ice_reporting_frequency=ice_reporting_frequency,
            duns_number=duns_number,
            cage_code=cage_code,
            created_by_id=actor_id,
        )
        self.session.add(contract)
        self.session.flush()
        logger.info(
            "contract_created",
            extra={"contract_number": contract_number, "contract_type": contract_type.value},
        )
        return self._to_dto(contract)

    def activate_contract(self, contract_id: UUID) -> ContractInfo:
        """
        Activate a contract to accept charges.

        Args:
            contract_id: UUID of contract to activate.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.ACTIVE
        contract.is_active = True
        self.session.flush()
        logger.info(
            "contract_activated",
            extra={"contract_number": contract.contract_number},
        )
        return self._to_dto(contract)

    def suspend_contract(self, contract_id: UUID) -> ContractInfo:
        """
        Suspend a contract temporarily.

        Suspended contracts cannot accept new charges.

        Args:
            contract_id: UUID of contract to suspend.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.SUSPENDED
        self.session.flush()
        logger.warning(
            "contract_suspended",
            extra={"contract_number": contract.contract_number, "reason": "suspended"},
        )
        return self._to_dto(contract)

    def complete_contract(self, contract_id: UUID) -> ContractInfo:
        """
        Mark contract as completed (work done, pending closeout).

        Args:
            contract_id: UUID of contract to complete.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.COMPLETED
        self.session.flush()
        return self._to_dto(contract)

    def close_contract(self, contract_id: UUID) -> ContractInfo:
        """
        Close a contract permanently.

        Closed contracts cannot accept charges or be reopened.

        Args:
            contract_id: UUID of contract to close.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.CLOSED
        contract.is_active = False
        self.session.flush()
        logger.info(
            "contract_terminated",
            extra={"contract_number": contract.contract_number},
        )
        return self._to_dto(contract)

    def add_funding(
        self,
        contract_id: UUID,
        amount: Decimal,
    ) -> ContractInfo:
        """
        Add funding to a contract.

        Args:
            contract_id: UUID of contract.
            amount: Amount to add to funded_amount.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.funded_amount = contract.funded_amount + amount
        self.session.flush()
        return self._to_dto(contract)

    def update_ceiling(
        self,
        contract_id: UUID,
        ceiling_amount: Decimal,
    ) -> ContractInfo:
        """
        Update contract ceiling amount.

        Args:
            contract_id: UUID of contract.
            ceiling_amount: New ceiling amount.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.ceiling_amount = ceiling_amount
        self.session.flush()
        return self._to_dto(contract)

    # =========================================================================
    # CLIN Operations
    # =========================================================================

    def get_clin(
        self,
        contract_id: UUID,
        clin_number: str,
    ) -> CLINInfo:
        """
        Get a CLIN by contract and line number.

        Args:
            contract_id: UUID of the contract.
            clin_number: CLIN number.

        Returns:
            CLINInfo DTO.

        Raises:
            CLINNotFoundError: If CLIN doesn't exist.
        """
        contract = self._get_by_id(contract_id)
        stmt = select(ContractLineItem).where(
            ContractLineItem.contract_id == contract_id,
            ContractLineItem.line_number == clin_number,
        )
        clin = self.session.execute(stmt).scalar_one_or_none()
        if clin is None:
            raise CLINNotFoundError(contract.contract_number, clin_number)
        return self._clin_to_dto(clin)

    def list_clins(self, contract_id: UUID) -> list[CLINInfo]:
        """
        List all CLINs for a contract.

        Args:
            contract_id: UUID of the contract.

        Returns:
            List of CLINInfo DTOs.
        """
        stmt = select(ContractLineItem).where(
            ContractLineItem.contract_id == contract_id,
        ).order_by(ContractLineItem.line_number)

        clins = self.session.execute(stmt).scalars().all()
        return [self._clin_to_dto(c) for c in clins]

    def add_clin(
        self,
        contract_id: UUID,
        line_number: str,
        description: str,
        clin_type: str,
        actor_id: UUID,
        funded_amount: Decimal | None = None,
        ceiling_amount: Decimal | None = None,
        labor_category: str | None = None,
        hourly_rate: Decimal | None = None,
        estimated_hours: Decimal | None = None,
    ) -> CLINInfo:
        """
        Add a CLIN to a contract.

        Args:
            contract_id: UUID of the contract.
            line_number: CLIN number (e.g., "0001").
            description: CLIN description.
            clin_type: Type (LABOR, MATERIAL, TRAVEL, etc.).
            actor_id: UUID of user creating the CLIN.
            funded_amount: Optional funded amount.
            ceiling_amount: Optional ceiling amount.
            labor_category: Labor category for labor CLINs.
            hourly_rate: Hourly rate for T&M/LH CLINs.
            estimated_hours: Estimated hours.

        Returns:
            Created CLINInfo DTO.
        """
        clin = ContractLineItem(
            contract_id=contract_id,
            line_number=line_number,
            description=description,
            clin_type=clin_type,
            funded_amount=funded_amount or Decimal("0"),
            ceiling_amount=ceiling_amount,
            labor_category=labor_category,
            hourly_rate=hourly_rate,
            estimated_hours=estimated_hours,
            created_by_id=actor_id,
        )
        self.session.add(clin)
        self.session.flush()
        return self._clin_to_dto(clin)

    # =========================================================================
    # DCAA Compliance Validation
    # =========================================================================

    def validate_can_charge(
        self,
        contract_number: str,
        charge_date: date,
        charge_amount: Decimal,
        is_allowable: bool = True,
        incurred_to_date: Decimal | None = None,
    ) -> ContractInfo:
        """
        Validate that a charge can be applied to a contract.

        Preconditions:
            - ``contract_number`` identifies an existing contract.
            - ``charge_amount`` is a positive ``Decimal`` (never float).
            - ``incurred_to_date``, if provided, is a non-negative ``Decimal``
              representing total costs already charged to this contract.

        Postconditions:
            - Returns ``ContractInfo`` only if ALL DCAA checks pass.
            - On any failure, raises a typed exception (no silent pass).

        Checks:
        1. Contract exists and is active
        2. Charge date is within period of performance
        3. Charge would not exceed funding
        4. Charge would not exceed ceiling
        5. Unallowable costs are rejected

        Args:
            contract_number: Contract number.
            charge_date: Date of the charge.
            charge_amount: Amount to charge.
            is_allowable: Whether the cost is DCAA-allowable.
            incurred_to_date: Current incurred amount (optional).

        Returns:
            ContractInfo if charge is valid.

        Raises:
            ContractNotFoundError: If contract doesn't exist.
            ContractInactiveError: If contract is not active.
            ContractPOPExpiredError: If outside POP.
            ContractFundingExceededError: If would exceed funding.
            ContractCeilingExceededError: If would exceed ceiling.
            UnallowableCostToContractError: If cost is unallowable.
        """
        contract = self._get_by_number(contract_number)

        # Check active status
        if not contract.can_accept_charges:
            raise ContractInactiveError(
                contract_number,
                contract.status.value,
            )

        # Check unallowable costs
        if not is_allowable:
            raise UnallowableCostToContractError(
                contract_number,
                cost_type="expense",
            )

        # Check period of performance
        if contract.start_date and charge_date < contract.start_date:
            raise ContractPOPExpiredError(
                contract_number,
                str(charge_date),
                str(contract.start_date) if contract.start_date else None,
                str(contract.period_of_performance_end or contract.end_date)
                if (contract.period_of_performance_end or contract.end_date)
                else None,
            )

        pop_end = contract.period_of_performance_end or contract.end_date
        if pop_end and charge_date > pop_end:
            raise ContractPOPExpiredError(
                contract_number,
                str(charge_date),
                str(contract.start_date) if contract.start_date else None,
                str(pop_end),
            )

        # Check funding
        incurred = incurred_to_date or Decimal("0")
        if (incurred + charge_amount) > contract.funded_amount:
            raise ContractFundingExceededError(
                contract_number,
                str(contract.funded_amount),
                str(incurred),
                str(charge_amount),
                contract.currency,
            )

        # Check ceiling
        if contract.ceiling_amount:
            if (incurred + charge_amount) > contract.ceiling_amount:
                raise ContractCeilingExceededError(
                    contract_number,
                    str(contract.ceiling_amount),
                    str(incurred),
                    str(charge_amount),
                    contract.currency,
                )

        return self._to_dto(contract)

    def validate_clin_charge(
        self,
        contract_id: UUID,
        clin_number: str,
        charge_amount: Decimal,
        incurred_to_date: Decimal | None = None,
    ) -> CLINInfo:
        """
        Validate that a charge can be applied to a CLIN.

        Args:
            contract_id: UUID of the contract.
            clin_number: CLIN number.
            charge_amount: Amount to charge.
            incurred_to_date: Current incurred amount (optional).

        Returns:
            CLINInfo if charge is valid.

        Raises:
            CLINNotFoundError: If CLIN doesn't exist.
            CLINInactiveError: If CLIN is not active.
        """
        contract = self._get_by_id(contract_id)

        stmt = select(ContractLineItem).where(
            ContractLineItem.contract_id == contract_id,
            ContractLineItem.line_number == clin_number,
        )
        clin = self.session.execute(stmt).scalar_one_or_none()

        if clin is None:
            raise CLINNotFoundError(contract.contract_number, clin_number)

        if not clin.is_active:
            raise CLINInactiveError(contract.contract_number, clin_number)

        return self._clin_to_dto(clin)

    def record_ice_submission(
        self,
        contract_id: UUID,
        submission_date: date,
    ) -> ContractInfo:
        """
        Record an ICE (Incurred Cost Electronically) submission.

        DCAA requires periodic ICE submissions for cost-reimbursement contracts.

        Args:
            contract_id: UUID of the contract.
            submission_date: Date of the ICE submission.

        Returns:
            Updated ContractInfo DTO.
        """
        contract = self._get_by_id(contract_id)
        contract.last_ice_submission_date = submission_date
        self.session.flush()
        return self._to_dto(contract)

    def get_contracts_needing_ice(self) -> list[ContractInfo]:
        """
        Get contracts that need ICE submission.

        Returns contracts where:
        - Active and cost-reimbursement type
        - ICE submission is overdue based on frequency

        Returns:
            List of ContractInfo DTOs needing ICE submission.
        """
        cost_reimb_types = [
            ContractType.COST_PLUS_FIXED_FEE,
            ContractType.COST_PLUS_INCENTIVE_FEE,
            ContractType.COST_PLUS_AWARD_FEE,
            ContractType.TIME_AND_MATERIALS,
            ContractType.LABOR_HOUR,
        ]

        stmt = select(Contract).where(
            Contract.is_active == True,
            Contract.status == ContractStatus.ACTIVE,
            Contract.contract_type.in_(cost_reimb_types),
            Contract.ice_reporting_frequency != ICEReportingFrequency.NONE,
        ).order_by(Contract.contract_number)

        contracts = self.session.execute(stmt).scalars().all()

        # Filter by submission date
        today = date.today()
        needing_ice = []
        for contract in contracts:
            if contract.last_ice_submission_date is None:
                needing_ice.append(contract)
                continue

            # Calculate days since last submission
            days_since = (today - contract.last_ice_submission_date).days

            # Check based on frequency
            if contract.ice_reporting_frequency == ICEReportingFrequency.MONTHLY:
                if days_since > 30:
                    needing_ice.append(contract)
            elif contract.ice_reporting_frequency == ICEReportingFrequency.QUARTERLY:
                if days_since > 90:
                    needing_ice.append(contract)
            elif contract.ice_reporting_frequency == ICEReportingFrequency.ANNUAL:
                if days_since > 365:
                    needing_ice.append(contract)

        return [self._to_dto(c) for c in needing_ice]

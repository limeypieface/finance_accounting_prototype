"""Government contract and CLIN management with DCAA compliance."""

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
from finance_kernel.logging_config import get_logger
from finance_kernel.models.contract import (
    Contract,
    ContractLineItem,
    ContractStatus,
    ContractType,
    ICEReportingFrequency,
)
from finance_kernel.services.base import BaseService

logger = get_logger("services.contract")


@dataclass(frozen=True)
class ContractInfo:
    """Immutable DTO for contract data."""

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
    """Immutable DTO for contract line item data."""

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
    """Manages government contracts and CLINs with DCAA compliance validation."""

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
        """Get contract by ID."""
        return self._to_dto(self._get_by_id(contract_id))

    def get_by_number(self, contract_number: str) -> ContractInfo:
        """Get contract by contract number."""
        return self._to_dto(self._get_by_number(contract_number))

    def find_by_number(self, contract_number: str) -> ContractInfo | None:
        """Find contract by number, returning None if not found."""
        stmt = select(Contract).where(Contract.contract_number == contract_number)
        contract = self.session.execute(stmt).scalar_one_or_none()
        return self._to_dto(contract) if contract else None

    def list_active(
        self,
        contract_type: ContractType | None = None,
    ) -> list[ContractInfo]:
        """List active contracts, optionally filtered by type."""
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
        """List all active cost-reimbursement contracts."""
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
        """Create a new contract."""
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
        """Activate a contract to accept charges."""
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
        """Suspend a contract temporarily."""
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.SUSPENDED
        self.session.flush()
        logger.warning(
            "contract_suspended",
            extra={"contract_number": contract.contract_number, "reason": "suspended"},
        )
        return self._to_dto(contract)

    def complete_contract(self, contract_id: UUID) -> ContractInfo:
        """Mark contract as completed (pending closeout)."""
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.COMPLETED
        self.session.flush()
        return self._to_dto(contract)

    def close_contract(self, contract_id: UUID) -> ContractInfo:
        """Close a contract permanently."""
        contract = self._get_by_id(contract_id)
        contract.status = ContractStatus.CLOSED
        contract.is_active = False
        self.session.flush()
        logger.info(
            "contract_terminated",
            extra={"contract_number": contract.contract_number},
        )
        return self._to_dto(contract)

    def add_funding(self, contract_id: UUID, amount: Decimal) -> ContractInfo:
        """Add funding to a contract."""
        contract = self._get_by_id(contract_id)
        contract.funded_amount = contract.funded_amount + amount
        self.session.flush()
        return self._to_dto(contract)

    def update_ceiling(self, contract_id: UUID, ceiling_amount: Decimal) -> ContractInfo:
        """Update contract ceiling amount."""
        contract = self._get_by_id(contract_id)
        contract.ceiling_amount = ceiling_amount
        self.session.flush()
        return self._to_dto(contract)

    # =========================================================================
    # CLIN Operations
    # =========================================================================

    def get_clin(self, contract_id: UUID, clin_number: str) -> CLINInfo:
        """Get a CLIN by contract and line number."""
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
        """List all CLINs for a contract."""
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
        """Add a CLIN to a contract."""
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
        """Validate that a charge can be applied to a contract (DCAA checks)."""
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
        """Validate that a charge can be applied to a CLIN."""
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

    def record_ice_submission(self, contract_id: UUID, submission_date: date) -> ContractInfo:
        """Record an ICE submission for DCAA compliance."""
        contract = self._get_by_id(contract_id)
        contract.last_ice_submission_date = submission_date
        self.session.flush()
        return self._to_dto(contract)

    def get_contracts_needing_ice(self) -> list[ContractInfo]:
        """Get active cost-reimbursement contracts needing ICE submission."""
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

            days_since = (today - contract.last_ice_submission_date).days

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

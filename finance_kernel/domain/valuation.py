"""Valuation -- Versioned valuation models and resolver."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.valuation")


@dataclass(frozen=True)
class ValuationResult:
    """Result of a valuation computation."""

    success: bool
    value: Decimal | None = None
    currency: str | None = None
    model_id: str | None = None
    model_version: int | None = None
    error: str | None = None

    @classmethod
    def ok(
        cls,
        value: Decimal,
        currency: str,
        model_id: str,
        model_version: int,
    ) -> "ValuationResult":
        """Successful valuation."""
        return cls(
            success=True,
            value=value,
            currency=currency,
            model_id=model_id,
            model_version=model_version,
        )

    @classmethod
    def fail(cls, error: str) -> "ValuationResult":
        """Failed valuation."""
        return cls(success=False, error=error)


@dataclass(frozen=True)
class ValuationModel:
    """Versioned valuation model definition."""

    model_id: str
    version: int
    description: str
    currency_field: str
    uses_fields: tuple[str, ...]
    compute: Callable[[dict[str, Any]], Decimal | None]

    @property
    def model_key(self) -> str:
        """Unique key for this model version."""
        return f"{self.model_id}:v{self.version}"


class ValuationModelNotFoundError(Exception):
    """Valuation model not found."""

    code: str = "VALUATION_MODEL_NOT_FOUND"

    def __init__(self, model_id: str, version: int | None = None):
        self.model_id = model_id
        self.version = version
        msg = f"Valuation model not found: {model_id}"
        if version is not None:
            msg += f" (version {version})"
        super().__init__(msg)


class ValuationModelRegistry:
    """Registry for valuation models (P8)."""

    # Class-level registry: model_id -> {version -> model}
    _models: ClassVar[dict[str, dict[int, ValuationModel]]] = {}

    @classmethod
    def register(cls, model: ValuationModel) -> None:
        """Register a valuation model."""
        if model.model_id not in cls._models:
            cls._models[model.model_id] = {}

        cls._models[model.model_id][model.version] = model
        logger.info(
            "valuation_model_registered",
            extra={
                "model_id": model.model_id,
                "version": model.version,
                "description": model.description,
            },
        )

    @classmethod
    def get(
        cls,
        model_id: str,
        version: int | None = None,
    ) -> ValuationModel:
        """Get a valuation model by ID, optionally by version."""
        if model_id not in cls._models:
            raise ValuationModelNotFoundError(model_id, version)

        versions = cls._models[model_id]

        if version is not None:
            if version not in versions:
                raise ValuationModelNotFoundError(model_id, version)
            return versions[version]

        if not versions:
            raise ValuationModelNotFoundError(model_id)

        latest = max(versions.keys())
        return versions[latest]

    @classmethod
    def has_model(cls, model_id: str, version: int | None = None) -> bool:
        """Check if model exists."""
        if model_id not in cls._models:
            return False
        if version is None:
            return len(cls._models[model_id]) > 0
        return version in cls._models[model_id]

    @classmethod
    def list_models(cls) -> list[str]:
        """List all registered model IDs."""
        return sorted(cls._models.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear all models. For testing only."""
        cls._models.clear()


class ValuationResolver:
    """Resolves valuation using registered models."""

    def resolve(
        self,
        model_id: str,
        payload: dict[str, Any],
        model_version: int | None = None,
    ) -> ValuationResult:
        """Resolve valuation using a registered model."""
        logger.debug(
            "valuation_resolve_started",
            extra={
                "model_id": model_id,
                "model_version": model_version,
            },
        )

        try:
            model = ValuationModelRegistry.get(model_id, model_version)
        except ValuationModelNotFoundError as e:
            logger.warning(
                "valuation_model_not_found",
                extra={
                    "model_id": model_id,
                    "model_version": model_version,
                },
            )
            return ValuationResult.fail(str(e))

        # Extract currency
        currency = self._get_field_value(payload, model.currency_field)
        if currency is None:
            logger.warning(
                "valuation_currency_missing",
                extra={
                    "model_id": model_id,
                    "currency_field": model.currency_field,
                },
            )
            return ValuationResult.fail(
                f"Currency field '{model.currency_field}' not found in payload"
            )

        # Compute value
        try:
            value = model.compute(payload)
            if value is None:
                logger.warning(
                    "valuation_computation_null",
                    extra={
                        "model_id": model.model_id,
                        "model_version": model.version,
                    },
                )
                return ValuationResult.fail("Model computation returned None")

            logger.info(
                "valuation_computed",
                extra={
                    "model_id": model.model_id,
                    "model_version": model.version,
                    "value": str(value),
                    "currency": str(currency),
                    "method": model.description,
                },
            )

            return ValuationResult.ok(
                value=value,
                currency=str(currency),
                model_id=model.model_id,
                model_version=model.version,
            )
        except Exception as e:
            logger.error(
                "valuation_computation_failed",
                extra={
                    "model_id": model.model_id,
                    "model_version": model.version,
                    "error": str(e),
                },
            )
            return ValuationResult.fail(f"Valuation computation failed: {e}")

    def _get_field_value(
        self,
        payload: dict[str, Any],
        field_path: str,
    ) -> Any:
        """Get a value from payload by dot-notation path."""
        if field_path.startswith("payload."):
            field_path = field_path[8:]

        parts = field_path.split(".")
        current = payload

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current


# Register standard valuation models


def _quantity_times_unit_price(payload: dict[str, Any]) -> Decimal | None:
    """Standard computation: quantity * unit_price."""
    quantity = payload.get("quantity")
    unit_price = payload.get("unit_price")

    if quantity is None or unit_price is None:
        return None

    try:
        return Decimal(str(quantity)) * Decimal(str(unit_price))
    except (InvalidOperation, ValueError):
        return None


def _fixed_amount(payload: dict[str, Any]) -> Decimal | None:
    """Extract fixed amount from payload."""
    amount = payload.get("amount")
    if amount is None:
        return None
    try:
        return Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None


# Standard receipt valuation model
STANDARD_RECEIPT_V1 = ValuationModel(
    model_id="standard_receipt_v1",
    version=1,
    description="Standard receipt valuation: quantity * unit_price",
    currency_field="currency",
    uses_fields=("quantity", "unit_price", "currency"),
    compute=_quantity_times_unit_price,
)

# Fixed amount valuation model
FIXED_AMOUNT_V1 = ValuationModel(
    model_id="fixed_amount_v1",
    version=1,
    description="Fixed amount from payload",
    currency_field="currency",
    uses_fields=("amount", "currency"),
    compute=_fixed_amount,
)


def _register_default_models():
    """Register default valuation models."""
    ValuationModelRegistry.register(STANDARD_RECEIPT_V1)
    ValuationModelRegistry.register(FIXED_AMOUNT_V1)


_register_default_models()

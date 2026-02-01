"""
Valuation -- Versioned valuation models and resolver.

Responsibility:
    Provides versioned valuation models that compute monetary values from
    event payloads.  Profiles reference models by ID only (P8 -- no inline
    expressions).

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.

Invariants enforced:
    P8 -- Profiles reference valuation models by ID only; no inline
          expressions are permitted.

Failure modes:
    - ValuationModelNotFoundError (R18: VALUATION_MODEL_NOT_FOUND)
    - ValuationResult.fail() when computation returns None or raises

Audit relevance:
    ValuationResult records model_id and model_version so auditors can
    verify that the correct valuation logic was applied and replay it
    deterministically.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, ClassVar

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.valuation")


@dataclass(frozen=True)
class ValuationResult:
    """
    Result of a valuation computation.

    Attributes:
        success: Whether valuation succeeded
        value: The computed value (if success)
        currency: The currency of the value
        model_id: ID of the model used
        model_version: Version of the model used
        error: Error message if failed
    """

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
    """
    Versioned valuation model definition.

    Encapsulates the logic for computing value from event data.
    Uses a callable for flexibility while maintaining determinism.

    Attributes:
        model_id: Unique model identifier
        version: Model version
        description: Human-readable description
        currency_field: Field path to get currency from
        uses_fields: Fields this model reads from
        compute: Function to compute value from payload
    """

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
    """
    Registry for valuation models.

    Contract:
        Class-level singleton registry.  ``register()`` adds models;
        ``get()`` returns the requested model or raises.

    Guarantees:
        - ``get()`` returns the latest version when ``version=None``.
        - ``has_model()`` is a non-throwing membership test.

    Non-goals:
        - Does NOT persist to database (in-memory, populated at module load).
        - Does NOT evaluate models (ValuationResolver does that).

    Invariants enforced:
        P8 -- Profiles reference models by ID only; no inline expressions.
    """

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
        """
        Get a valuation model.

        Args:
            model_id: The model identifier.
            version: Specific version, or None for latest.

        Returns:
            The ValuationModel.

        Raises:
            ValuationModelNotFoundError: If not found.
        """
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
    """
    Resolves valuation using registered models.

    Contract:
        ``resolve()`` looks up a model by ID and applies its compute
        function to the payload.  Returns ``ValuationResult`` (never raises
        for business rule violations).

    Guarantees:
        - ``resolve()`` always returns a ``ValuationResult`` -- success or
          failure, never an unhandled exception for model logic errors.

    Non-goals:
        - Does NOT manage model registration (ValuationModelRegistry does).
        - Does NOT perform I/O.
    """

    def resolve(
        self,
        model_id: str,
        payload: dict[str, Any],
        model_version: int | None = None,
    ) -> ValuationResult:
        """
        Resolve valuation using a registered model.

        Args:
            model_id: The valuation model ID.
            payload: The event payload.
            model_version: Specific version, or None for latest.

        Returns:
            ValuationResult with value and currency.
        """
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

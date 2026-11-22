"""A Django model field storing a physical quantity as a nano-base integer.

:class:`QuantityField` is a thin :class:`~django.db.models.BigIntegerField` that
records the canonical base unit it stores (e.g. ``"nanosecond"``). Values are
plain integers in that base unit, but the field also accepts a Pint
:class:`~pint.Quantity` on assignment (converting it) and can hand one back to
downstream physics code via :meth:`QuantityField.quantity`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django.db import models

from .registry import get_registry

if TYPE_CHECKING:
    import pint


class QuantityField(models.BigIntegerField):
    def __init__(self, *args: Any, base_unit: str, **kwargs: Any) -> None:
        self.base_unit = base_unit
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        kwargs["base_unit"] = self.base_unit
        return name, path, args, kwargs

    def get_prep_value(self, value: int | pint.Quantity | None) -> int | None:
        if value is not None and hasattr(value, "to"):  # a pint Quantity
            quantity = cast("pint.Quantity", value)
            value = int(round(quantity.to(self.base_unit).magnitude))
        return super().get_prep_value(value)

    def quantity(self, value: int | None) -> pint.Quantity | None:
        """Wrap a stored integer back into a Pint quantity in ``base_unit``."""
        if value is None:
            return None
        return value * get_registry()(self.base_unit)

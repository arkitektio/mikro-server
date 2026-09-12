"""The global, configurable Pint unit registry used by all kanne scalars.

A single registry instance is shared process-wide (Pint quantities are only
comparable when they originate from the same registry). It is built lazily on
first use and can be customised through the ``KANNE`` Django setting::

    KANNE = {
        "registry_kwargs": {"system": "SI"},
        "definitions": ["myunit = 1e-9 ampere = myu"],
    }
"""

from __future__ import annotations

import pint
from django.conf import settings

_REGISTRY: pint.UnitRegistry | None = None


def get_registry() -> pint.UnitRegistry:
    """Return the shared unit registry, building it on first use."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build()
    return _REGISTRY


def set_registry(ureg: pint.UnitRegistry) -> None:
    """Override the shared registry (intended for tests / future configuration)."""
    global _REGISTRY
    _REGISTRY = ureg


def _build() -> pint.UnitRegistry:
    cfg = getattr(settings, "KANNE", {}) or {}
    ureg = pint.UnitRegistry(**cfg.get("registry_kwargs", {}))
    # Allow offset units (e.g. degC) to be parsed from a single string and
    # converted to their base unit (kelvin). Without this, "37 degC" raises an
    # ambiguous-offset-operation error.
    ureg.autoconvert_offset_to_baseunit = True
    for line in cfg.get("definitions", []):
        ureg.define(line)
    return ureg

"""Laying one config document over another."""

from __future__ import annotations

from hub_contract.json_types import JSON


def merge(base: dict[str, JSON], over: dict[str, JSON]) -> dict[str, JSON]:
    """``over`` laid on ``base``: a mapping is merged into key by key, anything else replaces."""
    merged = dict(base)
    for key, value in over.items():
        existing = merged.get(key)
        merged[key] = merge(existing, value) if isinstance(existing, dict) and isinstance(value, dict) else value
    return merged

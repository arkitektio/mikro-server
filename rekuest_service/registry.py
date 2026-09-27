"""Module-level helpers over :data:`rekuest_service.service.default_service` — kept for existing callers.

A service declares itself with its own :class:`rekuest_service.Service` (``@service.action``,
``service.signal``); these register on the process-default service instead, which is what
``rekuest_service.views.urlpatterns`` serves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rekuest_service.service import Action, SignalDeclaration, default_service

__all__ = ["Action", "SignalDeclaration", "action", "declare_signal", "declared_signals", "registered"]


def action(
    interface: str, *, name: str | None = None, description: str | None = None, default_interval: int | None = None, default_cron: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as the action ``interface`` on the default service (see ``Service.action``)."""
    return default_service.action(interface=interface, name=name or interface, description=description, default_interval=default_interval, default_cron=default_cron)


def registered() -> dict[str, Action]:
    return default_service.actions


def declare_signal(identifier: str, *, kinds=("CREATED",), descriptors=(), description: str | None = None) -> SignalDeclaration:
    """Declare a signal on the default service (see ``Service.signal``)."""
    return default_service.signal(identifier, kinds=kinds, descriptors=descriptors, description=description).declaration


def declared_signals() -> dict[str, SignalDeclaration]:
    return {identifier: handle.declaration for identifier, handle in default_service.signals.items()}

"""A service's HookAgent: the actions rekuest can ask the service to run.

    from rekuest_service import HookAgent

    agent = HookAgent(service)

    @agent.action(default_interval=300)
    def reembed_stale() -> dict:
        '''Re-embed stale rows.'''
        return {"reembedded": reembed_all(MODELS)}

Every organization of the hub has the agent, with its own schedules, so an action runs for one
organization: a function that takes ``organization`` is given its slug and does that
organization's share of the work, nothing else::

    @agent.action(default_interval=300)
    def sync_all_mailboxes(organization: str) -> dict: ...

A HookAgent is an agent like any app's, with two differences: it is reached over HTTP (rekuest
POSTs each Assign to the service's ``_rekuest/hook``; :mod:`rekuest_service.views`), and it
belongs to a :class:`~rekuest_service.Service`, which is where the structures it works on are
declared. rekuest registers its actions as real actions and schedules every one that declares a
default.

The split matters: a service says what exists (structures, their descriptors, signals); only an
agent says what can be done (actions).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rekuest_service.service import Service, default_service


@dataclass(frozen=True)
class Action:
    interface: str
    function: Callable[..., Any]
    name: str
    description: str | None
    default_interval: int | None
    default_cron: str | None

    @property
    def takes_organization(self) -> bool:
        """Whether the function asks for the ``organization`` (slug) the run is for."""
        return "organization" in inspect.signature(self.function).parameters

    def manifest(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "name": self.name,
            "description": self.description,
            "default_interval": self.default_interval,
            "default_cron": self.default_cron,
        }


def _docstring_parts(function: Callable[..., Any]) -> tuple[str | None, str | None]:
    """``(first line, rest)`` of a docstring — the name and description arkitekt derives too."""
    doc = inspect.getdoc(function)
    if not doc:
        return None, None
    first, _, rest = doc.partition("\n")
    return first.strip() or None, rest.strip() or None


class HookAgent:
    """The agent of one service: its actions. A service has at most one."""

    def __init__(self, service: Service) -> None:
        if service.hook_agent is not None:
            raise ValueError(f"The service {service.name!r} already has a HookAgent")
        self.service = service
        self._actions: dict[str, Action] = {}
        service.hook_agent = self

    def action(
        self,
        function: Callable[..., Any] | None = None,
        /,
        *,
        interface: str | None = None,
        name: str | None = None,
        description: str | None = None,
        default_interval: int | None = None,
        default_cron: str | None = None,
    ) -> Any:
        """Offer ``function`` (sync or async; no arguments, or ``organization``) as an action; ``@agent.action`` or ``@agent.action(...)``.

        The interface defaults to the function's name; name and description to its docstring's
        first line and the rest. ``default_interval`` (seconds) or ``default_cron`` makes rekuest
        schedule it on its own. It should return a small JSON-able dict, reported as the run's
        result, and must be safe to run twice — a lost report may get it redelivered.
        """
        if default_interval is not None and default_cron is not None:
            raise ValueError("Give default_interval or default_cron, not both")

        def register(target: Callable[..., Any]) -> Callable[..., Any]:
            key = interface or target.__name__
            doc_name, doc_description = _docstring_parts(target)
            declared = Action(key, target, name or doc_name or key, description if description is not None else doc_description, default_interval, default_cron)
            existing = self._actions.get(key)
            if existing is not None and existing.function is not target:
                raise ValueError(f"The rekuest action {key!r} is registered twice")
            self._actions[key] = declared
            return target

        return register(function) if function is not None else register

    @property
    def actions(self) -> dict[str, Action]:
        return dict(self._actions)

    def manifest(self) -> list[dict[str, Any]]:
        return [a.manifest() for a in self._actions.values()]

    def __repr__(self) -> str:
        return f"HookAgent({self.service.name!r}, actions={list(self._actions)})"


#: The default service's agent: what the module-level ``rekuest_service.action`` registers on.
default_agent = HookAgent(default_service)

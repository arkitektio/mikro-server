"""A service as the hub's rekuest sees it: its actions and its signals, in one declaration.

Modelled on the arkitekt client's ``App``::

    from rekuest_service import Service

    service = Service("mikro", description="Microscopy data")

    @service.action(default_interval=300)
    def reembed_stale() -> dict:
        '''Re-embed stale rows.'''
        return {"reembedded": reembed_all(MODELS)}

    dataset_created = service.signal("@mikro/arraydataset", kinds=["CREATED"], descriptors=ARRAY_DESCRIPTOR_KEYS)

    # later, where a dataset is created:
    dataset_created.emit(dataset.pk, organization=dataset.organization.slug, descriptors={...})

and in ``urls.py``: ``urlpatterns = [..., *service.urls]``. The endpoints are bound to THIS
service — no module-level registry is consulted — so what the manifest lists is exactly what the
declaration says. rekuest reads that manifest (``GET <hook_url>/manifest``) when it provisions
the service: actions become rekuest actions (with a schedule when they declare a default),
signals become the declarations triggers are checked against.

The name is what rekuest knows the service by (``rekuest.service_agents[].service``); a
``SERVICE`` in ``settings.REKUEST_HOOK`` overrides it, e.g. for a second instance of one service.
"""

from __future__ import annotations

import datetime
import inspect
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

KINDS = ("CREATED", "UPDATED", "DELETED")


@dataclass(frozen=True)
class Action:
    interface: str
    function: Callable[..., Any]
    name: str
    description: str | None
    default_interval: int | None
    default_cron: str | None

    def manifest(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "name": self.name,
            "description": self.description,
            "default_interval": self.default_interval,
            "default_cron": self.default_cron,
        }


@dataclass(frozen=True)
class SignalDeclaration:
    identifier: str
    kinds: tuple[str, ...]
    descriptors: tuple[str, ...]
    description: str | None

    def manifest(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "kinds": list(self.kinds), "descriptors": list(self.descriptors), "description": self.description}


def organization_of(path: str = "organization") -> Callable[[Any], str | None]:
    """An ``organization=`` for :meth:`Service.model_signal`: follow ``path`` (dotted, e.g.
    ``"room.organization"``) from the object to its organization and take its slug."""
    steps = path.split(".")

    def resolve(obj: Any) -> str | None:
        for step in steps:
            obj = getattr(obj, step, None)
            if obj is None:
                return None
        return getattr(obj, "slug", None)

    return resolve


def _docstring_parts(function: Callable[..., Any]) -> tuple[str | None, str | None]:
    """``(first line, rest)`` of a docstring — the name and description arkitekt derives too."""
    doc = inspect.getdoc(function)
    if not doc:
        return None, None
    first, _, rest = doc.partition("\n")
    return first.strip() or None, rest.strip() or None


class Signal:
    """A declared signal. ``emit`` announces one object; it is best-effort and never raises on delivery."""

    def __init__(self, service: Service, declaration: SignalDeclaration) -> None:
        self.service = service
        self.declaration = declaration
        self._warned_keys: set[str] = set()

    @property
    def identifier(self) -> str:
        return self.declaration.identifier

    def emit(self, object: Any, *, organization: str, descriptors: dict[str, Any] | None = None, kind: str | None = None) -> None:
        """Announce ``kind`` (default: the one declared kind) of ``object``, once the transaction commits.

        A kind outside the declaration is a programming error and raises. Descriptor keys the
        declaration does not name are sent anyway, with one warning: rekuest checks triggers
        against the declared keys, so an undeclared key is one no trigger can test.
        """
        kinds = self.declaration.kinds
        if kind is None:
            if len(kinds) != 1:
                raise ValueError(f"{self.identifier} declares {kinds}; say which kind this is")
            kind = kinds[0]
        if kind not in kinds:
            raise ValueError(f"{self.identifier} is declared for {kinds}, not {kind!r}")
        undeclared = set(descriptors or {}) - set(self.declaration.descriptors) - self._warned_keys
        if undeclared:
            self._warned_keys |= undeclared
            logger.warning("Signal %s carries undeclared descriptor(s) %s; no trigger can test them", self.identifier, ", ".join(sorted(undeclared)))
        self.service._emit(kind, self.identifier, object, organization=organization, descriptors=descriptors)

    def __repr__(self) -> str:
        return f"Signal({self.identifier!r}, kinds={self.declaration.kinds})"


class Service:
    """One service's declaration towards its hub's rekuest: what it offers, what it announces."""

    def __init__(self, name: str | None, *, identifier: str | None = None, description: str | None = None, key: Any = None) -> None:
        self.name = name
        #: The key this service signs with; None (the rule) = this instance's key
        #: (``settings.INSTANCE``). Set when one process plays several services (tests).
        self.key = key
        #: The fakts identifier this instance signs as (``iss``) — what the coord's trust bundle
        #: lists its key under. Defaults to ``live.arkitekt.<name>``.
        self.identifier = identifier or (f"live.arkitekt.{name}" if name else None)
        self.description = description
        self._actions: dict[str, Action] = {}
        self._signals: dict[str, Signal] = {}
        self._warned_undeclared: set[tuple[str, str]] = set()

    # --- declaring -----------------------------------------------------------------------

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
        """Offer ``function`` (sync or async, no arguments) as an action; ``@service.action`` or ``@service.action(...)``.

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

    def signal(self, identifier: str, *, kinds: Iterable[str] = ("CREATED",), descriptors: Iterable[str] = (), description: str | None = None) -> Signal:
        """Declare that this service emits ``kinds`` of ``identifier`` objects with these descriptor keys."""
        kinds = tuple(kinds)
        bad = [k for k in kinds if k not in KINDS]
        if bad or not kinds:
            raise ValueError(f"A signal kind is one of {KINDS}, not {bad or 'nothing'}")
        declaration = SignalDeclaration(identifier, kinds, tuple(descriptors), description)
        existing = self._signals.get(identifier)
        if existing is not None:
            if existing.declaration != declaration:
                raise ValueError(f"The signal {identifier!r} is declared twice, differently")
            return existing
        handle = Signal(self, declaration)
        self._signals[identifier] = handle
        return handle

    # --- what rekuest reads --------------------------------------------------------------

    @property
    def actions(self) -> dict[str, Action]:
        return dict(self._actions)

    @property
    def signals(self) -> dict[str, Signal]:
        return dict(self._signals)

    def manifest(self) -> dict[str, Any]:
        return {
            "service": self.service_name(),
            "identifier": self.signing_identifier(),
            "description": self.description,
            "actions": [a.manifest() for a in self._actions.values()],
            "signals": [s.declaration.manifest() for s in self._signals.values()],
        }

    @property
    def urls(self) -> list:
        """The ``_rekuest/hook`` endpoints, bound to this service. Mount them in ``urls.py``."""
        from rekuest_service.views import urlpatterns_for

        return urlpatterns_for(self)

    # --- configuration and sending -------------------------------------------------------

    def config(self) -> dict[str, Any] | None:
        """``settings.REKUEST_HOOK`` when it can reach rekuest — its URL set and an instance key
        configured (``settings.INSTANCE``) — else None, and everything is then a no-op."""
        config = getattr(settings, "REKUEST_HOOK", None)
        if not config or not config.get("REKUEST_URL") or self.signing_key() is None:
            return None
        return config

    def signing_key(self) -> Any:
        """What this service signs with: its own ``key``, else this instance's key."""
        from rekuest_service.trust import instance_key

        return self.key if self.key is not None else instance_key()

    def signing_identifier(self) -> str | None:
        """What this service signs as: ``settings.REKUEST_HOOK["IDENTIFIER"]``, the declared
        identifier, or — for a service declared without a name (the default service) —
        ``live.arkitekt.<the configured service name>``."""
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        if config.get("IDENTIFIER") or self.identifier:
            return config.get("IDENTIFIER") or self.identifier
        name = self.service_name()
        return f"live.arkitekt.{name}" if name else None

    @staticmethod
    def rekuest_identifier() -> str:
        """Whom rekuest's requests must come from (``settings.REKUEST_HOOK["REKUEST_IDENTIFIER"]``)."""
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        return config.get("REKUEST_IDENTIFIER") or "live.arkitekt.rekuest"

    def service_name(self) -> str | None:
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        return config.get("SERVICE") or self.name

    def model_signal(
        self,
        model: Any,
        identifier: str,
        *,
        organization: Callable[[Any], str | None],
        kinds: Iterable[str] = KINDS,
        descriptors: Callable[[Any], dict[str, Any]] | None = None,
        descriptor_keys: Iterable[str] = (),
        when: Callable[[Any, str], bool] | None = None,
        description: str | None = None,
    ) -> Signal:
        """Declare a signal for every save and delete of ``model`` — no ``emit`` in the mutations.

        A save that creates the row is CREATED, any other save UPDATED (so ``update_or_create``
        upserts are told apart for free), a delete DELETED; kinds not listed are not sent.
        ``organization(obj)`` names the organization (its slug); ``descriptors(obj)`` returns the
        flat descriptor dict, whose keys ``descriptor_keys`` declares; ``when(obj, kind)`` may veto
        (privacy, half-written rows).

        For saves, ``organization`` and ``descriptors`` run after the transaction commits, so they
        see everything the request wrote after the row itself — provided it wrote them in one
        transaction: outside one, "after the commit" is right after that save. A mutation whose
        descriptors depend on rows written after the object belongs in ``transaction.atomic``.
        For deletes they run at delete time, while the row still exists. The provenance token is always read at save time. A
        raising callable costs one warning: saving never fails because of a signal. Bulk writes
        (``bulk_create``, ``update()``) send nothing — Django sends no ``post_save`` for them.
        """
        from django.db.models.signals import post_delete, post_save

        handle = self.signal(identifier, kinds=kinds, descriptors=descriptor_keys, description=description)
        uid = f"rekuest_service:{self.name}:{identifier}"

        def on_save(sender: Any, instance: Any, created: bool = False, raw: bool = False, **_: Any) -> None:
            if not raw:  # fixtures being loaded are not events
                self._emit_for(handle, instance, "CREATED" if created else "UPDATED", organization, descriptors, when, lazy=True)

        def on_delete(sender: Any, instance: Any, **_: Any) -> None:
            self._emit_for(handle, instance, "DELETED", organization, descriptors, when, lazy=False)

        post_save.connect(on_save, sender=model, weak=False, dispatch_uid=f"{uid}:save")
        post_delete.connect(on_delete, sender=model, weak=False, dispatch_uid=f"{uid}:delete")
        return handle

    def _emit_for(self, handle: Signal, instance: Any, kind: str, organization: Callable, descriptors: Callable | None, when: Callable | None, *, lazy: bool) -> None:
        if kind not in handle.declaration.kinds or instance.pk is None or self.config() is None:
            return  # not announced, or nowhere to announce it: a save costs nothing extra
        try:
            if when is not None and not when(instance, kind):
                return
        except Exception as error:  # noqa: BLE001
            logger.warning("Signal %s: `when` failed for %s: %s", handle.identifier, instance.pk, error)
            return
        if lazy:
            self._emit(kind, handle.identifier, instance.pk, organization=lambda: organization(instance), descriptors=(lambda: descriptors(instance)) if descriptors else None)
            return
        try:
            org = organization(instance)
            values = descriptors(instance) if descriptors else None
        except Exception as error:  # noqa: BLE001
            logger.warning("Signal %s: could not describe %s %s: %s", handle.identifier, kind, instance.pk, error)
            return
        self._emit(kind, handle.identifier, instance.pk, organization=org, descriptors=values)

    def _emit(self, kind: str, identifier: str, object: Any, *, organization: str | Callable[[], str | None], descriptors: dict[str, Any] | Callable[[], dict[str, Any]] | None) -> None:
        """Queue one signal for after the commit. ``organization``/``descriptors`` may be callables,
        evaluated then (see :meth:`model_signal`)."""
        from rekuest_service.signals import current_provenance_token, enqueue

        config = self.config()
        service = self.service_name()
        if config is None or not service:
            return
        if identifier not in self._signals and (identifier, kind) not in self._warned_undeclared:
            self._warned_undeclared.add((identifier, kind))
            logger.warning("Emitting %s %s without declaring it (service.signal); triggers cannot be checked against it", kind, identifier)
        base = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "identifier": identifier,
            "object": str(object),
            # Read now, while the request that caused the object is still the current context.
            "provenance": current_provenance_token(),
            "occurred_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        issuer = self.signing_identifier()
        key = self.signing_key()

        def dispatch() -> None:
            try:
                org = organization() if callable(organization) else organization
                values = descriptors() if callable(descriptors) else descriptors
            except Exception as error:  # noqa: BLE001  a signal never breaks the write it describes
                logger.warning("Signal %s: could not describe %s %s: %s", identifier, kind, object, error)
                return
            if not org:
                logger.debug("Signal %s %s %s has no organization; not sent", kind, identifier, object)
                return
            enqueue(config, service, issuer, {**base, "organization": org, "descriptors": values or {}}, key)

        transaction.on_commit(dispatch)

    def __repr__(self) -> str:
        return f"Service({self.name!r}, actions={list(self._actions)}, signals={list(self._signals)})"


#: The service the module-level helpers (``rekuest_service.action``, ``declare_signal``, ``emit``,
#: ``rekuest_service.views.urlpatterns``) register on. Kept so existing callers work; a service
#: declares itself with its own ``Service(...)``.
default_service = Service(None)

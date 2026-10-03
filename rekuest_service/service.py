"""A service as the hub's rekuest sees it: the structures it hosts and the signals it emits.

    from rekuest_service import Descriptor, Service, organization_of

    service = Service("mikro", description="Microscopy data")

    # A model the service hosts: the descriptors of its objects, and a signal for every save and delete.
    service.structure(ArrayDataset, "@mikro/arraydataset", organization=organization_of(),
                      descriptors=ARRAY_DESCRIPTORS, describe=array_descriptors)

    # An event that is not a model's save or delete:
    dataset_created = service.signal("@mikro/arraydataset", kinds=["CREATED"], descriptors=ARRAY_DESCRIPTOR_KEYS)
    dataset_created.emit(dataset.pk, organization=dataset.organization.slug, descriptors={...})

and in ``urls.py``: ``urlpatterns = [..., *service.urls]``. The endpoints are bound to THIS
service — no module-level registry is consulted — so what the manifest lists is exactly what the
declaration says. rekuest reads that manifest (``GET <hook_url>/manifest``) when it provisions
the service: structures become its catalog of what this service hosts, signals become the
declarations triggers are checked against.

A service declares no actions. Work rekuest can ask of it is offered by its HookAgent
(:mod:`rekuest_service.agent`): an agent like any app's, reached over HTTP, whose actions take
and return this service's structures.

The name is what rekuest knows the service by (``rekuest.service_agents[].service``); a
``SERVICE`` in ``settings.REKUEST_HOOK`` overrides it, e.g. for a second instance of one service.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

from rekuest_service.structures import Descriptor, Structure, check_identifier

logger = logging.getLogger(__name__)

KINDS = ("CREATED", "UPDATED", "DELETED")

#: 2: the manifest also lists ``structures``. A reader that does not find the key is looking at
#: an older service and must not read its absence as "hosts nothing".
MANIFEST_VERSION = 2


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
    """One service's declaration towards its hub's rekuest: what it hosts, what it announces.

    A service declares structures and signals, hub-wide facts about its data. It declares no
    actions: work rekuest can ask for belongs to the service's :class:`~rekuest_service.HookAgent`.
    """

    def __init__(self, name: str | None, *, identifier: str | None = None, description: str | None = None, key: Any = None) -> None:
        self.name = name
        #: The key this service signs with; None (the rule) = this instance's key
        #: (``settings.INSTANCE``). Set when one process plays several services (tests).
        self.key = key
        #: The fakts identifier this instance signs as (``iss``) — what the coord's trust bundle
        #: lists its key under. Defaults to ``live.arkitekt.<name>``.
        self.identifier = identifier or (f"live.arkitekt.{name}" if name else None)
        self.description = description
        #: The service's HookAgent, once one is declared (``HookAgent(service)``).
        self.hook_agent: Any = None
        self._signals: dict[str, Signal] = {}
        self._structures: dict[str, Structure] = {}
        self._warned_undeclared: set[tuple[str, str]] = set()

    # --- declaring -----------------------------------------------------------------------

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
    def signals(self) -> dict[str, Signal]:
        return dict(self._signals)

    @property
    def structures(self) -> dict[str, Structure]:
        return dict(self._structures)

    def structure_for(self, target: Any) -> Structure | None:
        """The structure declared under an identifier, or for a model (class or instance)."""
        if isinstance(target, str):
            return self._structures.get(target)
        cls = target if isinstance(target, type) else type(target)
        for declared in self._structures.values():
            if declared.model is cls:
                return declared
        return next((d for d in self._structures.values() if isinstance(d.model, type) and issubclass(cls, d.model)), None)

    def describe(self, obj: Any) -> dict[str, Any]:
        """The descriptors of ``obj`` as its structure declares them; ``{}`` for a model no structure hosts."""
        declared = self.structure_for(obj)
        return declared.describe(obj) if declared is not None else {}

    def manifest(self) -> dict[str, Any]:
        return {
            "service": self.service_name(),
            "identifier": self.signing_identifier(),
            "description": self.description,
            # Not the service's: its HookAgent's, when it has one. One document, so rekuest reads both in one request.
            "actions": self.hook_agent.manifest() if self.hook_agent is not None else [],
            "signals": [s.declaration.manifest() for s in self._signals.values()],
            "structures": [s.manifest() for s in self._structures.values()],
            "manifest_version": MANIFEST_VERSION,
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

    def structure(
        self,
        model: Any,
        identifier: str,
        *,
        organization: Callable[[Any], str | None] | None = None,
        descriptors: Iterable[Descriptor | str] = (),
        describe: Callable[[Any], dict[str, Any]] | None = None,
        label: str | None = None,
        description: str | None = None,
        kinds: Iterable[str] = KINDS,
        when: Callable[[Any, str], bool] | None = None,
        signal_descriptors: Iterable[str] = (),
        signal_description: str | None = None,
    ) -> Structure:
        """Declare that this service hosts ``model`` as the structure ``identifier``.

        ``descriptors`` lists the descriptors its objects carry (a bare key is an untyped
        :class:`Descriptor`); ``describe(obj)`` computes them, as a flat ``{key: value}``. Both
        are said here once: the signals, the manifest and :meth:`describe` read this declaration.

        Every save and delete of ``model`` is signalled — no ``emit`` in the mutations. A save
        that creates the row is CREATED, any other save UPDATED (so ``update_or_create`` upserts
        are told apart for free), a delete DELETED; kinds not listed are not sent, and
        ``kinds=()`` hosts the structure without ever signalling it. ``organization(obj)`` names
        the organization (its slug) and is needed to signal; ``when(obj, kind)`` may veto
        (privacy, half-written rows). ``signal_descriptors`` are further keys only a hand-made
        ``emit`` carries: facts about the event, not the object.

        For saves, ``organization`` and ``describe`` run after the transaction commits, so they
        see everything the request wrote after the row itself — provided it wrote them in one
        transaction: outside one, "after the commit" is right after that save. A mutation whose
        descriptors depend on rows written after the object belongs in ``transaction.atomic``.
        For deletes they run at delete time, while the row still exists. The provenance token is always read at save time. A
        raising callable costs one warning: saving never fails because of a signal. Bulk writes
        (``bulk_create``, ``update()``) send nothing — Django sends no ``post_save`` for them.
        """
        from django.db.models.signals import post_delete, post_save

        check_identifier(identifier)
        kinds = tuple(kinds)
        declared_descriptors = tuple(d if isinstance(d, Descriptor) else Descriptor(d) for d in descriptors)
        if len({d.key for d in declared_descriptors}) != len(declared_descriptors):
            raise ValueError(f"The structure {identifier!r} declares a descriptor key twice")
        if kinds and organization is None:
            raise ValueError(f"The structure {identifier!r} is signalled ({kinds}); say which organization an object belongs to")
        if label is None and hasattr(model, "_meta"):
            label = str(model._meta.verbose_name).title()

        handle = None
        if kinds:
            keys = (*(d.key for d in declared_descriptors), *signal_descriptors)
            handle = self.signal(identifier, kinds=kinds, descriptors=keys, description=signal_description)
        declared = Structure(identifier, model, label, description, declared_descriptors, describe, organization, kinds, handle)
        existing = self._structures.get(identifier)
        if existing is not None:
            if existing.model is not model or existing.manifest() != declared.manifest() or existing.kinds != kinds:
                raise ValueError(f"The structure {identifier!r} is declared twice, differently")
            return existing
        other = next((s for s in self._structures.values() if s.model is model), None)
        if other is not None:
            raise ValueError(f"{model.__name__} is already hosted as {other.identifier!r}; a model is one structure")
        self._structures[identifier] = declared
        if handle is None:
            return declared

        uid = f"rekuest_service:{self.name}:{identifier}"

        def on_save(sender: Any, instance: Any, created: bool = False, raw: bool = False, **_: Any) -> None:
            if not raw:  # fixtures being loaded are not events
                self._emit_for(handle, instance, "CREATED" if created else "UPDATED", organization, describe, when, lazy=True)

        def on_delete(sender: Any, instance: Any, **_: Any) -> None:
            self._emit_for(handle, instance, "DELETED", organization, describe, when, lazy=False)

        post_save.connect(on_save, sender=model, weak=False, dispatch_uid=f"{uid}:save")
        post_delete.connect(on_delete, sender=model, weak=False, dispatch_uid=f"{uid}:delete")
        return declared

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
        """:meth:`structure`, as it was spelled before structures were declared; returns the signal handle."""
        declared = self.structure(
            model, identifier, organization=organization, kinds=kinds, descriptors=descriptor_keys, describe=descriptors, when=when, signal_description=description
        )
        return declared.signal

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
        return f"Service({self.name!r}, structures={list(self._structures)}, signals={list(self._signals)})"


#: The service the module-level helpers (``rekuest_service.declare_signal``, ``emit``,
#: ``rekuest_service.views.urlpatterns``) register on. Kept so existing callers work; a service
#: declares itself with its own ``Service(...)``.
default_service = Service(None)

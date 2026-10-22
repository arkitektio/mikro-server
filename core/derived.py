"""Columns a container derives from its coordinate anchors.

Anchors are where metadata is true: each spoke states a fact at some coordinates of one
container. A list or a filter over containers should not join through anchor, spoke and JSON to
ask "when was this acquired?", so a container may also keep a fact that holds across all of it
on a column of its own. The column says how it is computed, where it is declared::

    acquisition_start = DerivedDateTimeField(deriver=acquisition.start, db_index=True)

A deriver reads the container's anchors and returns a verdict, not just a value, because a
cached column must stay *true*. "Nobody said", "they say different things" and "what they say
cannot all be true" all leave the column null, but they are not the same thing:

- :class:`Stated`: every anchor that states the fact is consistent with the others. Written.
- :class:`Unstated`: no anchor states it.
- :class:`Ambiguous`: anchors that do not cover each other disagree -- channel 0 and channel 1
  name different detectors. A dataset may legitimately vary along an axis, so this is no error.
- :class:`Conflict`: an anchor that holds over another contradicts it -- the whole dataset says
  it was acquired from 10:00 to 10:05 and its frame 3 says 11:00. Nonsensical metadata: it is
  recorded as a ``DerivationConflict`` row so it can be found and fixed.

"Holds over" is :func:`covers`: an anchor omitting an axis holds along all of it, so ``{}``
covers ``{"t": 3}``, which covers ``{"t": 3, "c": 0}``. :func:`envelope` and :func:`agreement`
apply that one rule to ranges and to single values, so a deriver is a few lines.

The columns follow the anchors on their own: every spoke model a deriver names as a source
re-derives its container when saved or deleted (``core/signals.py``). A writer stating many
anchors at once wraps them in :func:`deferred`, so the container is derived once rather than once
per spoke. A bulk write sends no signal, so its writer calls :func:`rederive`, as it calls
``descriptors.stamp``.
"""

from __future__ import annotations

import datetime
import threading
from collections.abc import Callable, Hashable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db import models


# --- verdicts -----------------------------------------------------------------------


@dataclass(frozen=True)
class Stated[T]:
    """Every anchor that states the fact is consistent: the column holds ``value``."""

    value: T


@dataclass(frozen=True)
class Unstated:
    """No anchor states the fact."""


@dataclass(frozen=True)
class Ambiguous:
    """Anchors that do not cover each other disagree: true of parts, of the whole nothing."""

    reason: str


@dataclass(frozen=True)
class Conflict:
    """An anchor contradicts one it holds over: nonsensical metadata, recorded."""

    reason: str
    anchors: tuple[int, ...]


type Derivation[T] = Stated[T] | Unstated | Ambiguous | Conflict

UNSTATED = Unstated()


def project[T, U](derivation: Derivation[T], read: Callable[[T], U]) -> Derivation[U]:
    """Read one part of a stated value; any other verdict passes through unchanged."""
    return Stated(read(derivation.value)) if isinstance(derivation, Stated) else derivation


# --- what the anchors say -----------------------------------------------------------


@dataclass(frozen=True)
class Statement[T]:
    """One anchor's word on a fact: where it holds, and what it says."""

    anchor: int
    coordinates: dict[str, Any]
    value: T


def covers(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    """Whether an anchor at ``outer`` holds over one at ``inner``: it pins a subset of the same axes to the same values."""
    return outer != inner and outer.items() <= inner.items()


def _where(coordinates: dict[str, Any]) -> str:
    return "the whole container" if not coordinates else ", ".join(f"{axis}={value}" for axis, value in sorted(coordinates.items()))


def _covering_pairs[T](statements: list[Statement[T]]) -> Iterable[tuple[Statement[T], Statement[T]]]:
    for outer in statements:
        for inner in statements:
            if covers(outer.coordinates, inner.coordinates):
                yield outer, inner


def _conflict(found: list[tuple[str, Statement[Any], Statement[Any]]]) -> Conflict:
    anchors = sorted({statement.anchor for _, outer, inner in found for statement in (outer, inner)})
    return Conflict("; ".join(reason for reason, _, _ in found), tuple(anchors))


def envelope[T: (datetime.datetime, float, int)](statements: list[Statement[tuple[T, T]]]) -> Derivation[tuple[T, T]]:
    """The span every ``(low, high)`` statement falls in.

    True of the container whenever the statements are, so siblings never make it ambiguous. A
    range an anchor states must lie inside the range of every anchor that holds over it.
    """
    if not statements:
        return UNSTATED
    found = [
        (f"{_where(inner.coordinates)} states {inner.value[0]}..{inner.value[1]}, outside {outer.value[0]}..{outer.value[1]} stated for {_where(outer.coordinates)}", outer, inner)
        for outer, inner in _covering_pairs(statements)
        if inner.value[0] < outer.value[0] or inner.value[1] > outer.value[1]
    ]
    if found:
        return _conflict(found)
    return Stated((min(statement.value[0] for statement in statements), max(statement.value[1] for statement in statements)))


def agreement[T: Hashable](statements: list[Statement[T]]) -> Derivation[T]:
    """The one value every statement gives.

    A value an anchor states must equal the value of every anchor that holds over it; siblings
    that differ leave the container without one value, which is ambiguous, not wrong.
    """
    if not statements:
        return UNSTATED
    found = [
        (f"{_where(inner.coordinates)} states {inner.value!r}, {_where(outer.coordinates)} states {outer.value!r}", outer, inner)
        for outer, inner in _covering_pairs(statements)
        if inner.value != outer.value
    ]
    if found:
        return _conflict(found)
    values = {statement.value for statement in statements}
    if len(values) > 1:
        return Ambiguous(f"{len(values)} different values: {', '.join(sorted(repr(value) for value in values))}")
    return Stated(values.pop())


class AnchorSet:
    """A container's anchors, as derivers read them: each source spoke loaded once, each shared computation memoised."""

    def __init__(self, container: models.Model) -> None:
        self.container = container
        self._spokes: dict[str, list[models.Model]] = {}
        self._memo: dict[Hashable, Any] = {}

    def spokes(self, label: str) -> list[models.Model]:
        """Every spoke of the model ``label`` hanging off this container's anchors, with its anchor."""
        if label not in self._spokes:
            field = container_field(type(self.container))
            model = apps.get_model(label)
            self._spokes[label] = list(model.objects.filter(**{f"anchor__{field}": self.container.pk}).select_related("anchor").order_by("anchor_id"))
        return self._spokes[label]

    def statements[T](self, label: str, read: Callable[[Any], T | None]) -> list[Statement[T]]:
        """What each anchor states through a spoke of ``label``; a spoke ``read`` returns None for states nothing."""
        statements = []
        for spoke in self.spokes(label):
            value = read(spoke)
            if value is not None:
                statements.append(Statement(spoke.anchor_id, spoke.anchor.coordinates, value))
        return statements

    def memo[T](self, key: Hashable, compute: Callable[[], T]) -> T:
        """Compute once per set: the start and the end of a window share one check."""
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]


def container_field(model: type[models.Model]) -> str:
    """The foreign key of ``CoordinateAnchor`` that pins an anchor into a container of this model."""
    anchor = apps.get_model("core", "CoordinateAnchor")
    for field in anchor._meta.get_fields():
        if isinstance(field, models.ForeignKey) and field.related_model is model:
            return field.name
    raise TypeError(f"{model.__name__} has no coordinate anchors to derive from")


# --- derivers -------------------------------------------------------------------------


class Deriver[T]:
    """A function computing a column from a container's anchors, and the spoke models it reads."""

    def __init__(self, function: Callable[[AnchorSet], Derivation[T]], sources: tuple[str, ...]) -> None:
        self.function = function
        self.sources = sources
        self.path = f"{function.__module__}.{function.__qualname__}"
        self.summary = (function.__doc__ or "").strip().split("\n")[0]

    def __call__(self, anchors: AnchorSet) -> Derivation[T]:
        return self.function(anchors)

    def __repr__(self) -> str:
        return f"<deriver {self.path}>"


#: The spoke models any deriver reads, as ``app.Model`` labels: what ``core/signals.py`` listens to.
SOURCES: set[str] = set()


def deriver[T](*, sources: tuple[str, ...]) -> Callable[[Callable[[AnchorSet], Derivation[T]]], Deriver[T]]:
    """Declare a deriver, and the spoke models (``app.Model`` labels) whose writes change what it returns."""

    def wrap(function: Callable[[AnchorSet], Derivation[T]]) -> Deriver[T]:
        SOURCES.update(sources)
        return Deriver(function, sources)

    return wrap


# --- fields ---------------------------------------------------------------------------


class DerivedField:
    """A column written only by :func:`rederive`, from the deriver it names.

    Always nullable -- null is what every verdict but :class:`Stated` writes -- and never
    editable. A migration sees the plain Django field, without the deriver or the help text it
    is described by, so moving or rewording a deriver changes no schema.
    """

    plain: type[models.Field]

    def __init__(self, *args: Any, deriver: Deriver[Any], **kwargs: Any) -> None:
        self.deriver = deriver
        kwargs.setdefault("help_text", f"Derived from the coordinate anchors by {deriver.path}: {deriver.summary} Null when no anchor states it, or when they disagree")
        kwargs.update(null=True, blank=True, editable=False)
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        name, _, args, kwargs = super().deconstruct()
        kwargs.pop("help_text", None)
        return name, f"django.db.models.{self.plain.__name__}", args, kwargs

    def clone(self) -> models.Field:
        # The migration state's copy of the field, built from `deconstruct`: the plain field.
        _, _, args, kwargs = self.deconstruct()
        return self.plain(*args, **kwargs)


class DerivedDateTimeField(DerivedField, models.DateTimeField):
    plain = models.DateTimeField


class DerivedCharField(DerivedField, models.CharField):
    plain = models.CharField


class DerivedFloatField(DerivedField, models.FloatField):
    plain = models.FloatField


def derived_fields(model: type[models.Model]) -> list[DerivedField]:
    """The derived columns of a model, in declaration order."""
    return [field for field in model._meta.concrete_fields if isinstance(field, DerivedField)]


def rederive(container: models.Model) -> None:
    """Derive every derived column of ``container`` again, and record the conflicts found.

    Written with ``update()``: no save, so no history row and no descriptors -- the columns are
    the server's, not an edit. The instance is updated too, so a mutation returning it answers
    with what was written. A container with derived columns keeps its conflicts as
    ``derivation_conflicts``.
    """
    fields = derived_fields(type(container))
    if not fields:
        return
    anchors = AnchorSet(container)
    values: dict[str, Any] = {}
    conflicts = []
    record = container.derivation_conflicts
    for field in fields:
        verdict = field.deriver(anchors)
        values[field.attname] = verdict.value if isinstance(verdict, Stated) else None
        if isinstance(verdict, Conflict):
            conflicts.append(record.model(field=field.name, reason=verdict.reason, anchors=list(verdict.anchors), **{record.field.name: container}))
    type(container).objects.filter(pk=container.pk).update(**values)
    for attname, value in values.items():
        setattr(container, attname, value)
    record.all().delete()
    record.model.objects.bulk_create(conflicts)


# --- deriving once per batch of writes ---------------------------------------------------

_batch = threading.local()


@contextmanager
def deferred() -> Iterator[None]:
    """Derive every container whose anchors were written inside the block once, as it closes.

    Ingest states an anchor per timepoint: deriving after each would read every window again
    for each one written. Nested blocks derive at the outermost close. A block left by an
    exception derives nothing -- the transaction it is part of is rolling back.
    """
    depth = getattr(_batch, "depth", 0)
    if depth == 0:
        _batch.pending = {}
    _batch.depth = depth + 1
    try:
        yield
    except BaseException:
        if depth == 0:
            _batch.pending = {}
        raise
    finally:
        _batch.depth = depth
    if depth == 0:
        pending, _batch.pending = _batch.pending, {}
        for container in pending.values():
            rederive(container)


def request(container: models.Model) -> None:
    """Derive ``container`` now, or when the enclosing :func:`deferred` block closes."""
    if getattr(_batch, "depth", 0):
        # Keyed by row: the first instance seen is the one derived, and written to.
        _batch.pending.setdefault((type(container), container.pk), container)
    else:
        rederive(container)

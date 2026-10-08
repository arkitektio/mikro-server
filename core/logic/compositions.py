"""What the data layer is told about the compositions laid out over it.

A composition -- a scene, a chart -- names data by id and adopts a space as its world. The
data layer never needs to know *which* compositions exist, but three of its lifecycle
decisions depend on them: a space some composition is laid out over must not be deleted,
must not be swept as an orphan, and a row a composition's settings name must not be deleted
out from under it.

Those used to be written as knowledge of scenes: ``system.scenes``, ``scenes__isnull=True``,
a ``guard=`` naming a function that queries layers. A second composition would have been
invisible to all three, and the failure is not a refusal -- it is a sweep that deletes a
space something is drawn over.

So a composition *registers* here, from its app's ``ready()``, and the data layer reads the
registry. ``core`` registers scenes through exactly the calls another app uses, which is the
point: nothing here knows one composition from another.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import models as django_models


@dataclass(frozen=True)
class Composition:
    """One kind of composition, as far as a world's lifecycle is concerned."""

    #: The composition model.
    model: type[django_models.Model]
    #: The reverse accessor its world FK puts on ``CoordinateSystem``.
    world_relation: str
    #: What a refusal calls one of them: "scene", "chart".
    noun: str


_COMPOSITIONS: list[Composition] = []

_DELETE_GUARDS: dict[type[django_models.Model], list[Callable[[django_models.Model], None]]] = {}


def register_composition(model: type[django_models.Model], *, world_relation: str, noun: str) -> None:
    """Declare a composition model and the relation by which a space knows it is its world.

    Idempotent, because ``ready()`` can run more than once in a process (the autoreloader,
    a test runner re-populating the app registry).
    """
    composition = Composition(model=model, world_relation=world_relation, noun=noun)
    if composition not in _COMPOSITIONS:
        _COMPOSITIONS.append(composition)


def compositions() -> tuple[Composition, ...]:
    """Every registered composition, in registration order."""
    return tuple(_COMPOSITIONS)


def no_composition_over() -> dict[str, bool]:
    """The filter matching the spaces no composition is laid out over.

    The composition half of "is this space garbage", beside ``graph._UNINHABITED`` which is
    the resident half. Read when the sweep runs, never at import, so an app registered after
    ``core`` was imported is still asked about.
    """
    return {f"{composition.world_relation}__isnull": True for composition in _COMPOSITIONS}


def assert_no_composition_over(system: django_models.Model) -> None:
    """Raise when some composition is laid out over this space, naming the first few.

    Every world FK is RESTRICT, so the database would refuse the delete anyway -- but it would
    refuse with an IntegrityError naming a constraint, and this names the compositions.
    """
    for composition in _COMPOSITIONS:
        laid_out = list(getattr(system, composition.world_relation).all()[:5])
        if laid_out:
            raise ValueError(
                f"Coordinate system {system.pk} is the world of {len(laid_out)} {composition.noun}(s) ({', '.join(str(row.pk) for row in laid_out)}) and cannot be deleted. "
                f"A shared space outlives the {composition.noun}s that adopt it; delete them first."
            )


def register_delete_guard(model: type[django_models.Model], guard: Callable[[django_models.Model], None]) -> None:
    """Declare a check that must pass before a row of ``model`` is deleted, whoever asks.

    The PROTECT half of a delete where the dependency is not a foreign key: a composition's
    settings name a row by id inside a JSON document, so no constraint can hold it. The guard
    raises to refuse. Idempotent for the same reason :func:`register_composition` is.
    """
    guards = _DELETE_GUARDS.setdefault(model, [])
    if guard not in guards:
        guards.append(guard)


def assert_deletable(item: django_models.Model) -> None:
    """Run every guard registered for this row's model."""
    for guard in _DELETE_GUARDS.get(type(item), ()):
        guard(item)

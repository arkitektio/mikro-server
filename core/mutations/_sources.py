"""The one way a layer mutation names what it draws: a lens, or the container whose whole lens is meant.

Every layer draws through a lens. A caller that has a lens names it; one that has the
container names that, and gets the container's whole lens -- the row that has existed since
the container was made. Exactly one of the two, and the lens must be of the kind the layer
draws: a point layer refuses an array lens by name rather than by a cascade of attribute errors.
"""

from __future__ import annotations

from kante.types import Info

from core import models
from core.logic import coordinate_system as coordinate_system_logic
from core.scoping import get_for_org


def resolve_source_lens(
    info: Info,
    *,
    kind: type[models.Lens],
    lens: str | None,
    container_model: type,
    container: str | None,
    what: str,
    role: str = "this layer draws through",
) -> models.Lens:
    """The lens a layer draws through -- or a selection is cut from -- from exactly one of ``lens`` and ``container``."""
    if (lens is None) == (container is None):
        raise ValueError(f"Name exactly one of `lens` (a {kind.KIND.lower()} lens) or `{what}` (the container, through its whole lens).")
    if lens is not None:
        if not kind.objects.filter(pk=lens).exists():
            found = models.Lens.objects.filter(pk=lens).first()
            if found is None:
                raise ValueError(f"Lens {lens} does not exist.")
            raise ValueError(f"Lens {lens} is a {type(found).KIND.lower()} lens; {role} a {kind.KIND.lower()} lens. Pass a lens over a {what}, or the {what} itself.")
        return get_for_org(kind, info, id=lens)
    return coordinate_system_logic.whole_lens(get_for_org(container_model, info, id=container))

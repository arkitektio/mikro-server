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
from core.scoping import for_org, get_for_org


def lens_of_kind(info: Info, *, kind: type[models.Lens], lens: str, role: str = "this layer draws through") -> models.Lens:
    """The lens of one kind a layer draws through, refused in prose when the id names a lens of another kind."""
    # Asked of the caller's organization only: another organization's lens does not exist here,
    # whatever kind it is.
    if not for_org(kind, info).filter(pk=lens).exists():
        found = for_org(models.Lens, info).filter(pk=lens).first()
        if found is None:
            raise ValueError(f"Lens {lens} does not exist.")
        raise ValueError(f"Lens {lens} is a {type(found).KIND.lower()} lens; {role} a {kind.KIND.lower()} lens. Pass a {kind.KIND.lower()} lens -- a container's whole lens is its `fullLens`.")
    return get_for_org(kind, info, id=lens)


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
    """The lens a selection is cut from, from exactly one of ``lens`` and ``container``: what the create-lens mutations take, where a container is where a selection is first named."""
    if (lens is None) == (container is None):
        raise ValueError(f"Name exactly one of `lens` (a {kind.KIND.lower()} lens) or `{what}` (the container, through its whole lens).")
    if lens is not None:
        return lens_of_kind(info, kind=kind, lens=lens, role=role)
    return coordinate_system_logic.whole_lens(get_for_org(container_model, info, id=container))

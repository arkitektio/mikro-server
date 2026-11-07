"""What a scene and a chart have in common, as far as a client holding either can tell.

The GraphQL face of what :mod:`core.logic.compositions` calls a composition: something that
draws data through lenses, naming it by id and owning none, laid out over a world it adopts: a
scene's place, an AXIS chart's one axis, a VALUES chart's rows. It lives here because
``Scene`` implements it and ``core`` imports no other app; it names no implementer.
"""

import strawberry
from strawberry.scalars import JSON

from core.types._shared import DESCRIPTORS_DESCRIPTION
from core.types.coords import CoordinateSystem


@strawberry.interface(
    description=(
        "A composition: something that draws data through lenses, names it by id and owns none of it. A **scene** shows *where* -- every screen axis is an axis of its "
        "world, data is placed there by the graph's edges, and values are drawn as appearance (colour, size). A **chart** shows *how much* -- values are drawn as "
        "position, and at most one axis is drawn from its world: an AXIS chart draws its one axis (intensity against `t`), a VALUES chart draws its rows at the values of "
        "their columns (nuclei at `area` against `volume`), its world saying whose rows. Only what every kind has is here -- the layers differ per kind, so select them on the concrete type"
    )
)
class Visualization:
    """A scene or a chart: a composition over a world."""

    id: strawberry.ID
    name: str
    descriptors: JSON = strawberry.field(description=DESCRIPTORS_DESCRIPTION)
    world_coordinate_system: CoordinateSystem = strawberry.field(
        description="The space this visualization is laid out over: a scene's place, an AXIS chart's one axis, a VALUES chart's rows (a table's space, not drawn). Never owned by it: several can share the space, it outlives each of them, and deleting one never deletes it"
    )

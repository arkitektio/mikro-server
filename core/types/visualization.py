"""What a scene and a chart have in common, as far as a client holding either can tell.

The GraphQL face of what :mod:`core.logic.compositions` calls a composition: something laid
out over a space it adopts as its world, naming data by id and owning none. It lives here
because ``Scene`` implements it and ``core`` imports no other app; it names no implementer.
"""

import strawberry
from strawberry.scalars import JSON

from core.types._shared import DESCRIPTORS_DESCRIPTION
from core.types.coords import CoordinateSystem


@strawberry.interface(
    description=(
        "A composition laid out over a coordinate system: a scene (a place) or a chart (one metric axis). It adopts the space as its world, names data by id and owns "
        "none of it. Only what every kind has is here -- the layers differ per kind, so select them on the concrete type"
    )
)
class Visualization:
    """A scene or a chart: a composition over a world."""

    id: strawberry.ID
    name: str
    descriptors: JSON = strawberry.field(description=DESCRIPTORS_DESCRIPTION)
    world_coordinate_system: CoordinateSystem = strawberry.field(
        description="The space this visualization is laid out over. Never owned by it: several can share the space, it outlives each of them, and deleting one never deletes it"
    )

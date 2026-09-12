"""GraphQL input type for a camera pose, mirrored off the same pydantic model."""

import strawberry
from strawberry.experimental import pydantic

from core import scalars
from core.input_unions import prose_errors
from core.render.camera import models


# Decorated in its own right even though its only caller today is `AnimationWaypointInput`,
# which is wrapped too: a pose is a reusable input, and the first mutation to take one at
# the top level would otherwise surface a raw pydantic report. The wrapper only catches a
# `ValidationError`, so nesting two of them changes nothing.
@prose_errors
@pydantic.input(models.CameraStateModel, description="Where a viewer's camera is in a scene, and how it is looking at it. Give the flat view, the volumetric view, or both -- one pose serves either, and `Scene.preferredView` picks which a viewer opens. Every number is read against the scene's world coordinate system, whose axes carry the units, so they are bare numbers here")
class CameraStateInput:
    """Where a viewer's camera is in a scene, and how it is looking at it."""

    position: scalars.Any = strawberry.field(description="Where the camera is centred, keyed by the world's axis names, e.g. {'t': 4, 'z': 12.5, 'y': 300, 'x': 220}. Every key must name an axis of the scene's world. Omit an axis to leave the viewer wherever it already was along it")
    cross_section_orientation: list[float] | None = strawberry.field(default=None, description="The flat view's orientation, as a quaternion (x, y, z, w). Omit to leave it to the viewer")
    cross_section_scale: float | None = strawberry.field(default=None, description="The flat view's zoom, in world units per screen pixel, so greater than zero. Omit to leave it to the viewer")
    projection_orientation: list[float] | None = strawberry.field(default=None, description="The volumetric view's orientation, as a quaternion (x, y, z, w). Omit to leave it to the viewer")
    projection_scale: float | None = strawberry.field(default=None, description="The volumetric view's zoom, in world units per screen pixel, so greater than zero. Omit to leave it to the viewer")

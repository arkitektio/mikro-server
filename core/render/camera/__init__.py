"""Typed models for where a viewer's camera is in a scene.

A camera pose as a composable pydantic model, mirrored into GraphQL input and
output types the same way the layer render graph and the Optikit state are. The
pose is stored on :class:`core.models.AnimationWaypoint.camera` as the model's
dump and reconstructed on read, so the JSON column never grows a shape the types
cannot express.

The shape is neuroglancer's, which the in-layer render graph already borrows
from: one position, and *two* views of it -- a flat cross-section and a
volumetric projection, each with its own orientation and scale. One pose
therefore serves both, and ``Scene.preferredView`` decides which a viewer opens.

Coordinates here are bare numbers, deliberately. They are read against the
scene's world coordinate system, whose axes carry the units -- the same rule as
``Annotation.vectors`` ("in the coordinate system's own units") and ``Layer``'s
widths ("in scene units"). Quantity-typing them, as the Optikit stage pose is
quantity-typed, would put a second copy of a unit next to the axis that already
owns it, free to disagree with it. Optikit is the other case: standalone
hardware truth, pinned to no system, with nothing else to describe its units.
"""

"""Concrete implementations of the polymorphic ``Layer`` GraphQL interface.

Subtypes that are only reachable through the ``Layer`` interface (e.g. via
``Scene.layers``) are not auto-discovered by strawberry; they must be registered
in the schema's ``types=`` list or they are dropped from the SDL. This mirrors
the ``layer_render_node_types`` registration in ``core/render/layer/constants.py``.
"""

from core import types


layer_types = [
    types.ImageLayer,
    types.IntensityLayer,
    types.RgbLayer,
    types.PhasorLayer,
    types.LabelLayer,
    types.AnnotationLayer,
    types.PointLayer,
    types.TrackLayer,
    types.MeshLayer,
    types.NetworkLayer,
    types.VectorLayer,
]

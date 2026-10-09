"""Concrete implementations of the polymorphic ``Lens`` GraphQL interface.

Subtypes only reachable through the ``Lens`` interface (``lenses``, ``lens(id)``, a layer's
``lens``) are not auto-discovered by strawberry; they must be registered in the schema's
``types=`` list or they are dropped from the SDL. The same arrangement as ``core/types/layers.py``.
"""

from core import types


lens_types = [
    types.ArrayLens,
    types.TableLens,
    types.SparseLens,
    types.MeshLens,
    types.NetworkLens,
    types.AnnotationLens,
]

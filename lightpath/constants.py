"""The concrete element types the schema must register.

An `OpticalElement` subtype is referenced by no field -- the interface is what a query
selects -- so leaving one out of the schema's ``types=[...]`` erases it from the SDL
silently, and a graph containing that kind fails at resolution. The same failure mode as
the storage union: a parallel list of element kinds that nothing checks.

So this list is derived from :data:`lightpath.objects.models.ELEMENT_MODEL_BY_KIND` rather
than written beside it, and `test_lightpath_elements` asserts the mapping is total over
`ElementKind`. Adding a kind means adding its model and its type; forgetting either one
fails the suite instead of a client's query.
"""

from lightpath.objects import types
from lightpath.objects.models import ELEMENT_MODEL_BY_KIND

#: The strawberry type for each element model, keyed by the model it mirrors.
_TYPE_BY_MODEL = {
    types.ObjectiveElement._pydantic_type: types.ObjectiveElement,
    types.LensElement._pydantic_type: types.LensElement,
    types.MirrorElement._pydantic_type: types.MirrorElement,
    types.BeamSplitterElement._pydantic_type: types.BeamSplitterElement,
    types.DetectorElement._pydantic_type: types.DetectorElement,
    types.OtherSourceElement._pydantic_type: types.OtherSourceElement,
    types.LaserElement._pydantic_type: types.LaserElement,
    types.LampElement._pydantic_type: types.LampElement,
    types.SampleElement._pydantic_type: types.SampleElement,
    types.CCDElement._pydantic_type: types.CCDElement,
    types.OtherElement._pydantic_type: types.OtherElement,
    types.FilterElement._pydantic_type: types.FilterElement,
    types.PinholeElement._pydantic_type: types.PinholeElement,
    types.ShutterElement._pydantic_type: types.ShutterElement,
    types.PolarizerElement._pydantic_type: types.PolarizerElement,
    types.WaveplateElement._pydantic_type: types.WaveplateElement,
    types.ApertureElement._pydantic_type: types.ApertureElement,
}

interface_types = [_TYPE_BY_MODEL[model] for model in ELEMENT_MODEL_BY_KIND.values()]

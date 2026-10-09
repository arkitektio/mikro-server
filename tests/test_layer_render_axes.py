"""`renderAxes` belongs to the view: every lens-backed layer kind answers it, and the lens does not.

Which axis faces screen x, y, z, time and intensity is a convention of how data is looked
at, so it is a field of the layer. A lens is a selection and chooses nothing about how it
is looked at, so it carries no such field.
"""

import re

import pytest
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

RENDER_AXES = "renderAxes { x y z t intensity phasor vector }"

QUERY = f"""
query Layers($id: ID!) {{
  scene(id: $id) {{
    layers {{
      __typename
      ... on ImageLayer {{ {RENDER_AXES} }}
      ... on IntensityLayer {{ {RENDER_AXES} }}
      ... on RgbLayer {{ {RENDER_AXES} }}
      ... on PhasorLayer {{ {RENDER_AXES} }}
      ... on VectorLayer {{ {RENDER_AXES} }}
      ... on LabelLayer {{ {RENDER_AXES} }}
    }}
  }}
}}
"""

#: The axes each kind needs for its own slot of the mapping to be filled, and the mapping a
#: renderer must then be handed: a phasor layer wants a MICROTIME axis, a vector layer a
#: DISPLACEMENT axis, the rest a channel axis. Spatial axes named for the screen bind by name.
_CASES = {
    enums.LayerKindChoices.IMAGE.value: (seed.SIMPLE_AXES, [3, 64, 64], {"x": "x", "y": "y", "z": None, "t": None, "intensity": "c", "phasor": None, "vector": None}),
    enums.LayerKindChoices.INTENSITY.value: (seed.SIMPLE_AXES, [3, 64, 64], {"x": "x", "y": "y", "z": None, "t": None, "intensity": "c", "phasor": None, "vector": None}),
    enums.LayerKindChoices.RGB.value: (seed.SIMPLE_AXES, [3, 64, 64], {"x": "x", "y": "y", "z": None, "t": None, "intensity": "c", "phasor": None, "vector": None}),
    enums.LayerKindChoices.LABEL.value: (seed.SIMPLE_AXES, [3, 64, 64], {"x": "x", "y": "y", "z": None, "t": None, "intensity": "c", "phasor": None, "vector": None}),
    enums.LayerKindChoices.PHASOR.value: (
        [seed.axis("tau", enums.AxisType.MICROTIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        [8, 64, 64],
        {"x": "x", "y": "y", "z": None, "t": None, "intensity": None, "phasor": "tau", "vector": None},
    ),
    enums.LayerKindChoices.VECTOR.value: (
        [seed.axis("v", enums.AxisType.DISPLACEMENT), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        [2, 64, 64],
        {"x": "x", "y": "y", "z": None, "t": None, "intensity": None, "phasor": None, "vector": "v"},
    ),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_lens_backed_layer_answers_render_axes_from_its_lens_axes(authenticated_context: HttpContext):
    """One layer per lens-backed kind, each over a lens whose axes fill that kind's slot, each answering the mapping its axes imply."""
    assert set(_CASES) == set(enums.ARRAY_LAYER_KINDS), "an array-lens kind this test does not build a layer for"

    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Every kind")
    for order, (kind, (axes, shape, _)) in enumerate(_CASES.items()):
        dataset = await seed.create_array_dataset(ctx, kind, axes=axes, shapes=[shape])
        lens = await seed.create_lens(ctx, dataset)
        await models.Layer.objects.acreate(scene=scene, kind=kind, lens=lens, order=order)

    result = await schema.execute(QUERY, context_value=ctx, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    layers = result.data["scene"]["layers"]

    assert len(layers) == len(enums.ARRAY_LAYER_KINDS)
    expected_by_kind = {kind: expected for kind, (_, _, expected) in _CASES.items()}
    for layer in layers:
        kind = layer["__typename"].removesuffix("Layer").lower()
        assert layer["renderAxes"] == expected_by_kind[kind], layer["__typename"]


def test_the_lens_has_no_render_axes() -> None:
    """A selection does not choose how it is looked at: the field is the layer's alone."""
    printed = schema.as_str()
    lens_type = re.search(r"\n(?:type|interface) Lens\b[^{]*\{(.*?)\n\}", printed, re.S)
    assert lens_type, "no `Lens` type in the SDL"
    assert "renderAxes" not in lens_type.group(1)
    assert "renderAxes" in printed, "the layers still answer it"

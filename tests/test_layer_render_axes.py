"""`renderAxes` belongs to the view: every lens-backed layer kind answers it, and answers what its lens does.

Which axis faces screen x, y, z, time and intensity is a convention of how data is looked
at, so it is a field of the layer. The lens keeps answering, deprecated, until clients have
moved; the two must agree for as long as both exist.
"""

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
      ... on ImageLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
      ... on IntensityLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
      ... on RgbLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
      ... on PhasorLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
      ... on VectorLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
      ... on LabelLayer {{ {RENDER_AXES} lens {{ {RENDER_AXES} }} }}
    }}
  }}
}}
"""

#: The axes each kind needs for its own slot of the mapping to be filled: a phasor layer
#: wants a MICROTIME axis, a vector layer a DISPLACEMENT axis, the rest a channel axis.
_AXES_BY_KIND = {
    enums.LayerKindChoices.IMAGE.value: (seed.SIMPLE_AXES, [3, 64, 64]),
    enums.LayerKindChoices.INTENSITY.value: (seed.SIMPLE_AXES, [3, 64, 64]),
    enums.LayerKindChoices.RGB.value: (seed.SIMPLE_AXES, [3, 64, 64]),
    enums.LayerKindChoices.LABEL.value: (seed.SIMPLE_AXES, [3, 64, 64]),
    enums.LayerKindChoices.PHASOR.value: ([seed.axis("tau", enums.AxisType.MICROTIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)], [8, 64, 64]),
    enums.LayerKindChoices.VECTOR.value: ([seed.axis("v", enums.AxisType.DISPLACEMENT), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)], [2, 64, 64]),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_lens_backed_layer_answers_render_axes_as_its_lens_does(authenticated_context: HttpContext):
    """One layer per lens-backed kind, each over a lens whose axes fill that kind's slot, and `layer.renderAxes == layer.lens.renderAxes` for all of them."""
    assert set(_AXES_BY_KIND) == set(enums.LENS_BACKED_KINDS), "a lens-backed kind this test does not build a layer for"

    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Every kind")
    for order, (kind, (axes, shape)) in enumerate(_AXES_BY_KIND.items()):
        dataset = await seed.create_array_dataset(ctx, kind, axes=axes, shapes=[shape])
        lens = await seed.create_lens(ctx, dataset)
        await models.Layer.objects.acreate(scene=scene, kind=kind, lens=lens, order=order)

    result = await schema.execute(QUERY, context_value=ctx, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    layers = result.data["scene"]["layers"]

    assert len(layers) == len(enums.LENS_BACKED_KINDS)
    for layer in layers:
        assert layer["renderAxes"] == layer["lens"]["renderAxes"], layer["__typename"]
        assert layer["renderAxes"]["x"] == "x" and layer["renderAxes"]["y"] == "y"

    by_type = {layer["__typename"]: layer["renderAxes"] for layer in layers}
    assert by_type["IntensityLayer"]["intensity"] == "c"
    assert by_type["PhasorLayer"]["phasor"] == "tau"
    assert by_type["VectorLayer"]["vector"] == "v"


def test_the_lens_field_is_deprecated_in_favour_of_the_layers() -> None:
    """The schema says where to read it now; the value does not change until the field goes."""
    printed = schema.as_str()
    assert "@deprecated(reason: \"Read `renderAxes` on the layer" in printed

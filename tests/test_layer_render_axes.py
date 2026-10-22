"""`suggestedRenderAxes`: the server's convention for which lens axis faces which screen axis.

Which axis faces screen x, y, z is a convention of how data is looked at. A layer states
no such field: a viewer reads it off the layer's placement (`asAffine`) under its own view,
and the non-spatial roles off the lens' axis types. What the server still answers is the
convention itself, on the lens, for a viewer opening a lens nothing places yet -- and the
same rule decides what a layer kind may be created over.
"""

import pytest
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

RENDER_AXES = "lens { suggestedRenderAxes { x y z t intensity phasor vector } }"

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
async def test_every_lens_backed_layers_lens_suggests_render_axes_from_its_axes(authenticated_context: HttpContext):
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
        assert layer["lens"]["suggestedRenderAxes"] == expected_by_kind[kind], layer["__typename"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bare_lens_suggests_the_render_axes_a_layer_over_it_would_carry(authenticated_context: HttpContext):
    """Before any layer exists a viewer still needs a convention to open with: the lens suggests one from its axis types and names."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Bare", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    lens = await seed.create_lens(ctx, dataset)
    suggested = await schema.execute("query S($id: ID!) { lens(id: $id) { ... on ArrayLens { suggestedRenderAxes { x y z t intensity phasor vector } } } }", context_value=ctx, variable_values={"id": str(lens.pk)})
    assert not suggested.errors, suggested.errors
    assert suggested.data["lens"]["suggestedRenderAxes"] == {"x": "x", "y": "y", "z": None, "t": None, "intensity": "c", "phasor": None, "vector": None}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_no_longer_states_render_axes(authenticated_context: HttpContext):
    """Which axis faces the screen is the viewer's, read off the placement under its view: no layer kind carries it."""
    result = await schema.execute("query { scenes { layers { ... on ImageLayer { renderAxes { x } } } } }", context_value=authenticated_context)
    assert result.errors and "renderAxes" in result.errors[0].message

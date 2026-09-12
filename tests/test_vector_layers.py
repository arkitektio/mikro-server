"""The vector layer: a vector-valued lens drawn as glyphs.

What is under test is the value-domain split, not the drawing: a DISPLACEMENT axis's
positions are the components of one per-point offset, so the axis is read as geometry --
which axis that is is derived, never stored -- and none of the intensity vocabulary
survives. The refusals matter most: every one of them replaces a picture that would have
rendered without error and been wrong (a flow field drawn as a grey image, a 3-vector
flattened onto a plane).
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed


LAYER_FIELDS = """
    id
    glyph
    glyphStride
    glyphScale
    colormap
    color
    climMin
    climMax
    blending
    vectorAxis
"""

CREATE_LAYER = """
mutation Create($input: CreateVectorLayerInput!) {
  createVectorLayer(input: $input) {
    %s
  }
}
""" % LAYER_FIELDS

UPDATE_LAYER = """
mutation Update($input: UpdateVectorLayerInput!) {
  updateVectorLayer(input: $input) {
    %s
  }
}
""" % LAYER_FIELDS

CREATE_INTENSITY = """
mutation Create($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

UPDATE_INTENSITY = """
mutation Update($input: UpdateIntensityLayerInput!) {
  updateIntensityLayer(input: $input) { id }
}
"""

FROM_SYSTEM = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) {
    id
    layers { __typename ... on VectorLayer { glyph colormap vectorAxis } }
  }
}
"""


def _field(components: int, *, volumetric: bool) -> list:
    """A vector field's axes: a DISPLACEMENT value axis over a plane or a volume."""
    spatial = [seed.axis("z", enums.AxisType.SPACE)] if volumetric else []
    return [
        seed.axis("v", enums.AxisType.DISPLACEMENT),
        *spatial,
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ]


async def _flow_lens(ctx: HttpContext, *, components: int = 2, volumetric: bool = False, name: str = "Flow") -> models.Lens:
    shape = [components, 16, 32, 32] if volumetric else [components, 32, 32]
    dataset = await seed.create_array_dataset(ctx, name, axes=_field(components, volumetric=volumetric), shapes=[shape])
    return await seed.create_lens(ctx, dataset)


async def _scene_with(ctx: HttpContext, lens: models.Lens) -> models.Scene:
    scene = await seed.create_scene(ctx)
    await seed.register_into_scene(ctx, scene, lens.dataset)
    return scene


async def _create(ctx: HttpContext, lens: models.Lens, scene: models.Scene, **extra):
    variables = {"input": {"scene": str(scene.pk), "lens": str(lens.pk), **extra}}
    return await schema.execute(CREATE_LAYER, context_value=ctx, variable_values=variables)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_vector_layer_defaults_and_derived_axis(authenticated_context: HttpContext):
    """The defaults are the mutation's own, and the component axis is derived, not chosen.

    NORMAL, not ADDITIVE: glyphs are geometry over what is beneath them, and summing an
    arrow with the field under it draws brighter arrows where the data is bright.
    """
    lens = await _flow_lens(authenticated_context, components=3, volumetric=True)
    scene = await _scene_with(authenticated_context, lens)

    result = await _create(authenticated_context, lens, scene)
    assert not result.errors, result.errors

    layer = result.data["createVectorLayer"]
    assert layer["glyph"] == "ARROW"
    assert layer["colormap"] == "VIRIDIS"
    assert layer["blending"] == "NORMAL"
    assert layer["glyphStride"] is None, "null is the renderer's budget, not a stride of its own"
    assert layer["glyphScale"] is None, "null auto-normalizes"
    assert layer["vectorAxis"] == "v", "derived from the axis types, never taken as input"

    stored = await sync_to_async(models.Layer.objects.get)(pk=layer["id"])
    assert stored.kind == enums.LayerKindChoices.VECTOR.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_without_a_displacement_axis_is_refused(authenticated_context: HttpContext):
    """No DISPLACEMENT axis means no components to read: every drawn arrow would be invented."""
    dataset = await seed.create_array_dataset(authenticated_context, "Plain")  # (c, y, x)
    lens = await seed.create_lens(authenticated_context, dataset)
    scene = await _scene_with(authenticated_context, lens)

    result = await _create(authenticated_context, lens, scene)
    assert result.errors
    assert "DISPLACEMENT" in str(result.errors[0])
    assert "createIntensityLayer" in str(result.errors[0]), "the refusal names the mutation that does want this lens"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "components, volumetric, fragment",
    [
        (1, False, "scalar"),  # one component is a scalar wearing a value axis
        (4, True, "matrix or feature vector"),  # four is a jacobian, not an offset
        (3, False, "spatial axes"),  # a 3-vector on a plane has no third direction to draw along
    ],
)
async def test_undrawable_component_counts_are_refused(authenticated_context: HttpContext, components: int, volumetric: bool, fragment: str):
    """2 or 3 components, and no more components than spatial axes -- each refusal is a wrong picture avoided."""
    lens = await _flow_lens(authenticated_context, components=components, volumetric=volumetric)
    scene = await _scene_with(authenticated_context, lens)

    result = await _create(authenticated_context, lens, scene)
    assert result.errors
    assert fragment in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_intensity_layer_over_a_vector_lens_is_refused_by_default(authenticated_context: HttpContext):
    """The grey-layer gap, closed. With `intensityAxis` omitted, 'single-valued data' used to
    resolve and the field drew as one grey layer over its components -- wrong picture, no
    error, nothing pointing back. The explicit case was already refused by `assert_channel_axis`."""
    lens = await _flow_lens(authenticated_context, components=2)
    scene = await _scene_with(authenticated_context, lens)

    omitted = await schema.execute(CREATE_INTENSITY, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk)}})
    assert omitted.errors
    assert "createVectorLayer" in str(omitted.errors[0]), "the refusal names the mutation that reads the axis as geometry"

    explicit = await schema.execute(CREATE_INTENSITY, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": "v"}})
    assert explicit.errors
    assert "CHANNEL" in str(explicit.errors[0]), "naming the vector axis as intensity keeps its older refusal"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_patches_and_checks_the_pair(authenticated_context: HttpContext):
    """Every field is a patch, and the clim pair is checked as the pair the row will hold."""
    lens = await _flow_lens(authenticated_context, components=2)
    scene = await _scene_with(authenticated_context, lens)
    created = await _create(authenticated_context, lens, scene, climMin=0.0, climMax=2.5)
    assert not created.errors, created.errors
    layer_id = created.data["createVectorLayer"]["id"]

    result = await schema.execute(UPDATE_LAYER, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "glyph": "LINE", "glyphStride": 4, "glyphScale": 1.5}})
    assert not result.errors, result.errors
    layer = result.data["updateVectorLayer"]
    assert layer["glyph"] == "LINE"
    assert layer["glyphStride"] == 4
    assert layer["glyphScale"] == 1.5
    assert layer["climMax"] == 2.5, "what is not sent keeps its current value"

    crossed = await schema.execute(UPDATE_LAYER, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "climMin": 3.0}})
    assert crossed.errors, "a min sent alone is checked against the max it will sit beside"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_degenerate_stride_and_scale_are_refused(authenticated_context: HttpContext):
    """A stride of 0 samples nothing and a non-positive scale draws nothing or draws reversed."""
    lens = await _flow_lens(authenticated_context, components=2)
    scene = await _scene_with(authenticated_context, lens)

    for extra in ({"glyphStride": 0}, {"glyphScale": 0.0}, {"glyphScale": -1.0}):
        result = await _create(authenticated_context, lens, scene, **extra)
        assert result.errors, f"{extra} should be refused"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_kind_is_fixed_for_the_life_of_the_row(authenticated_context: HttpContext):
    """`updateIntensityLayer` on a vector layer is refused, naming `updateVectorLayer` -- and the
    other way round, so neither vocabulary can land on the other's columns."""
    lens = await _flow_lens(authenticated_context, components=2)
    scene = await _scene_with(authenticated_context, lens)
    created = await _create(authenticated_context, lens, scene)
    assert not created.errors, created.errors
    layer_id = created.data["createVectorLayer"]["id"]

    wrong = await schema.execute(UPDATE_INTENSITY, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "gamma": 2.0}})
    assert wrong.errors
    assert "updateVectorLayer" in str(wrong.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bootstrap_infers_a_vector_layer_from_the_displacement_axis(authenticated_context: HttpContext):
    """The DISPLACEMENT axis is authored evidence in LABEL's CATEGORIZED sense, so unlike RGB
    the bootstrap *does* infer from it -- one layer, no channels to peel, indistinguishable
    from one `createVectorLayer` authored."""
    dataset = await seed.create_array_dataset(authenticated_context, "FlowField", axes=_field(3, volumetric=True), shapes=[[3, 16, 32, 32]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await schema.execute(FROM_SYSTEM, context_value=authenticated_context, variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": {}}})
    assert not result.errors, result.errors

    layers = result.data["createSceneFromCoordinateSystem"]["layers"]
    assert [layer["__typename"] for layer in layers] == ["VectorLayer"], "one layer: the components are one offset, not signals to composite"
    assert layers[0] == {"__typename": "VectorLayer", "glyph": "ARROW", "colormap": "VIRIDIS", "vectorAxis": "v"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bootstrap_falls_through_when_the_field_is_not_drawable(authenticated_context: HttpContext):
    """A DISPLACEMENT axis `createVectorLayer` would refuse must not decide the recipe: the
    bootstrap falls through to the ordinary inference rather than materializing a layer no
    mutation can update into validity."""
    dataset = await seed.create_array_dataset(authenticated_context, "Jacobian", axes=_field(4, volumetric=True), shapes=[[4, 16, 32, 32]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await schema.execute(FROM_SYSTEM, context_value=authenticated_context, variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": {}}})
    assert not result.errors, result.errors
    typenames = {layer["__typename"] for layer in result.data["createSceneFromCoordinateSystem"]["layers"]}
    assert "VectorLayer" not in typenames

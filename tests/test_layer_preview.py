"""``previewLayer``: the layer a create would make, resolved and never written.

What it promises, and what these check: a preview resolves the placement fields exactly
as the saved layer would (same ``asAffine``, same ``placement``), it writes no row, it is
refused for the create's own reasons, and an image layer's graph-indexed lenses resolve
without a through-table row.
"""

import pytest

from core import enums, models
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed

_CYX = [
    seed.axis("c", enums.AxisType.CHANNEL),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

_FIELDS = """
    __typename
    id
    kind
    placement
    asAffine { matrix inputAxes outputAxes }
    pathToWorld { inverted }
"""

PREVIEW = f"""
    query Preview($input: PreviewLayerInput!) {{
        previewLayer(input: $input) {{ {_FIELDS} ... on IntensityLayer {{ colormap lens {{ id }} }} }}
    }}
"""

CREATE = f"""
    mutation Create($input: CreateIntensityLayerInput!) {{
        createIntensityLayer(input: $input) {{ {_FIELDS} ... on IntensityLayer {{ colormap lens {{ id }} }} }}
    }}
"""


async def _placed_channel(ctx: HttpContext):
    """A scene, and one channel of a lens its world reaches."""
    dataset = await seed.create_array_dataset(ctx, "Preview", axes=_CYX, shapes=[[3, 32, 32]])
    lens = await seed.create_lens(ctx, dataset)
    scene = await seed.create_scene(ctx)
    await seed.register_into_scene(ctx, scene, dataset)
    channel = await seed.channel_lens(ctx, lens, 1)
    return scene, lens, channel


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_preview_resolves_what_the_create_would_and_writes_nothing(db, authenticated_context: HttpContext):
    scene, _, channel = await _placed_channel(authenticated_context)
    intensity = {"scene": str(scene.id), "lens": str(channel.id), "colormap": "MAGMA"}

    before = await models.Layer.objects.acount()
    preview = await schema.execute(PREVIEW, context_value=authenticated_context, variable_values={"input": {"intensity": intensity}})
    assert not preview.errors, preview.errors
    assert await models.Layer.objects.acount() == before, "a preview writes no row"

    previewed = preview.data["previewLayer"]
    assert previewed["__typename"] == "IntensityLayer"
    assert int(previewed["id"]) < 0, "a preview's id names no row"
    assert previewed["asAffine"] is not None, "placed through the same graph a saved layer is"

    created = await schema.execute(CREATE, context_value=authenticated_context, variable_values={"input": intensity})
    assert not created.errors, created.errors
    saved = created.data["createIntensityLayer"]
    for field in ("__typename", "kind", "placement", "asAffine", "pathToWorld", "colormap", "lens"):
        assert previewed[field] == saved[field], field


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_preview_is_refused_for_the_creates_reasons(db, authenticated_context: HttpContext):
    _, _, channel = await _placed_channel(authenticated_context)
    # A scene whose world the lens does not reach: the create refuses it, so the preview does.
    elsewhere = await seed.create_scene(authenticated_context, "Elsewhere")
    result = await schema.execute(
        PREVIEW,
        context_value=authenticated_context,
        variable_values={"input": {"intensity": {"scene": str(elsewhere.id), "lens": str(channel.id)}}},
    )
    assert result.errors
    assert "createTransformation" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_preview_of_an_image_layer_resolves_its_graph_lenses(db, authenticated_context: HttpContext):
    scene, lens, channel = await _placed_channel(authenticated_context)
    query = f"""
        query Preview($input: PreviewLayerInput!) {{
            previewLayer(input: $input) {{ {_FIELDS} ... on ImageLayer {{ renderGraph {{ root {{ __typename }} }} }} }}
        }}
    """
    graph = {
        "root": {
            "kind": "blend",
            "children": [{"kind": "channel", "lens": str(channel.id), "transfer": {"colormap": "GREEN"}}],
        }
    }
    before = await models.Layer.objects.acount()
    result = await schema.execute(
        query,
        context_value=authenticated_context,
        variable_values={"input": {"image": {"scene": str(scene.id), "lens": str(lens.id), "renderGraph": graph}}},
    )
    assert not result.errors, result.errors
    assert result.data["previewLayer"]["__typename"] == "ImageLayer"
    assert await models.Layer.objects.acount() == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_preview_names_exactly_one_layer(db, authenticated_context: HttpContext):
    result = await schema.execute(PREVIEW, context_value=authenticated_context, variable_values={"input": {}})
    assert result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_preview_places_like_the_mesh_layer_it_would_make(db, authenticated_context: HttpContext):
    from tests.test_layer_kinds import _seed_mesh_collection

    ctx = authenticated_context
    scene = await seed.create_scene(ctx)
    collection, system = await _seed_mesh_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=system)
    mesh = {"scene": str(scene.id), "lens": await seed.whole_lens_id(models.MeshCollection, str(collection.id))}

    preview_query = f"""
        query Preview($input: PreviewLayerInput!) {{ previewLayer(input: $input) {{ {_FIELDS} ... on MeshLayer {{ wireframe lens {{ id }} }} }} }}
    """
    create_mutation = f"""
        mutation Create($input: CreateMeshLayerInput!) {{ createMeshLayer(input: $input) {{ {_FIELDS} ... on MeshLayer {{ wireframe lens {{ id }} }} }} }}
    """
    before = await models.Layer.objects.acount()
    preview = await schema.execute(preview_query, context_value=authenticated_context, variable_values={"input": {"mesh": mesh}})
    assert not preview.errors, preview.errors
    assert await models.Layer.objects.acount() == before
    created = await schema.execute(create_mutation, context_value=authenticated_context, variable_values={"input": mesh})
    assert not created.errors, created.errors

    previewed, saved = preview.data["previewLayer"], created.data["createMeshLayer"]
    assert previewed["__typename"] == "MeshLayer"
    for field in ("kind", "placement", "asAffine", "pathToWorld", "wireframe", "lens"):
        assert previewed[field] == saved[field], field

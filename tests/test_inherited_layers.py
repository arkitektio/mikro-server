"""Values derived IDENTICAL are drawn the way their first producer declared: the staged scene copies its layers."""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed
from tests.test_derived_datasets import _derive

FROM_LENS = """
mutation F($input: CreateSceneFromLensInput!) {
  createSceneFromLens(input: $input) {
    layers { __typename ... on RgbLayer { climMin climMax redLens { id } greenLens { id } blueLens { id } } }
  }
}
"""


async def _photograph(ctx: HttpContext, name: str, clim: tuple[float, float]) -> models.Lens:
    """A (c, y, x) dataset whose producer drew it as one RGB layer with blue and red swapped, and its whole lens."""
    dataset = await seed.create_array_dataset(ctx, name)
    whole = await seed.create_lens(ctx, dataset, slices=[])
    red, green, blue = [await seed.channel_lens(ctx, whole, index) for index in (2, 1, 0)]
    scene = await seed.create_scene(ctx, f"{name} scene")
    await models.Layer.objects.acreate(
        kind=enums.LayerKind.RGB, scene=scene, lens=whole, red_lens=red, green_lens=green, blue_lens=blue, clim_min=clim[0], clim_max=clim[1]
    )
    return whole


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stitch_of_photographs_is_staged_as_one_rgb_layer_with_their_mapping_and_both_windows(authenticated_context: HttpContext) -> None:
    """Shape alone never makes RGB; two IDENTICAL sources that were drawn as RGB do, mapping and windows included."""
    ctx = authenticated_context
    tile_a = await _photograph(ctx, "Tile A", (0.0, 200.0))
    tile_b = await _photograph(ctx, "Tile B", (10.0, 255.0))
    stitched = await _derive(
        ctx,
        "Stitched",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[
            {"kind": "LENS", "lens": str(tile.pk), "transform": {"kind": "IDENTITY"}, "valueRelation": "IDENTICAL"}
            for tile in (tile_a, tile_b)
        ],
    )
    assert not stitched.errors, stitched.errors
    lens_id = await seed.whole_lens_id(models.ArrayDataset, stitched.data["createArrayDataset"]["id"])

    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": lens_id}})
    assert not result.errors, result.errors
    (layer,) = result.data["createSceneFromLens"]["layers"]
    assert layer["__typename"] == "RgbLayer", "inherited, where inference alone would have made three intensity layers"
    assert (layer["climMin"], layer["climMax"]) == (0.0, 255.0), "the union of both tiles' windows"

    @sync_to_async
    def pinned(component: str) -> int:
        cut = models.ArrayLens.objects.get(pk=layer[component]["id"])
        (channel,) = [slice_.start for slice_ in cut.slices_list if slice_.axis == "c"]
        return channel

    assert [await pinned(c) for c in ("redLens", "greenLens", "blueLens")] == [2, 1, 0], "the producer's channel mapping, not red-green-blue by position"

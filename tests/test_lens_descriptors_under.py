"""A lens can be asked what its crop to a shape would provide, before anyone makes it.

`ArrayLens.descriptorsUnder(annotation:)` answers the descriptors `createLens(lens:, annotation:)`
would give the lens it returns -- the same cut (`annotation_cut_slices`), over an unsaved
lens -- so a client can offer the actions a crop fits and make the crop only when one runs.
"""

import pytest
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request
from tests.test_lens_geometry import _collection_on, _draw, _lens

UNDER = """
query Under($lens: ID!, $annotation: ID!) {
  lens(id: $lens) { id ... on ArrayLens { descriptorsUnder(annotation: $annotation) } }
}
"""

CUT = """
mutation Cut($input: CreateLensInput!) {
  createLens(input: $input) { id descriptors }
}
"""

CORNERS = [[1.0, 2.0], [1.0, 6.0], [4.0, 6.0], [4.0, 2.0]]


async def _under(ctx: HttpContext, lens_id: str, annotation_id: str):
    result = await schema.execute(UNDER, context_value=_fresh_request(ctx), variable_values={"lens": lens_id, "annotation": annotation_id})
    assert not result.errors, result.errors
    return result.data["lens"]["descriptorsUnder"]


async def _cut(ctx: HttpContext, lens_id: str, annotation_id: str) -> dict:
    result = await schema.execute(CUT, context_value=_fresh_request(ctx), variable_values={"input": {"lens": lens_id, "annotation": annotation_id}})
    assert not result.errors, result.errors
    return result.data["createLens"]["descriptors"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_says_what_its_crop_to_a_shape_provides_without_making_it(authenticated_context: HttpContext):
    """The answer is the crop's own descriptors, and asking writes no lens and records no cut."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[[3, 6, 8]])
    collection = await _collection_on(ctx, axes=["c", "y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    whole = await seed.create_lens(ctx, dataset, slices=[])
    channel = await _lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 2}])
    lenses, cuts = await models.ArrayLens.objects.acount(), await models.LensCut.objects.acount()

    over_whole = await _under(ctx, str(whole.pk), rectangle["id"])
    over_channel = await _under(ctx, channel["id"], rectangle["id"])

    assert await models.ArrayLens.objects.acount() == lenses, "asking makes no lens"
    assert await models.LensCut.objects.acount() == cuts, "asking records no cut"
    assert over_whole["@mikro/is_whole"] is False and over_whole["@mikro/n_channels"] == 3
    assert over_channel["@mikro/n_channels"] == 1, "the channel the lens pins stays pinned"

    assert over_whole == await _cut(ctx, str(whole.pk), rectangle["id"]), "it is what the cut then gives"
    assert over_channel == await _cut(ctx, channel["id"], rectangle["id"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_that_misses_the_lens_has_no_crop(authenticated_context: HttpContext):
    """Where `createLens` would refuse, the answer is null rather than an error."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    rows = await _lens(ctx, dataset, [{"axis": "y", "start": 5, "stop": 6}])

    assert await _under(ctx, rows["id"], rectangle["id"]) is None


DRAW_WITH_CUT = """
mutation Draw($input: CreateAnnotationInput!, $lens: ID!) {
  createAnnotation(input: $input) { id descriptors descriptorsUnder(lens: $lens) }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_says_what_its_crop_of_a_lens_provides_in_the_request_that_draws_it(authenticated_context: HttpContext):
    """`Annotation.descriptorsUnder(lens:)` is the same answer, asked from the shape -- so a client drawing it gets both in one go."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[[3, 6, 8]])
    collection = await _collection_on(ctx, axes=["c", "y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    channel = await _lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 2}])
    lenses = await models.ArrayLens.objects.acount()

    result = await schema.execute(
        DRAW_WITH_CUT,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"collection": collection["id"], "kind": "RECTANGLE", "vectors": CORNERS}, "lens": channel["id"]},
    )
    assert not result.errors, result.errors
    drawn = result.data["createAnnotation"]

    assert drawn["descriptors"]["@mikro/annotation_kind"] == "RECTANGLE"
    assert drawn["descriptorsUnder"] == await _under(ctx, channel["id"], drawn["id"]), "both ends answer one question"
    assert drawn["descriptorsUnder"]["@mikro/n_channels"] == 1
    assert await models.ArrayLens.objects.acount() == lenses, "drawing makes no crop"
    assert drawn["descriptorsUnder"] == await _cut(ctx, channel["id"], drawn["id"])

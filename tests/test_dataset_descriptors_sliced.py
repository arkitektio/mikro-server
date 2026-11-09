"""A dataset can be asked what a selection of it would provide, before anyone makes the lens.

`ArrayDataset.descriptorsSliced(slices:)` answers the descriptors `createLens(dataset:, slices:)`
would give the lens it returns -- the same normalization, over an unsaved lens -- so a viewer
can offer the actions that fit the plane or box on screen and make the lens only when one runs.
"""

import pytest
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request

SLICED = """
query Sliced($dataset: ID!, $slices: [SliceInput!]!) {
  arrayDataset(id: $dataset) { id descriptorsSliced(slices: $slices) }
}
"""

CUT = """
mutation Cut($input: CreateLensInput!) {
  createLens(input: $input) { id descriptors }
}
"""


async def _sliced(ctx: HttpContext, dataset_id: str, slices: list[dict]):
    result = await schema.execute(SLICED, context_value=_fresh_request(ctx), variable_values={"dataset": dataset_id, "slices": slices})
    assert not result.errors, result.errors
    return result.data["arrayDataset"]["descriptorsSliced"]


async def _cut(ctx: HttpContext, dataset_id: str, slices: list[dict]) -> dict:
    result = await schema.execute(CUT, context_value=_fresh_request(ctx), variable_values={"input": {"dataset": dataset_id, "slices": slices}})
    assert not result.errors, result.errors
    return result.data["createLens"]["descriptors"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_says_what_a_selection_provides_without_making_it(authenticated_context: HttpContext):
    """The answer is the lens' own descriptors, and asking writes no lens."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[[3, 6, 8]])
    channel = [{"axis": "c", "start": 1, "stop": 2}]
    rows = [{"axis": "y", "start": 2, "stop": 5}]
    lenses = await models.ArrayLens.objects.acount()

    one_channel = await _sliced(ctx, str(dataset.pk), channel)
    some_rows = await _sliced(ctx, str(dataset.pk), rows)

    assert await models.ArrayLens.objects.acount() == lenses, "asking makes no lens"
    assert one_channel["@mikro/n_channels"] == 1 and one_channel["@mikro/is_whole"] is False
    assert some_rows["@mikro/n_channels"] == 3
    assert one_channel == await _cut(ctx, str(dataset.pk), channel), "it is what the cut then gives"
    assert some_rows == await _cut(ctx, str(dataset.pk), rows)

    everything = await _sliced(ctx, str(dataset.pk), [])
    assert everything["@mikro/is_whole"] is True, "no slices is the whole lens"
    assert everything == await _cut(ctx, str(dataset.pk), [])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_selection_the_cut_would_refuse_has_no_descriptors(authenticated_context: HttpContext):
    """Where `createLens` would refuse, the answer is null rather than an error."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])

    assert await _sliced(ctx, str(dataset.pk), [{"axis": "y", "start": 7, "stop": 9}]) is None, "an empty window"
    assert await _sliced(ctx, str(dataset.pk), [{"axis": "z", "start": 0, "stop": 1}]) is None, "an axis the dataset lacks"

"""`createSceneFromLens` stages a lens of any kind: the layer its container implies, reading that lens, in the container's own space."""

import pytest
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_layer_kinds import _seed_table_dataset
from tests.test_windowed_lenses import _a_sparse

FROM_LENS = """
mutation F($input: CreateSceneFromLensInput!) {
  createSceneFromLens(input: $input) {
    id name worldCoordinateSystem { id }
    layers { __typename ... on PointLayer { lens { id } windowFilters { column min max } } ... on TrackLayer { lens { id } } }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_from_a_table_lens_draws_that_window_as_a_point_layer_in_the_tables_space(authenticated_context: HttpContext):
    ctx = authenticated_context
    table, system = await _seed_table_dataset(ctx, "points")
    window = await seed.create_windowed_lens(ctx, table, [{"axis": "x", "min": 0.0, "max": 10.0}])

    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str(window.pk)}})
    assert not result.errors, result.errors
    scene = result.data["createSceneFromLens"]
    assert scene["name"] == "points" and scene["worldCoordinateSystem"]["id"] == str(system.pk), "named for the table, composed in its own space"
    (layer,) = scene["layers"]
    assert layer["__typename"] == "PointLayer" and layer["lens"]["id"] == str(window.pk)
    assert layer["windowFilters"] == [{"column": "x", "min": 0.0, "max": 10.0}], "the layer reads the window, not the whole table"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_from_a_lens_over_a_tracked_table_draws_a_track_layer(authenticated_context: HttpContext):
    ctx = authenticated_context
    table, _ = await _seed_table_dataset(ctx, "tracks", with_track=True)
    whole = await seed.create_windowed_lens(ctx, table)
    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str(whole.pk), "name": "Trajectories"}})
    assert not result.errors, result.errors
    (layer,) = result.data["createSceneFromLens"]["layers"]
    assert layer["__typename"] == "TrackLayer" and layer["lens"]["id"] == str(whole.pk)
    assert result.data["createSceneFromLens"]["name"] == "Trajectories"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_from_a_sparse_lens_is_refused_in_prose(authenticated_context: HttpContext):
    """No layer draws a matrix, and the build says so rather than handing back an empty scene."""
    ctx = authenticated_context
    sparse_id, _, _ = await _a_sparse(ctx)
    whole = await models.SparseLens.objects.aget(sparse_dataset_id=sparse_id)
    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str(whole.pk)}})
    assert result.errors and "no layer kind" in result.errors[0].message, result.errors
    assert not await models.Scene.objects.filter(name__icontains="expression").aexists(), "nothing was left behind"

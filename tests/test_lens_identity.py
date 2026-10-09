"""A lens is a deterministic function of what it selects: one row per (dataset, normalized slices).

The same selection asked for twice -- by one client, by two scenes, by a chart -- is one id,
so a port handed a lens sees the same argument for the same data. Every dataset has its whole
lens from creation, and that lens, like any lens something draws through, cannot be deleted.
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from datalayer.models import ZarrStore
from kante.context import HttpContext

from chart import models as chart_models
from core import enums, models
from mikro_server.schema import schema
from tests import seed
from tests.test_charts import TRACE, _chart, _lay_along, _ok

CREATE_LENS = """
mutation CreateLens($input: CreateLensInput!) {
  createLens(input: $input) { id shape slices { axis start stop step } coordinateSystem { id } }
}
"""

FROM_SYSTEM = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) { id layers { id ... on IntensityLayer { lens { id } } } }
}
"""

CREATE_DATASET = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id intrinsicSystem { id } }
}
"""


async def _lens(ctx: HttpContext, dataset: models.ArrayDataset, slices: list[dict] | None = None) -> dict:
    result = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": slices or []}})
    assert not result.errors, result.errors
    return result.data["createLens"]


async def _refused(ctx: HttpContext, dataset: models.ArrayDataset, slices: list[dict]) -> str:
    result = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": slices}})
    assert result.errors, "the lens was created"
    return result.errors[0].message


async def _lens_ids(dataset: models.ArrayDataset) -> list[int]:
    return [pk async for pk in models.Lens.objects.filter(dataset=dataset).order_by("pk").values_list("pk", flat=True)]


# --- the whole lens -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_dataset_has_exactly_its_whole_lens(authenticated_context: HttpContext):
    """createArrayDataset ends with the lens selecting all of it, in the intrinsic system, and nothing else."""
    store = await ZarrStore.objects.acreate(organization=authenticated_context.request.organization, key="whole", bucket="zarr", shape=[2, 32, 32], chunks=[2, 32, 32], version="3", dtype="uint8", populated=True)
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            CREATE_DATASET,
            context_value=authenticated_context,
            variable_values={"input": {"name": "Whole", "data": str(store.id), "scales": [], "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]}},
        )
    assert not result.errors, result.errors
    made = result.data["createArrayDataset"]

    lenses = [lens async for lens in models.Lens.objects.filter(dataset_id=made["id"])]
    assert len(lenses) == 1
    assert lenses[0].slices == []
    assert str(lenses[0].coordinate_system_id) == made["intrinsicSystem"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_asking_for_the_whole_dataset_is_the_whole_lens(authenticated_context: HttpContext):
    """createLens with no slices, and with a slice that keeps every axis whole, is the one row made at creation."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")
    (whole_id,) = await _lens_ids(dataset)

    bare = await _lens(authenticated_context, dataset)
    open_ended = await _lens(authenticated_context, dataset, [{"axis": "y"}])
    spelled_out = await _lens(authenticated_context, dataset, [{"axis": "x", "start": 0, "stop": 64, "step": 1}, {"axis": "c", "start": -3}])

    assert bare["id"] == open_ended["id"] == spelled_out["id"] == str(whole_id)
    assert bare["slices"] == [], "a full-axis slice is dropped: it selects nothing narrower than the array"
    assert await _lens_ids(dataset) == [whole_id]


# --- sliced lenses --------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_same_selection_is_the_same_lens(authenticated_context: HttpContext):
    """Two calls for one crop are one id, one coordinate system and one edge."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")
    before_systems = await sync_to_async(models.CoordinateSystem.objects.count)()
    before_edges = await sync_to_async(models.Transformation.objects.count)()

    first = await _lens(authenticated_context, dataset, [{"axis": "y", "start": 8, "stop": 40}])
    second = await _lens(authenticated_context, dataset, [{"axis": "y", "start": 8, "stop": 40}])

    assert first["id"] == second["id"]
    assert first["coordinateSystem"] == second["coordinateSystem"]
    assert await sync_to_async(models.CoordinateSystem.objects.count)() == before_systems + 1, "one space for the one selection"
    assert await sync_to_async(models.Transformation.objects.count)() == before_edges + 1, "one edge back into the grid"
    assert len(await _lens_ids(dataset)) == 2, "the whole lens and the crop"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_spelling_of_a_selection_is_one_lens(authenticated_context: HttpContext):
    """Negative bounds, open bounds and a stop past the end resolve to the same stored slice and the same row."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS", shapes=[[3, 64, 64]])

    spellings = [
        [{"axis": "y", "start": 8}],
        [{"axis": "y", "start": 8, "stop": 64}],
        [{"axis": "y", "start": 8, "stop": 400}],
        [{"axis": "y", "start": -56}],
        [{"axis": "y", "start": 8, "step": 1}],
    ]
    made = [await _lens(authenticated_context, dataset, spelling) for spelling in spellings]

    assert len({lens["id"] for lens in made}) == 1
    assert made[0]["slices"] == [{"axis": "y", "start": 8, "stop": 64, "step": 1}]
    assert made[0]["shape"] == [3, 56, 64]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stepped_slice_is_spelled_by_what_it_selects(authenticated_context: HttpContext):
    """``0:10:3`` and ``0:12:3`` pick the same four positions, so they are one lens with one stop."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS", shapes=[[3, 64, 64]])

    ragged = await _lens(authenticated_context, dataset, [{"axis": "x", "start": 0, "stop": 12, "step": 3}])
    tight = await _lens(authenticated_context, dataset, [{"axis": "x", "start": 0, "stop": 10, "step": 3}])

    assert ragged["id"] == tight["id"]
    assert ragged["slices"] == [{"axis": "x", "start": 0, "stop": 10, "step": 3}]
    assert ragged["shape"] == [3, 64, 4]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_slices_are_stored_in_the_datasets_axis_order(authenticated_context: HttpContext):
    """The order a caller lists slices in is not part of the selection."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")

    xy = await _lens(authenticated_context, dataset, [{"axis": "x", "start": 4, "stop": 20}, {"axis": "y", "start": 8, "stop": 40}])
    yx = await _lens(authenticated_context, dataset, [{"axis": "y", "start": 8, "stop": 40}, {"axis": "x", "start": 4, "stop": 20}])

    assert xy["id"] == yx["id"]
    assert [entry["axis"] for entry in xy["slices"]] == ["y", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_selection_that_cannot_be_made_is_refused_in_prose(authenticated_context: HttpContext):
    """An axis the dataset lacks, an axis sliced twice, a reversing step and an empty window are refused, and nothing is written."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")
    before = await _lens_ids(dataset)

    assert "does not have" in await _refused(authenticated_context, dataset, [{"axis": "t", "start": 0, "stop": 1}])
    assert "sliced twice" in await _refused(authenticated_context, dataset, [{"axis": "y", "start": 0, "stop": 8}, {"axis": "y", "start": 8, "stop": 16}])
    assert "positive" in await _refused(authenticated_context, dataset, [{"axis": "y", "start": 40, "stop": 8, "step": -1}])
    assert "positive" in await _refused(authenticated_context, dataset, [{"axis": "y", "step": 0}])
    assert "selects nothing" in await _refused(authenticated_context, dataset, [{"axis": "y", "start": 40, "stop": 8}])
    assert "selects nothing" in await _refused(authenticated_context, dataset, [{"axis": "y", "start": 64}])

    assert await _lens_ids(dataset) == before


# --- the compositions share it ----------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_scenes_over_one_dataset_share_its_lens(authenticated_context: HttpContext):
    """Bootstrapping twice over one intrinsic system draws both scenes through the one whole lens."""
    dataset = await seed.create_array_dataset(authenticated_context, "Owner")
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    (whole_id,) = await _lens_ids(dataset)

    scenes = []
    for _ in range(2):
        result = await schema.execute(FROM_SYSTEM, context_value=authenticated_context, variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": {}}})
        assert not result.errors, result.errors
        scenes.append(result.data["createSceneFromCoordinateSystem"])

    lens_ids = {layer["lens"]["id"] for scene in scenes for layer in scene["layers"]}
    assert lens_ids == {str(whole_id)}
    assert await _lens_ids(dataset) == [whole_id], "no scene minted a lens of its own"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_trace_from_a_dataset_reads_its_whole_lens(authenticated_context: HttpContext):
    """createTraceChartLayer(dataset:) draws through the dataset's one whole lens rather than minting another."""
    ctx = authenticated_context
    chart = await _chart(ctx, "Profile")
    profile = await seed.create_array_dataset(ctx, "Profile", axes=[seed.axis("d", enums.AxisType.SPACE)], shapes=[[64]])
    await _lay_along(ctx, profile, chart, "d")
    (whole_id,) = await _lens_ids(profile)

    made = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk)}))["createTraceChartLayer"]

    assert made["lens"]["id"] == str(whole_id)
    assert await _lens_ids(profile) == [whole_id]
    assert await sync_to_async(chart_models.ChartLayer.objects.filter(lens_id=whole_id).count)() == 1

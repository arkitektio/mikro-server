"""Fields and filters a client asked for so it stops working around them.

Each one answers in the server what a client computed over a page: a creation time, a
dataset's bytes, whether a space is physical, a space's own voxel size, a directed
neighbourhood, a count of what a file was converted into, a pyramid level's scale, and the
anchors of a lens within a window.
"""

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from arkitekt_datalayer.models import ZarrStore
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

UM_YX = [{"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]
YX = [seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]


async def _query(ctx: HttpContext, query: str, **variables: Any) -> dict[str, Any]:
    result = await schema.execute(query, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


async def _calibrate(ctx: HttpContext, dataset: models.ArrayDataset, scale: list[float]) -> str:
    data = await _query(
        ctx,
        "mutation C($input: CreateCoordinateSystemInput!) { createCoordinateSystem(input: $input) { id } }",
        input={
            "name": f"{dataset.name} µm",
            "axes": UM_YX,
            "registrations": [
                {
                    "dataset": str(dataset.pk),
                    "validity": "MANUAL",
                    "transform": {"kind": "BY_DIMENSION", "parts": [{"kind": "SCALE", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": scale}]},
                }
            ],
        },
    )
    return data["createCoordinateSystem"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_datasets_and_files_say_when_they_were_made(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Timed", axes=YX, shapes=[[8, 8]])
    folder = await seed.create_folder(ctx, "Files")
    file = await seed.create_file(ctx, "raw.czi", folder)

    data = await _query(
        ctx,
        "query Q($d: ID!, $f: ID!) { arrayDataset(id: $d) { createdAt } file(id: $f) { createdAt } tableDatasets { createdAt } sparseDatasets { createdAt } }",
        d=str(dataset.pk),
        f=str(file.pk),
    )
    assert data["arrayDataset"]["createdAt"] and data["file"]["createdAt"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_weighs_every_level_of_its_pyramid(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", axes=YX, shapes=[[64, 64], [32, 32]])
    unmeasured = await seed.create_array_dataset(ctx, "Unmeasured", axes=YX, shapes=[[8, 8]])

    def attach_stores() -> None:
        for array, size in zip(dataset.data_arrays.order_by("level"), (4000, 1000)):
            array.store = ZarrStore.objects.create(path=f"s3://zarr/{array.pk}", bucket="zarr", key=f"level-{array.pk}", organization=ctx.request.organization, size_bytes=size)
            array.save()

    await sync_to_async(attach_stores)()

    single = await _query(ctx, "query Q($id: ID!) { arrayDataset(id: $id) { sizeBytes } }", id=str(dataset.pk))
    assert single["arrayDataset"]["sizeBytes"] == 5000

    listed = await _query(ctx, "{ arrayDatasets(ordering: [{ createdAt: ASC }]) { name sizeBytes } }")
    assert {row["name"]: row["sizeBytes"] for row in listed["arrayDatasets"]} == {"Pyramid": 5000, "Unmeasured": None}
    assert unmeasured


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_physical_spaces_are_told_from_index_spaces(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Grid", axes=YX, shapes=[[16, 16]])
    physical = await _calibrate(ctx, dataset, [0.5, 0.25])
    grid = str(dataset.coordinate_system_id)

    data = await _query(
        ctx,
        "{ yes: coordinateSystems(filters: { physical: true }) { id physical } no: coordinateSystems(filters: { physical: false }) { id physical } }",
    )
    assert [row["id"] for row in data["yes"]] == [physical]
    assert all(row["physical"] for row in data["yes"])
    assert grid in [row["id"] for row in data["no"]]
    assert not any(row["physical"] for row in data["no"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_reads_its_voxel_size_through_more_than_one_hop(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", axes=YX, shapes=[[64, 64], [32, 32]])
    physical = await _calibrate(ctx, dataset, [0.5, 0.25])
    level_one = await sync_to_async(lambda: dataset.data_arrays.get(level=1).coordinate_system_id)()

    query = "query Q($id: ID!) { coordinateSystem(id: $id) { voxelSizes { canonical hops system { id } axes { axis size } } } }"
    grid = (await _query(ctx, query, id=str(dataset.coordinate_system_id)))["coordinateSystem"]["voxelSizes"]
    assert [(entry["system"]["id"], entry["hops"], entry["canonical"]) for entry in grid] == [(physical, 1, True)]
    assert {entry["axis"]: entry["size"] for entry in grid[0]["axes"]} == {"y": 0.5, "x": 0.25}

    level = (await _query(ctx, query, id=str(level_one)))["coordinateSystem"]["voxelSizes"]
    assert [entry["hops"] for entry in level] == [2], "the level reaches the calibration through the dataset's grid"
    assert {entry["axis"]: entry["size"] for entry in level[0]["axes"]} == {"y": 1.0, "x": 0.5}

    assert (await _query(ctx, query, id=physical))["coordinateSystem"]["voxelSizes"] == [], "a physical space reaches nothing"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_coordinate_graph_can_walk_one_way(authenticated_context: HttpContext):
    ctx = authenticated_context
    first = await seed.create_array_dataset(ctx, "First", axes=YX, shapes=[[16, 16]])
    world = await _calibrate(ctx, first, [1.0, 1.0])
    second = await seed.create_array_dataset(ctx, "Second", axes=YX, shapes=[[16, 16]])
    await _query(
        ctx,
        "mutation R($input: CreateTransformationInput!) { createTransformation(input: $input) { id } }",
        input={"input": str(second.coordinate_system_id), "output": world, "transform": {"kind": "IDENTITY"}, "validity": "MANUAL"},
    )

    query = "query Q($id: ID!, $direction: GraphDirection!) { coordinateGraph(coordinateSystem: $id, direction: $direction) { systems { id } } }"

    def ids(data: dict[str, Any]) -> set[str]:
        return {system["id"] for system in data["coordinateGraph"]["systems"]}

    both = ids(await _query(ctx, query, id=str(first.coordinate_system_id), direction="BOTH"))
    assert str(second.coordinate_system_id) in both, "undirected, the walk crosses the world into the other dataset"

    outgoing = ids(await _query(ctx, query, id=str(first.coordinate_system_id), direction="OUTGOING"))
    assert outgoing == {str(first.coordinate_system_id), world}

    incoming = ids(await _query(ctx, query, id=world, direction="INCOMING"))
    assert {str(first.coordinate_system_id), str(second.coordinate_system_id), world} <= incoming


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_files_converted_containers_are_counted_and_paged(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Ingest")
    file = await seed.create_file(ctx, "series.lif", folder)
    for index in range(3):
        dataset = await seed.create_array_dataset(ctx, f"Series {index}", axes=YX, shapes=[[8, 8]])
        await _query(
            ctx,
            "mutation L($input: LinkFileInput!) { linkFile(input: $input) { id } }",
            input={"dataset": str(dataset.pk), "sourceFiles": [{"file": str(file.pk)}]},
        )

    data = await _query(
        ctx,
        """
        query Q($id: ID!) {
          file(id: $id) {
            all: derivedContainersCount
            tables: derivedContainersCount(kind: TABLE_DATASET)
            datasets: derivedContainersCount(kind: DATASET)
            page: derivedContainers(pagination: { limit: 2, offset: 0 }) { id }
            onlyTables: derivedContainers(filters: { containerKind: TABLE_DATASET }) { id }
          }
          files(filters: { ids: [$id] }) { derivedContainersCount }
        }
        """,
        id=str(file.pk),
    )
    assert (data["file"]["all"], data["file"]["tables"], data["file"]["datasets"]) == (3, 0, 3)
    assert len(data["file"]["page"]) == 2
    assert data["file"]["onlyTables"] == []
    assert data["files"] == [{"derivedContainersCount": 3}], "a page of files counts from its prefetch"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_pyramid_level_states_its_scale_against_level_zero(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", axes=YX, shapes=[[64, 64], [16, 32]])

    data = await _query(ctx, "query Q($id: ID!) { arrayDataset(id: $id) { dataArrays { level relativeScale { axis factor } } } }", id=str(dataset.pk))
    scales = {array["level"]: {entry["axis"]: entry["factor"] for entry in array["relativeScale"]} for array in data["arrayDataset"]["dataArrays"]}
    assert scales == {0: {"y": 1.0, "x": 1.0}, 1: {"y": 4.0, "x": 2.0}}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_pages_its_anchors_within_a_window(authenticated_context: HttpContext):
    ctx = authenticated_context
    axes = [seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL), *YX]
    dataset = await seed.create_array_dataset(ctx, "Timelapse", axes=axes, shapes=[[10, 2, 8, 8]])

    def pin() -> None:
        organization = ctx.request.organization
        models.CoordinateAnchor.objects.create(dataset=dataset, organization=organization, coordinates={})
        for t in range(10):
            models.CoordinateAnchor.objects.create(dataset=dataset, organization=organization, coordinates={"t": t, "c": t % 2})

    await sync_to_async(pin)()
    whole = await seed.whole_lens_id(models.ArrayDataset, dataset.pk)
    query = """
    query Q($id: ID!, $within: [WindowInput!], $pagination: OffsetPaginationInput) {
      lens(id: $id) { activeAnchors(within: $within, pagination: $pagination) { coordinates } }
    }
    """
    everything = await _query(ctx, query, id=whole)
    assert len(everything["lens"]["activeAnchors"]) == 11

    windowed = await _query(ctx, query, id=whole, within=[{"axis": "t", "min": 2, "max": 5}, {"axis": "c", "min": 0, "max": 0}])
    assert [anchor["coordinates"] for anchor in windowed["lens"]["activeAnchors"]] == [{}, {"t": 2, "c": 0}, {"t": 4, "c": 0}], "the global anchor holds in any window"

    paged = await _query(ctx, query, id=whole, pagination={"limit": 3, "offset": 9})
    assert len(paged["lens"]["activeAnchors"]) == 2

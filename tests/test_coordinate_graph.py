"""`coordinateGraph` hands back the neighbourhood of one coordinate system.

The list queries answer "which edges exist"; a filter can narrow them by input, output or
kind, but it cannot answer "which edges relate to *this* system", because relatedness is
transitive and a filter is not. Walking it client-side means a round trip per hop. So the
walk happens here, and what comes back is the subgraph -- nodes and directed edges, nothing
composed, in keeping with the rest of the coordinate API.

Reachability is undirected on purpose, and the tests pin that: standing on a calibrated
space, the edge that *points into* it is the calibration, and a forward-only walk would
answer "nothing relates to this" for exactly the space a user is likeliest to ask about --
one that, under residence, has no residents to describe it either.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed
from tests.test_placement_queries import QueryCounter, _fresh_request

GRAPH = """
query Graph($id: ID!, $maxDepth: Int) {
  coordinateGraph(coordinateSystem: $id, maxDepth: $maxDepth) {
    root { id residents { __typename } }
    systems { id name residents { __typename } axes { name type } }
    transformations {
      id kind inputAxes outputAxes
      input { id  }
      output { id  }
      ... on SequenceTransformation { transformations { id kind } }
    }
  }
}
"""

_AFFINE_3D = [
    [1.0, 0.0, 0.0, 5.0],
    [0.0, 1.0, 0.0, 5.0],
    [0.0, 0.0, 1.0, 0.0],
]

#: The physical axes of a `seed.SIMPLE_AXES` (c, y, x) dataset.
_PHYSICAL_AXES = [
    seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."),
    seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
    seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
]


async def _calibrate(ctx: HttpContext, dataset: models.ArrayDataset) -> models.CoordinateSystem:
    return await seed.create_physical_space(ctx, dataset, _PHYSICAL_AXES, scale=[1.0, 0.5, 0.5], name="Stage")


async def _register(ctx: HttpContext, dataset: models.ArrayDataset, scene: models.Scene) -> models.Transformation:
    def write() -> models.Transformation:
        # Authoring the edge into the world IS the placement (one truth per space):
        # there is no scene membership to join.
        return models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={"affine": _AFFINE_3D},
            organization=ctx.request.organization,
        )

    return await sync_to_async(write)()


async def _graph(ctx: HttpContext, system: models.CoordinateSystem, max_depth: int | None = None) -> dict:
    result = await schema.execute(GRAPH, context_value=ctx, variable_values={"id": str(system.pk), "maxDepth": max_depth})
    assert not result.errors, result.errors
    return result.data["coordinateGraph"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_reaches_the_whole_neighbourhood_of_a_dataset(authenticated_context: HttpContext):
    """From a dataset's pixel grid: its levels, its lens, its calibration, and the world it sits in."""
    dataset = await seed.create_array_dataset(authenticated_context, "Volume", shapes=[[3, 64, 64], [3, 32, 32]])
    await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    await _calibrate(authenticated_context, dataset)
    scene = await seed.create_scene(authenticated_context, "Composition")
    await _register(authenticated_context, dataset, scene)

    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    graph = await _graph(authenticated_context, intrinsic)

    assert graph["root"]["id"] == str(intrinsic.pk)

    # Five spaces: the dataset's own grid (where the dataset, level 0 and any unsliced lens
    # all live), the downsampled level's, the sliced lens' crop, the calibrated space and the
    # scene's world. The last two hold nothing at all -- a calibrated space and a world are
    # both just reference frames, which is exactly what RFC-9 collapsed PHYSICAL and SHARED
    # into.
    inhabitants = sorted(sorted(r["__typename"] for r in system["residents"]) for system in graph["systems"])
    assert inhabitants == [[], [], ["ArrayDataset", "DataArray"], ["DataArray"], ["Lens"]], inhabitants

    # Every edge is inside the component: no endpoint dangles.
    ids = {system["id"] for system in graph["systems"]}
    for edge in graph["transformations"]:
        assert edge["input"]["id"] in ids and edge["output"]["id"] in ids

    # And they arrive with their axis order, so the client can compose without a second trip.
    assert all(edge["inputAxes"] for edge in graph["transformations"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_edge_pointing_into_the_root_still_relates_to_it(authenticated_context: HttpContext):
    """Reachability is undirected: from a PHYSICAL system, the calibration points *in*.

    A forward-only walk leaves this system with no edges at all -- a calibration maps pixels
    *into* physical space, and physical space is a sink. Answering "nothing relates to this"
    for a system whose whole reason to exist is one edge would make the query useless
    exactly where it is most natural to start.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Volume")
    calibration = await _calibrate(authenticated_context, dataset)

    graph = await _graph(authenticated_context, calibration)

    assert graph["root"]["residents"] == [], "a calibrated space holds nothing; the edge is what relates it"
    assert graph["transformations"], "the calibration edge points into this system, and it relates to it"

    inhabitants = sorted(sorted(r["__typename"] for r in system["residents"]) for system in graph["systems"])
    # A single-level dataset has one pixel grid, shared by the dataset and its level 0.
    assert inhabitants == [[], ["ArrayDataset", "DataArray"]], inhabitants

    calibration_edge = next(edge for edge in graph["transformations"] if edge["output"]["id"] == str(calibration.pk))
    # Reached backwards, but reported forwards: the client still knows which way it composes.
    intrinsic_id = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    assert calibration_edge["input"]["id"] == intrinsic_id, "the calibration sets out from the dataset's own space"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_datasets_in_one_scene_reach_each_other_through_world(authenticated_context: HttpContext):
    """Relatedness is transitive, which is exactly what no filter on `transformations` can express."""
    first = await seed.create_array_dataset(authenticated_context, "First")
    second = await seed.create_array_dataset(authenticated_context, "Second")
    scene = await seed.create_scene(authenticated_context, "Composition")
    await _register(authenticated_context, first, scene)
    await _register(authenticated_context, second, scene)

    intrinsic = await sync_to_async(lambda: first.intrinsic_coordinate_system)()
    other = await sync_to_async(lambda: second.intrinsic_coordinate_system)()

    graph = await _graph(authenticated_context, intrinsic)

    assert str(other.pk) in {system["id"] for system in graph["systems"]}, "the second dataset is two hops away, through the world both are registered into"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_max_depth_bounds_the_walk_and_leaves_no_edge_dangling(authenticated_context: HttpContext):
    """A depth cutoff must cut whole edges, not leave ones pointing at systems it did not return."""
    first = await seed.create_array_dataset(authenticated_context, "First")
    second = await seed.create_array_dataset(authenticated_context, "Second")
    scene = await seed.create_scene(authenticated_context, "Composition")
    await _register(authenticated_context, first, scene)
    await _register(authenticated_context, second, scene)

    intrinsic = await sync_to_async(lambda: first.intrinsic_coordinate_system)()
    other = await sync_to_async(lambda: second.intrinsic_coordinate_system)()

    graph = await _graph(authenticated_context, intrinsic, max_depth=1)

    ids = {system["id"] for system in graph["systems"]}
    assert str(other.pk) not in ids, "the second dataset is two hops away and the walk was bounded at one"
    for edge in graph["transformations"]:
        assert edge["input"]["id"] in ids and edge["output"]["id"] in ids, "a bounded walk must not return an edge whose endpoint it withheld"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_stops_at_the_organization_boundary(authenticated_context: HttpContext, other_org_context: HttpContext):
    """A graph walk crosses foreign keys, and every one of them is a chance to cross a tenant.

    The traversal is not a filtered list the scoping layer would narrow on its own: it issues
    its own queries, so it carries the organization itself. Without that, one ID would hand
    back another organization's entire coordinate graph.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Ours")
    theirs = await seed.create_array_dataset(other_org_context, "Theirs")

    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    foreign = await sync_to_async(lambda: theirs.intrinsic_coordinate_system)()

    # Their system is not ours to read at all.
    denied = await schema.execute(GRAPH, context_value=authenticated_context, variable_values={"id": str(foreign.pk), "maxDepth": None})
    assert denied.errors, "a foreign coordinate system must not be a valid root"

    # And nothing of theirs turns up in ours.
    graph = await _graph(authenticated_context, intrinsic)
    assert str(foreign.pk) not in {system["id"] for system in graph["systems"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_is_flat_in_the_size_of_the_graph(authenticated_context: HttpContext):
    """The cost is the depth of the walk, not the width of it.

    A custom resolver returns a plain list, and a plain list is invisible to the optimizer --
    so `axes`, `children` and the endpoints have to be prefetched by the traversal itself or
    every one of them is a query per edge. This pins the property rather than the fix: the
    same count for a scene of two datasets and a scene of six.
    """

    async def measure(dataset_count: int) -> tuple[int, int]:
        scene = await seed.create_scene(authenticated_context, "Composition")
        datasets = [await seed.create_array_dataset(authenticated_context, f"D{index}", shapes=[[3, 64, 64], [3, 32, 32]]) for index in range(dataset_count)]
        for dataset in datasets:
            await seed.create_lens(authenticated_context, dataset, slices=[])
            await _calibrate(authenticated_context, dataset)
            await _register(authenticated_context, dataset, scene)

        intrinsic = await sync_to_async(lambda: datasets[0].intrinsic_coordinate_system)()
        variables = {"id": str(intrinsic.pk), "maxDepth": None}

        await schema.execute(GRAPH, context_value=_fresh_request(authenticated_context), variable_values=variables)

        with QueryCounter() as counter:
            result = await schema.execute(GRAPH, context_value=_fresh_request(authenticated_context), variable_values=variables)
        assert not result.errors, result.errors
        return len(result.data["coordinateGraph"]["systems"]), len(counter)

    small_systems, small_queries = await measure(2)

    await models.Scene.objects.all().adelete()
    await models.ArrayDataset.objects.all().adelete()

    large_systems, large_queries = await measure(6)

    assert large_systems > small_systems, "the six-dataset graph must actually be bigger"
    assert large_queries == small_queries, f"the walk's query count grows with the graph: {small_queries} for 2 datasets, {large_queries} for 6"

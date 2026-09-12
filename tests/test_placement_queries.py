"""The scene and placement API must not scale its query count with its layer count.

Every placement field (`pathToWorld`, `levelPaths`, and the space's `placedSystems` and
`annotations`) is a custom resolver over the coordinate graph, so the optimizer cannot see
what it touches: it walks `layer.lens.coordinate_system` for a client that selected
nothing but `pathToWorld`. Left alone, each layer rebuilt the scene's adjacency from
scratch and the reachability closure ran once per field.

These tests pin the property that fixes rather than the fix: **the same query count for a
scene of three layers and a scene of seven**. A count that grows with the layers is the N+1
coming back, whatever shape it returns in. Each scene carries one image layer at minimum,
so every placement field is actually exercised at both sizes.
"""

import threading

import pytest
from asgiref.sync import sync_to_async
from django.db.backends import utils as db_utils
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from core.logic import scene_graph, space_graph
from mikro_server.schema import schema
from tests import seed


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity.

    Per-request state (the scene-graph memo, the auth extension's caches) lives on the
    context, so reusing one across two executions would let the second ride on the first's
    warm caches -- and a query count measured that way is not the one a real client pays.
    """
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


class QueryCounter:
    """Counts every SQL statement, on any thread.

    Not `django_assert_num_queries`: the schema is executed async, so the ORM work
    happens on asgiref's executor thread, whose connection is a different object from
    the one the test thread would instrument. Patching the cursor catches all of them.
    """

    def __init__(self) -> None:
        self.queries: list[str] = []
        self._lock = threading.Lock()

    def __enter__(self) -> "QueryCounter":
        self._execute = db_utils.CursorWrapper.execute
        self._executemany = db_utils.CursorWrapper.executemany

        def execute(inner, sql, params=None):
            with self._lock:
                self.queries.append(sql)
            return self._execute(inner, sql, params)

        def executemany(inner, sql, param_list):
            with self._lock:
                self.queries.append(sql)
            return self._executemany(inner, sql, param_list)

        db_utils.CursorWrapper.execute = execute
        db_utils.CursorWrapper.executemany = executemany
        return self

    def __exit__(self, *exc) -> None:
        db_utils.CursorWrapper.execute = self._execute
        db_utils.CursorWrapper.executemany = self._executemany

    def __len__(self) -> int:
        return len(self.queries)


SCENE_PLACEMENTS = """
query ScenePlacements {
  scenes {
    id
    layers {
      id
      pathToWorld { inverted transformation { id kind inputAxes outputAxes ... on SequenceTransformation { transformations { id kind inputAxes outputAxes } } } }
      asAffine { matrix inputAxes outputAxes total }
      ... on ImageLayer {
        levelPaths {
          dataArray { id level }
          path { inverted transformation { id kind inputAxes outputAxes ... on SequenceTransformation { transformations { id kind inputAxes outputAxes } } } }
        }
      }
    }
    worldCoordinateSystem { placedSystems { id residents { __typename } } annotations { id } }
  }
}
"""

ROOT_LAYERS = """
query RootLayers {
  layers {
    id
    pathToWorld { inverted transformation { id kind inputAxes outputAxes ... on SequenceTransformation { transformations { id kind inputAxes outputAxes } } } }
    asAffine { matrix inputAxes outputAxes total }
    ... on ImageLayer {
      levelPaths {
        dataArray { id level }
        path { inverted transformation { id kind inputAxes outputAxes ... on SequenceTransformation { transformations { id kind inputAxes outputAxes } } } }
      }
    }
  }
}
"""

#: A three-level pyramid, so `levelPaths` has more than one level to place.
_SHAPES = [[3, 64, 64], [3, 32, 32], [3, 16, 16]]

_AFFINE = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


async def _seed_scene(ctx: HttpContext, *, layer_count: int) -> models.Scene:
    """A scene of `layer_count` layers spread over two registered datasets.

    Two datasets, not one: a single-dataset scene would not catch an adjacency that is
    rebuilt per dataset, and both datasets' edges have to stay in their own adjacency for
    the BFS to keep returning the path it returns today.

    The last two layers are an *annotation* layer over a real collection and a *mesh*
    layer over a mesh collection, so the seed covers the two source systems that are not
    reached through a lens. The mesh layer matters twice over: a collection owns its coordinate system, so
    resolving which dataset it belongs to means following its anchor edge, and doing that
    one layer at a time is a query per layer -- exactly the N+1 these tests exist to catch,
    on a path they would otherwise never touch.
    """
    datasets = [await seed.create_array_dataset(ctx, f"Placed{index}", shapes=_SHAPES) for index in range(2)]
    lenses = [await seed.create_lens(ctx, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}]) for dataset in datasets]
    scene = await seed.create_scene(ctx, "Composition")

    def setup() -> None:
        world = scene.world
        for index in range(layer_count - 2):
            models.Layer.objects.create(
                kind=enums.LayerKindChoices.IMAGE.value,
                scene=scene,
                lens=lenses[index % len(lenses)],
            )

        # The collection owns its drawing system; an identity edge anchors it to the
        # dataset's intrinsic grid, like the mesh collection below.
        annotation_collection = models.AnnotationCollection.objects.create(name="Regions", organization=ctx.request.organization)
        annotation_system = models.CoordinateSystem.objects.create(name="Regions/drawing", organization=ctx.request.organization)
        annotation_collection.coordinate_system = annotation_system
        annotation_collection.save(update_fields=["coordinate_system"])
        for index, axis in enumerate(datasets[0].intrinsic_coordinate_system.axes.all().order_by("order")):
            models.Axis.objects.create(coordinate_system=annotation_system, order=index, name=axis.name, type=axis.type)
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.IDENTITY.value,
            input=annotation_system,
            output=datasets[0].intrinsic_coordinate_system,
            organization=ctx.request.organization,
        )
        models.Annotation.objects.create(
            collection=annotation_collection,
            name="Region",
            kind=enums.AnnotationKindChoices.RECTANGLE.value,
            vectors=[[0.0, 0.0, 0.0], [0.0, 8.0, 8.0]],
        )
        models.Layer.objects.create(kind=enums.LayerKindChoices.ANNOTATION.value, scene=scene, annotation_collection=annotation_collection)

        store = seed._seed_fabriks_store_sync(ctx, axes=None, populated=True)
        collection = models.MeshCollection.objects.create(version="v1", spec_version="fabriks/1", store=store, organization=ctx.request.organization)
        mesh_system = models.CoordinateSystem.objects.create(name="v1/mesh", organization=ctx.request.organization)
        collection.coordinate_system = mesh_system
        collection.save(update_fields=["coordinate_system"])
        for index, axis in enumerate(datasets[0].intrinsic_coordinate_system.axes.all().order_by("order")):
            models.Axis.objects.create(coordinate_system=mesh_system, order=index, name=axis.name, type=axis.type)
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.IDENTITY.value,
            input=mesh_system,
            output=datasets[0].intrinsic_coordinate_system,
            organization=ctx.request.organization,
        )
        models.Layer.objects.create(kind=enums.LayerKindChoices.MESH.value, scene=scene, mesh_collection=collection)

        # Authoring the edge into the world is the placement (one truth per space):
        # nothing to add to any scene.
        for dataset in datasets:
            models.Transformation.objects.create(
                kind=enums.TransformKindChoices.AFFINE.value,
                input=dataset.intrinsic_coordinate_system,
                output=world,
                params={"affine": _AFFINE},
                organization=ctx.request.organization,
            )

    await sync_to_async(setup)()
    return scene


async def _execute(ctx: HttpContext, query: str) -> tuple[dict, int]:
    """The query's data and the number of SQL statements one fresh request costs.

    Warmed once and measured on a second, separate request: the first execution of a
    process pays one-off costs (content types, permissions) that no steady-state client
    pays, and counting them would bury the thing under test.
    """
    await schema.execute(query, context_value=_fresh_request(ctx))

    counted = _fresh_request(ctx)
    with QueryCounter() as counter:
        result = await schema.execute(query, context_value=counted)
    assert not result.errors, result.errors
    return result.data, len(counter)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scene_placements_are_flat_in_layer_count(authenticated_context: HttpContext):
    """Asking a scene for its layers' placements costs the same at 7 layers as at 3."""
    await _seed_scene(authenticated_context, layer_count=3)
    small_data, small_queries = await _execute(authenticated_context, SCENE_PLACEMENTS)

    await models.Scene.objects.all().adelete()
    await _seed_scene(authenticated_context, layer_count=7)
    large_data, large_queries = await _execute(authenticated_context, SCENE_PLACEMENTS)

    assert len(small_data["scenes"][0]["layers"]) == 3
    assert len(large_data["scenes"][0]["layers"]) == 7
    assert large_queries == small_queries, f"the placement query count grows with the layers: {small_queries} for 3 layers, {large_queries} for 7"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_root_layers_are_flat_in_layer_count(authenticated_context: HttpContext):
    """The same, through the root `layers` field rather than through a scene."""
    await _seed_scene(authenticated_context, layer_count=3)
    small_data, small_queries = await _execute(authenticated_context, ROOT_LAYERS)

    await models.Scene.objects.all().adelete()
    await _seed_scene(authenticated_context, layer_count=7)
    large_data, large_queries = await _execute(authenticated_context, ROOT_LAYERS)

    assert len(small_data["layers"]) == 3
    assert len(large_data["layers"]) == 7
    assert large_queries == small_queries, f"the root layer query count grows with the layers: {small_queries} for 3 layers, {large_queries} for 7"


SPACE_PLACED = """
query SpacePlaced { coordinateSystems { id placedSystems { id } } }
"""

SPACE_PLACED_AND_ANNOTATED = """
query SpaceBoth { coordinateSystems { id placedSystems { id } annotations { id } } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_spaces_placeable_set_is_walked_once_per_request(authenticated_context: HttpContext):
    """`placedSystems` and `annotations` answer from one walk of the space, not one each.

    Both derive from `placeable_system_ids_in`, which is a registrations fetch, a residence
    map, a descendant closure, a lineage-closed edge fetch and a reverse BFS. Asking a *list*
    of systems for both fields is 2N of those without a memo -- and these two fields used to
    live on `Scene`, where they shared the scene graph and cost nothing extra together.

    Asserted as "both costs the same as one, plus the annotation reads": a strict equality
    would be pinning how many queries annotations themselves take, which is not the point.
    """
    for index in range(3):
        dataset = await seed.create_array_dataset(authenticated_context, f"Placed{index}")
        scene = await seed.create_scene(authenticated_context, f"Scene{index}")
        await seed.register_into_scene(authenticated_context, scene, dataset)

    _one_data, one_queries = await _execute(authenticated_context, SPACE_PLACED)
    both_data, both_queries = await _execute(authenticated_context, SPACE_PLACED_AND_ANNOTATED)

    assert len(both_data["coordinateSystems"]) >= 6, "the fixture must cover several spaces, or N walks and one look alike"
    extra = both_queries - one_queries
    assert extra <= len(both_data["coordinateSystems"]), (
        f"adding `annotations` cost {extra} queries over {len(both_data['coordinateSystems'])} spaces: "
        "the placeable set is being walked a second time per space instead of read from the request memo"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_scenes_over_one_world_share_the_root_edge_fetch(authenticated_context: HttpContext):
    """The world's edges are fetched once per request, not once per scene composing over it.

    A scene's searchable universe is its world's edges plus its layers' datasets' facts, and
    the first half is the *world's* -- identical for every scene over it. Listing scenes would
    otherwise refetch it per scene, which is the same N+1 as the per-layer one, one level up.

    The `SpaceGraph` assertion is the other half of the rule, and it is not a limitation: that
    graph hands back whole containers, so it scopes its edges to the organization, and
    organization-scoped rows are simply not the same rows. The key carries the scoping so the
    two cannot silently share.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Shared")
    scene_a = await seed.create_scene(authenticated_context, "A")
    await seed.register_into_scene(authenticated_context, scene_a, dataset)

    def check() -> None:
        scene_b = models.Scene.objects.create(name="B", world=scene_a.world, organization=authenticated_context.request.organization)
        loaders: dict = {}

        a = scene_graph.SceneGraph(scene_a, loaders=loaders)
        b = scene_graph.SceneGraph(scene_b, loaders=loaders)
        assert a.universe.root_edges is b.universe.root_edges, "two scenes over one world must share the world's edge fetch"
        assert len(loaders["space_root_edges"]) == 1

        scoped = space_graph.SpaceGraph(scene_a.world, organization=authenticated_context.request.organization, loaders=loaders)
        assert scoped.universe.root_edges is not a.universe.root_edges, "an organization-scoped fetch is a different set of rows"
        assert len(loaders["space_root_edges"]) == 2, "so it gets its own key rather than reusing the unscoped one"

    await sync_to_async(check)()


CREATE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_creating_a_layer_is_flat_in_scene_size(authenticated_context: HttpContext):
    """Placing one more layer costs the same in a 7-layer scene as in a 3-layer one.

    The placement check a layer mutation runs (is this source placeable in this scene?)
    must fetch a universe whose size depends on the dataset and the world, not on the
    layer count -- the full SceneGraph, which walks every layer and every co-tenant
    dataset's edges, would make assembling a scene slower with every layer already in it.
    """

    async def measure(layer_count: int) -> int:
        scene = await _seed_scene(authenticated_context, layer_count=layer_count)
        # A fresh dataset, registered as its own seed step: the measured mutation is the
        # layer creation alone, and its placement check runs the full BFS in both scenes.
        dataset = await seed.create_array_dataset(authenticated_context, f"Incoming{layer_count}", shapes=_SHAPES)
        lens = await seed.create_lens(authenticated_context, dataset, slices=[])
        await seed.register_into_scene(authenticated_context, scene, dataset)

        variables = {"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": "c"}}

        counted = _fresh_request(authenticated_context)
        with QueryCounter() as counter:
            result = await schema.execute(CREATE_LAYER, context_value=counted, variable_values=variables)
        assert not result.errors, result.errors
        return len(counter)

    # Warm the process-lifetime caches (content types, auth) on a throwaway scene first.
    await measure(1)
    await models.Scene.objects.all().adelete()
    await models.ArrayDataset.objects.all().adelete()

    small_queries = await measure(3)
    await models.Scene.objects.all().adelete()
    await models.ArrayDataset.objects.all().adelete()

    large_queries = await measure(7)
    assert large_queries == small_queries, f"creating a layer costs more in a bigger scene: {small_queries} queries at 3 layers, {large_queries} at 7"

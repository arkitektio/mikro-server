"""A shared space, and a scene materialized from what was registered into it.

``createCoordinateSystem`` creates a shared space -- the one ownerless (SHARED) coordinate system -- and
authors, explicitly, one edge per source registered into it. ``createSceneFromCoordinateSystem``
then builds a scene that *adopts* the space as its world and turns those already-registered
sources into layers, up to a policy's ``nchildren``. It authors no edges at all -- it never
fabricates a placement, which is the pivot these tests hold it to.

The load-bearing behaviour: a registration into a shared space is the space's fact and places
its source in *every* scene over that space, so a layer is placed exactly when its source->space
registration exists -- and its path to world is that one edge. A layer whose registration
was never authored would pass creation yet resolve ``pathToWorld`` to null. Every image test asserts
a non-null path, not merely that the mutation did not raise.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) {
    id name residents { __typename }
    axes { name type unit }
  }
}
"""

FROM_CS = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) {
    id name
    worldCoordinateSystem { id  axes { name unit } registrations { id kind name } }
    layers {
      id kind
      placement
      placementValidity
      pathToWorld { inverted transformation { id kind } }
    }
  }
}
"""

#: The space's axes: a purely spatial y/x world in micrometres.
ATLAS_AXES = [
    {"name": "y", "type": "SPACE", "unit": "micrometer"},
    {"name": "x", "type": "SPACE", "unit": "micrometer"},
]


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity, so the scene-graph memo cannot go stale."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


def _register(source_field: str, source_id: str, axes=("y", "x"), validity: str = "VALIDATED") -> dict:
    """A registration path: place a source into the space by a BY_DIMENSION edge on shared axes."""
    return {
        source_field: source_id,
        "transform": {
            "kind": "BY_DIMENSION",
            "inputAxes": list(axes),
            "outputAxes": list(axes),
        },
        "validity": validity,
    }


async def _create_space(ctx: HttpContext, name: str, registrations: list[dict]) -> dict:
    result = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": name, "axes": ATLAS_AXES, "registrations": registrations}},
    )
    assert not result.errors, result.errors
    return result.data["createCoordinateSystem"]


async def _scene_from(ctx: HttpContext, space_id: str, **policy) -> dict:
    result = await schema.execute(
        FROM_CS,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"coordinateSystem": space_id, "policy": policy}},
    )
    assert not result.errors, result.errors
    return result.data["createSceneFromCoordinateSystem"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_places_every_registered_dataset_in_the_scene(authenticated_context: HttpContext):
    """The whole point: sources registered into the space become placed image layers.

    The scene adopts the space as its world -- nothing is authored -- and each
    dataset's registration edge composes directly, so every layer resolves a real
    ``pathToWorld`` of exactly the one authored hop.
    """
    a = await seed.create_array_dataset(authenticated_context, "A", shapes=[[3, 64, 64]])
    b = await seed.create_array_dataset(authenticated_context, "B", shapes=[[3, 64, 64]])

    space = await _create_space(
        authenticated_context,
        "Atlas",
        [_register("dataset", str(a.pk)), _register("dataset", str(b.pk))],
    )
    assert space["residents"] == [], "a space built to be registered into holds nothing of its own"

    # Two registration edges land in the space, one per dataset.
    registered = await sync_to_async(models.Transformation.objects.filter(output__pk=space["id"], parent__isnull=True).count)()
    assert registered == 2

    scene = await _scene_from(authenticated_context, space["id"])

    assert [(ax["name"], ax["unit"]) for ax in scene["worldCoordinateSystem"]["axes"]] == [("y", "micrometer"), ("x", "micrometer")]

    # Six image layers -- three channels each -- and EVERY one is placed: a non-null path is
    # the space's registration composing. The channels of one dataset share its registration:
    # placement is a fact about the data, and splitting the view of it changes no fact.
    assert len(scene["layers"]) == 6
    for layer in scene["layers"]:
        assert layer["kind"] == "INTENSITY"
        assert layer["placement"] == "PLACED"
        assert layer["pathToWorld"] is not None, "the source->space edge was not composed into the scene"
        assert layer["placementValidity"] == "VALIDATED"
        assert layer["pathToWorld"][-1]["transformation"]["kind"] == "BY_DIMENSION"

    # The space holds exactly the two source registrations: the world IS the space, so
    # nothing was authored on the way in, and each path is the one hop the space already held.
    assert len(scene["worldCoordinateSystem"]["registrations"]) == 2
    assert scene["worldCoordinateSystem"]["id"] == space["id"], "the scene adopts the space as its world"
    for layer in scene["layers"]:
        assert len(layer["pathToWorld"]) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nchildren_caps_the_sources_in_registration_order(authenticated_context: HttpContext):
    """``nchildren`` is a flat cap on *sources*, honoured in the order the registrations were authored."""
    datasets = [await seed.create_array_dataset(authenticated_context, name, shapes=[[3, 32, 32]]) for name in ("A", "B", "C")]
    space = await _create_space(
        authenticated_context,
        "Capped",
        [_register("dataset", str(dataset.pk)) for dataset in datasets],
    )

    scene = await _scene_from(authenticated_context, space["id"], nchildren=2)

    assert len(scene["layers"]) == 6, "two of the three sources, whole: three channels apiece"
    # The first two registrations (by pk) are the ones taken.
    placed_lens_dataset_ids = await sync_to_async(
        lambda: set(models.Layer.objects.filter(scene__pk=scene["id"]).values_list("lens__dataset__pk", flat=True))
    )()
    assert placed_lens_dataset_ids == {datasets[0].pk, datasets[1].pk}, "nchildren limits how many sources are materialized"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nchildren_counts_sources_and_never_truncates_an_acquisition(authenticated_context: HttpContext):
    """The cap counts sources, so a multi-channel dataset arrives whole or not at all.

    A dataset materializes one layer per channel, and a cap counted in *layers* would stop
    halfway through one: a scene showing two channels of a four-channel acquisition, with
    nothing saying the other two exist. Overshooting the cap is the cheaper wrong answer.
    """
    datasets = [
        await seed.create_array_dataset(
            authenticated_context,
            name,
            axes=[seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
            shapes=[[4, 32, 32]],
        )
        for name in ("A", "B")
    ]
    space = await _create_space(
        authenticated_context,
        "Fourplexes",
        [_register("dataset", str(dataset.pk)) for dataset in datasets],
    )

    scene = await _scene_from(authenticated_context, space["id"], nchildren=1)

    assert len(scene["layers"]) == 4, "one source was taken, and all four of its channels came with it"
    taken = await sync_to_async(lambda: set(models.Layer.objects.filter(scene__pk=scene["id"]).values_list("lens__dataset__pk", flat=True)))()
    assert taken == {datasets[0].pk}, "the cap is one source, not one layer"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_transform_tables_gates_table_layers(authenticated_context: HttpContext):
    """A registered table dataset becomes a point layer only when the policy asks for it."""
    store = await sync_to_async(models.ParquetStore.objects.create)(path="s3://parquet/mols", bucket="parquet", key="mols", organization=authenticated_context.request.organization, populated=True, columns=[{"name": n, "type": "DOUBLE", "nullable": True} for n in ("y", "x")])
    created = await schema.execute(
        """
        mutation Create($input: CreateTableDatasetInput!) {
          createTableDataset(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "data": str(store.pk),
                "name": "molecules",
                "columns": [{"name": "y", "dtype": "DOUBLE", "axisType": "SPACE", "unit": "micrometer"}, {"name": "x", "dtype": "DOUBLE", "axisType": "SPACE", "unit": "micrometer"}],
            }
        },
    )
    assert not created.errors, created.errors
    table_id = created.data["createTableDataset"]["id"]

    space = await _create_space(authenticated_context, "Tables", [_register("tableDataset", table_id)])

    off = await _scene_from(authenticated_context, space["id"], transformTables=False)
    assert off["layers"] == [], "a table is skipped unless transform_tables is set"

    on = await _scene_from(authenticated_context, space["id"], transformTables=True)
    (layer,) = on["layers"]
    assert layer["kind"] == "POINT"
    assert layer["pathToWorld"] is not None, "the table's registration edge was not composed into the scene"
    assert layer["placement"] == "PLACED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_include_meshes_gates_mesh_layers(authenticated_context: HttpContext):
    """A registered mesh collection becomes a mesh layer unless the policy excludes it."""
    dataset = await seed.create_array_dataset(authenticated_context, "Meshed", axes=seed.YX_AXES, shapes=[[64, 64]])
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    axes = await sync_to_async(lambda: [{"name": axis.name, "type": axis.type} for axis in system.axes.all()])()

    store = await seed.create_fabriks_store(authenticated_context)
    created = await schema.execute(
        """
        mutation Create($input: CreateMeshCollectionInput!) {
          createMeshCollection(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={
                "input": {
                    "axes": axes,
                    "derivedFrom": [{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "IDENTITY"}}],
                    "version": "v1",
                    "store": str(store.pk),
                }
            },
    )
    assert not created.errors, created.errors
    collection_id = created.data["createMeshCollection"]["id"]

    space = await _create_space(authenticated_context, "Meshes", [_register("meshCollection", collection_id)])

    off = await _scene_from(authenticated_context, space["id"], includeMeshes=False)
    assert off["layers"] == [], "a mesh collection is skipped when include_meshes is off"

    on = await _scene_from(authenticated_context, space["id"], includeMeshes=True)
    (layer,) = on["layers"]
    assert layer["kind"] == "MESH"
    assert layer["pathToWorld"] is not None
    assert layer["placement"] == "PLACED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_scenes_world_seeds_a_scene(authenticated_context: HttpContext):
    """A scene never owns its world, so another scene's world is adoptable like any shared space.

    There is no kind to smuggle in on creation -- the input carries no kind field at
    all, so 'creating an owned system' is unrepresentable rather than validated. And
    since no scene owns a space, the old refusal of "another scene's minted world" has
    nothing left to refuse: two scenes over one space is the ordinary case.
    """
    sdl = schema.as_str()
    input_def = sdl[sdl.find("input CreateCoordinateSystemInput ") : sdl.find("}", sdl.find("input CreateCoordinateSystemInput "))]
    assert "kind" not in input_def, "a shared space's kind is decided by its (absent) ownership, not an input"

    scene = await seed.create_scene(authenticated_context, "Plain")
    world = await sync_to_async(lambda: scene.world)()
    second = await schema.execute(
        FROM_CS,
        context_value=authenticated_context,
        variable_values={"input": {"coordinateSystem": str(world.pk), "policy": {}}},
    )
    assert not second.errors, second.errors
    assert second.data["createSceneFromCoordinateSystem"]["worldCoordinateSystem"]["id"] == str(world.pk), "two scenes, one space"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_rerun_makes_a_second_scene_and_leaves_the_space_untouched(authenticated_context: HttpContext):
    """Everything created is ordinary: run it twice and there are two scenes; the registrations are one copy."""
    dataset = await seed.create_array_dataset(authenticated_context, "Once", shapes=[[3, 48, 48]])
    space = await _create_space(authenticated_context, "Reused", [_register("dataset", str(dataset.pk))])

    first = await _scene_from(authenticated_context, space["id"])
    second = await _scene_from(authenticated_context, space["id"])
    assert first["id"] != second["id"]
    # Two scenes, one space: both adopt the very same space as their world.
    assert first["worldCoordinateSystem"]["id"] == second["worldCoordinateSystem"]["id"] == space["id"]

    # The space's own registration edges are untouched -- still exactly one, shared, not
    # copied per scene. Nothing is authored per run: the scene composes over the space itself.
    registered = await sync_to_async(models.Transformation.objects.filter(output__pk=space["id"], parent__isnull=True).count)()
    assert registered == 1
    for scene in (first, second):
        assert len(scene["layers"]) == 3, "the one source, one layer per channel"
        for layer in scene["layers"]:
            assert layer["pathToWorld"] is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_non_renderable_dataset_is_skipped_not_fatal(authenticated_context: HttpContext):
    """A registered dataset too small to render is skipped, exactly like a table with too few
    coordinate columns -- it does not abort the batch and place nothing."""
    good = await seed.create_array_dataset(authenticated_context, "Good", shapes=[[3, 64, 64]])
    # x is a single pixel: not renderable.
    tiny = await seed.create_array_dataset(authenticated_context, "Tiny", axes=seed.YX_AXES, shapes=[[64, 1]])

    space = await _create_space(
        authenticated_context,
        "Mixed",
        [_register("dataset", str(tiny.pk)), _register("dataset", str(good.pk))],
    )

    scene = await _scene_from(authenticated_context, space["id"])

    # Only the renderable one becomes layers; the tiny one is silently skipped.
    assert len(scene["layers"]) == 3, "the good dataset's three channels, and nothing from the tiny one"
    for layer in scene["layers"]:
        assert layer["kind"] == "INTENSITY"
        assert layer["pathToWorld"] is not None
    placed_dataset_ids = await sync_to_async(lambda: set(models.Layer.objects.filter(scene__pk=scene["id"]).values_list("lens__dataset_id", flat=True)))()
    assert placed_dataset_ids == {good.pk}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_calibrated_dataset_registers_through_its_physical_system(authenticated_context: HttpContext):
    """The natural calibrated workflow: register a dataset by its PHYSICAL calibration, not its
    intrinsic pixels. The layer's real source is intrinsic, yet its path to world resolves --
    the walk crosses the dataset's own (non-member) calibration edge to reach the composed
    PHYSICAL->space registration."""
    dataset = await seed.create_array_dataset(authenticated_context, "Cal", axes=seed.YX_AXES, shapes=[[64, 64]])
    await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        scale=[0.325, 0.325],
    )
    physical = await sync_to_async(lambda: graph_logic.physical_neighbours(dataset.coordinate_system)[0])()

    space = await _create_space(authenticated_context, "PhysAtlas", [_register("coordinateSystem", str(physical.pk))])

    scene = await _scene_from(authenticated_context, space["id"])

    (layer,) = scene["layers"]
    assert layer["kind"] == "INTENSITY"
    assert layer["placement"] == "PLACED"
    assert layer["pathToWorld"] is not None, "the layer's intrinsic source could not reach world through the PHYSICAL registration"


def test_the_schema_exposes_the_shared_space_mutations():
    """The SDL is the contract: both mutations exist, and the shared kind is created directly."""
    sdl = schema.as_str()
    assert "createCoordinateSystem(" in sdl
    assert "createSceneFromCoordinateSystem(" in sdl
    assert "input CreateSceneFromCoordinateSystemInput " in sdl
    assert "input ScenePolicyInput " in sdl

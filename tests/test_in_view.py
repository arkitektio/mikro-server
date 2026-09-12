"""What is in view of a region asked in a coordinate system, and which anchors go with it.

Two things this pins, and they pull in opposite directions.

**An extent is partial.** Composing at one fixed rank is right inside a dataset -- a level,
a lens and a calibration all keep its axes -- and wrong across a registration. A (c,y,x)
dataset registered onto the (y,x) of a (z,y,x) world is a *slab*, extended along z, and a
number written for z would cull it out of every view it is really in. So the extent names
the axes it constrains and stays silent about the rest, and the overlap test is a
conjunction over the axes both sides name.

**A source is never culled for being unbounded.** A mesh collection's vertices are in
Parquet the server never opens, and a warp field on the path has no closed form to push a
box through. Both come back with a state saying so and a full path, because refusing to
bound something is not the same as knowing it is out of view -- the same distinction
`placementState` draws beside a null `pathToWorld`.

Nothing here is stored. Refining a registration moves every extent that looks through it,
which is the property `test_refining_a_registration_moves_the_extent` exists to hold.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import coords as coords_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_mesh_placement import _mesh_collection
from tests.test_placement_queries import QueryCounter, _fresh_request

SPATIAL_AXES = [
    seed.axis("z", enums.AxisType.SPACE),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

IN_VIEW = """
query InView($id: ID!, $region: BoundingBoxInput!) {
  coordinateSystem(id: $id) {
    inView(region: $region) {
      extentState
      invariance
      validity
      extent { axis min max }
      system { id }
      path { inverted transformation { id kind } }
      source {
        ... on ArrayDataset { id name }
        ... on MeshCollection { id version }
        ... on AnnotationCollection { id name }
      }
      anchors { id coordinates }
    }
  }
}
"""


async def _in_view(ctx: HttpContext, system_id: str, mins: list[float], maxs: list[float]) -> list[dict]:
    result = await schema.execute(
        IN_VIEW,
        context_value=_fresh_request(ctx),
        variable_values={"id": str(system_id), "region": {"min": mins, "max": maxs}},
    )
    assert not result.errors, result.errors
    return result.data["coordinateSystem"]["inView"]


def _extent(hit: dict) -> dict[str, list[float]]:
    return {entry["axis"]: [entry["min"], entry["max"]] for entry in hit["extent"]}


# --- the extent -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_changing_registration_constrains_only_the_axes_it_names(authenticated_context: HttpContext):
    """A (c,y,x) dataset in a (z,y,x) world is a slab: bounded in y and x, free in z.

    The headline, and the case that fails without axis-aware composition -- `to_matrix` has
    no BY_DIMENSION branch, which is the kind every ordinary registration is written as.

    ABLATION: write a 0 for the unconstrained z and a region anywhere else in z culls this
    dataset away, silently, while looking exactly like a correct answer.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Slab", shapes=[[3, 64, 64]])  # (c, y, x)
    scene = await seed.create_scene(authenticated_context, "Slab scene")  # (z, y, x)
    await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [0.0, 0.0, 0.0], [10.0, 10.0, 10.0])

    assert hit["extentState"] == "KNOWN"
    assert _extent(hit) == {"y": [-0.5, 63.5], "x": [-0.5, 63.5]}, "z is not constrained by a registration that never mentions it"
    assert hit["source"]["name"] == "Slab"

    far = await _in_view(authenticated_context, world_id, [900.0, 0.0, 0.0], [910.0, 10.0, 10.0])
    assert len(far) == 1, "a region far away in the one axis the registration says nothing about must not cull the source"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_extent_is_the_half_voxel_box_of_the_level_zero_array(authenticated_context: HttpContext):
    """Voxel n covers [n-0.5, n+0.5), so a shape of S spans [-0.5, S-0.5] -- not [0, S]."""
    dataset = await seed.create_array_dataset(authenticated_context, "Boxed", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Boxed scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [-100.0, -100.0, -100.0], [100.0, 100.0, 100.0])
    assert _extent(hit) == {"z": [-0.5, 7.5], "y": [-0.5, 63.5], "x": [-0.5, 63.5]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refining_a_registration_moves_the_extent(authenticated_context: HttpContext):
    """The extent is derived, so one edge write moves it -- with no write to any source.

    The twin of `test_placement_validity`'s "fixing one edge fixes every layer". A stored
    per-source box would need a fan-out here, and the fan-out is what goes missing.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Refined", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Refined scene")
    edge = await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    before = await _in_view(authenticated_context, world_id, [-100.0] * 3, [100.0] * 3)
    assert _extent(before[0])["x"] == [-0.5, 63.5]

    def refine() -> None:
        child = edge.children.first()
        child.kind = enums.TransformKindChoices.SCALE.value
        child.params = {"scale": [2.0, 2.0, 2.0]}
        child.save(update_fields=["kind", "params"])

    await sync_to_async(refine)()

    after = await _in_view(authenticated_context, world_id, [-200.0] * 3, [200.0] * 3)
    assert _extent(after[0])["x"] == [-1.0, 127.0], "refining the edge moved every extent that looks through it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_queried_system_is_in_view_of_itself(authenticated_context: HttpContext):
    """A source whose own system IS the queried one: empty path, exact by construction."""
    dataset = await seed.create_array_dataset(authenticated_context, "Rooted", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    intrinsic_id = await sync_to_async(lambda: dataset.intrinsic_coordinate_system.pk)()

    (hit,) = await _in_view(authenticated_context, intrinsic_id, [0.0, 0.0, 0.0], [10.0, 10.0, 10.0])
    assert hit["path"] == []
    assert hit["validity"] == "VALIDATED"
    assert hit["invariance"] == "ISOMETRY"
    assert _extent(hit) == {"z": [-0.5, 7.5], "y": [-0.5, 63.5], "x": [-0.5, 63.5]}


# --- the states -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_collection_is_returned_unbounded(authenticated_context: HttpContext):
    """Its vertices are in Parquet the server never opens, so it is unbounded -- never culled."""
    dataset = await seed.create_array_dataset(authenticated_context, "Meshed", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Mesh scene")
    collection = await _mesh_collection(authenticated_context, dataset)
    system = await sync_to_async(lambda: collection.coordinate_system)()
    await seed.register_into_scene(authenticated_context, scene, system=system)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    hits = await _in_view(authenticated_context, world_id, [900.0, 900.0, 900.0], [910.0, 910.0, 910.0])
    meshes = [hit for hit in hits if hit["source"].get("version")]
    assert len(meshes) == 1, "a source the server cannot bound is not a source it knows to be out of view"
    assert meshes[0]["extentState"] == "UNREADABLE"
    assert meshes[0]["extent"] == []
    assert meshes[0]["anchors"] == [], "only an array dataset has anchors"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_registration_is_not_in_view(authenticated_context: HttpContext):
    """The one genuine exclusion: a declared non-correspondence is not a placement at all."""
    dataset = await seed.create_array_dataset(authenticated_context, "Unmapped", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Unmapped scene")

    def author_unmappable() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.UNMAPPABLE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={},
            creator=authenticated_context.request.user,
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(author_unmappable)()
    world_id = await sync_to_async(lambda: scene.world.pk)()

    assert await _in_view(authenticated_context, world_id, [-100.0] * 3, [100.0] * 3) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_backwards_registration_keeps_its_path_and_states_why_it_has_no_extent(authenticated_context: HttpContext):
    """Composed forward only -- so an inverted step is reported, not silently culled.

    The step IS invertible (the search offers a backwards step only when it is), so the
    client inverts the flagged edge itself. The server declining to do arithmetic is not the
    same as the map having no inverse.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Backwards", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Backwards scene")

    def author_reverse() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.ROTATION.value,
            input=scene.world,
            output=dataset.intrinsic_coordinate_system,
            params={"affine": [[0.0, -1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]},
            creator=authenticated_context.request.user,
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(author_reverse)()
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [-100.0] * 3, [100.0] * 3)
    assert hit["extentState"] == "INVERTED"
    assert hit["extent"] == []
    assert any(step["inverted"] for step in hit["path"]), "the path is still returned, flagged, for the client to invert"


# --- the region -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_region_that_misses_the_source_returns_nothing(authenticated_context: HttpContext):
    """The cull actually culls, on an axis the registration does constrain."""
    dataset = await seed.create_array_dataset(authenticated_context, "Elsewhere", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Elsewhere scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    assert await _in_view(authenticated_context, world_id, [500.0, 500.0, 500.0], [600.0, 600.0, 600.0]) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_degenerate_region_probes_a_plane(authenticated_context: HttpContext):
    """min == max is the probe a client sends to ask what is under this slice; bounds are closed."""
    dataset = await seed.create_array_dataset(authenticated_context, "Sliced", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Sliced scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    assert len(await _in_view(authenticated_context, world_id, [4.0, 4.0, 4.0], [4.0, 4.0, 4.0])) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shorter_region_constrains_only_its_leading_axes(authenticated_context: HttpContext):
    """A 2D box asked of a 3D space says nothing about the third axis, rather than pinning it to zero."""
    dataset = await seed.create_array_dataset(authenticated_context, "Prefix", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Prefix scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    assert len(await _in_view(authenticated_context, world_id, [0.0, 0.0], [4.0, 4.0]) ) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_region_longer_than_the_system_is_refused(authenticated_context: HttpContext):
    """Naming more axes than the system has is a client error, not an empty result."""
    scene = await seed.create_scene(authenticated_context, "Narrow")
    world_id = await sync_to_async(lambda: scene.world.pk)()

    result = await schema.execute(
        IN_VIEW,
        context_value=_fresh_request(authenticated_context),
        variable_values={"id": str(world_id), "region": {"min": [0.0] * 5, "max": [1.0] * 5}},
    )
    assert result.errors, "a region of five axes over a three-axis system must not silently succeed"


# --- anchors ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_pinned_outside_the_region_is_culled(authenticated_context: HttpContext):
    """The one that exercises the cull: a pinned axis the region *does* constrain.

    The dataset's z is registered onto the world's z, so each anchor's slab is one voxel wide
    there and a region covering only the first plane must reject the anchor at z=7. This is
    the test that puts the second walk, the composed forms and the half-voxel slab under load
    -- an anchor pinned on an axis the world does not have (below) can never fail any of them.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Stacked", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Stacked scene")

    def anchors() -> None:
        for value in (0, 7):
            models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={"z": value})

    await anchors_and_register(authenticated_context, scene, dataset, anchors)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [-1.0, -100.0, -100.0], [1.0, 100.0, 100.0])
    assert [anchor["coordinates"] for anchor in hit["anchors"]] == [{"z": 0}], "the anchor seven planes away is not in a region one plane deep"

    (whole,) = await _in_view(authenticated_context, world_id, [-100.0] * 3, [100.0] * 3)
    assert len(whole["anchors"]) == 2, "and both are in view of a region covering the stack"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_on_an_axis_the_space_does_not_have_is_never_culled(authenticated_context: HttpContext):
    """A (t,y,x) dataset in a (z,y,x) world: nothing constrains t, so no region can reject its anchors."""
    axes = [seed.axis("t", enums.AxisType.TIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
    dataset = await seed.create_array_dataset(authenticated_context, "Timed", axes=axes, shapes=[[10, 64, 64]])
    scene = await seed.create_scene(authenticated_context, "Timed scene")

    def anchors() -> None:
        for value in (0, 9):
            models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={"t": value})

    await anchors_and_register(authenticated_context, scene, dataset, anchors)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [0.0, 0.0, 0.0], [10.0, 10.0, 10.0])
    assert len(hit["anchors"]) == 2, "an axis the space does not have cannot cull an anchor"


async def anchors_and_register(ctx, scene, dataset, make_anchors) -> None:
    """Create the anchors and register the dataset, both off the event loop."""
    await sync_to_async(make_anchors)()
    await seed.register_into_scene(ctx, scene, dataset)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_that_pins_no_registered_axis_is_in_view_whenever_its_container_is(authenticated_context: HttpContext):
    """A channel label is everywhere: it pins c, and the region says nothing about c."""
    dataset = await seed.create_array_dataset(authenticated_context, "Channelled", shapes=[[3, 64, 64]])  # (c, y, x)
    scene = await seed.create_scene(authenticated_context, "Channelled scene")

    def anchors() -> None:
        models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={"c": 0})

    await anchors_and_register(authenticated_context, scene, dataset, anchors)
    world_id = await sync_to_async(lambda: scene.world.pk)()

    (hit,) = await _in_view(authenticated_context, world_id, [0.0, 0.0, 0.0], [10.0, 10.0, 10.0])
    assert [anchor["coordinates"] for anchor in hit["anchors"]] == [{"c": 0}]


# --- cost -------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_query_count_does_not_grow_with_the_sources(authenticated_context: HttpContext):
    """A flat cost in the number of registered sources -- the property the whole module exists for.

    Flat in the source *count*, on flat lineages: the derivation and lineage closures cost one
    query per generation, which is bounded by the shape of the data rather than by how much of
    it there is. Every source here carries anchors, so the anchor resolution -- which returns
    early when a dataset has none -- is measured doing its actual work rather than bailing.
    """

    async def build(name: str, count: int) -> str:
        scene = await seed.create_scene(authenticated_context, name)
        for index in range(count):
            dataset = await seed.create_array_dataset(authenticated_context, f"{name}-{index}", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
            await sync_to_async(models.CoordinateAnchor.objects.create)(dataset=dataset, coordinates={"z": 0})
            await seed.register_into_scene(authenticated_context, scene, dataset)
        return str(await sync_to_async(lambda: scene.world.pk)())

    small = await build("Small", 2)
    large = await build("Large", 6)

    # Warmed first, and measured on separate requests after: the first execution in a process
    # pays one-off costs (content types, permissions) that no steady-state client pays, and
    # counting them would bury the thing under test.
    await _in_view(authenticated_context, small, [-100.0] * 3, [100.0] * 3)

    counts = []
    for world_id in (small, large):
        with QueryCounter() as counter:
            hits = await _in_view(authenticated_context, world_id, [-100.0] * 3, [100.0] * 3)
        counts.append(len(counter.queries))

    assert len(hits) == 6, "the larger space really did have more sources in view"
    assert counts[0] == counts[1], f"the cost grew with the sources: {counts[0]} then {counts[1]}"


# --- the closed form --------------------------------------------------------------


def test_the_closed_form_interval_agrees_with_the_corner_enumeration():
    """`form_interval` is the O(n) shortcut for what `transformed_bbox` does with 2**n corners.

    Pinned rather than argued: "obviously equivalent" is how a sign error survives. A shear
    and a negative scale are included because those are where taking min/max of the two
    extreme corners alone would go wrong.
    """
    mins, maxs = [-1.0, 2.0, 0.0], [3.0, 5.0, 4.0]
    matrix = [
        [1.0, 0.5, 0.0, 7.0],
        [0.0, -2.0, 0.0, -1.0],
        [0.3, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    reference = coords_logic.transformed_bbox(mins, maxs, [(enums.TransformKindChoices.AFFINE.value, {"affine": matrix})])

    forms = {
        name: coords_logic.AxedForm(coefficients=tuple(matrix[index][:3]), constant=matrix[index][3])
        for index, name in enumerate(["a", "b", "c"])
    }
    closed = coords_logic.axed_bbox(mins, maxs, forms)

    for index, name in enumerate(["a", "b", "c"]):
        assert closed[name][0] == pytest.approx(reference["min"][index])
        assert closed[name][1] == pytest.approx(reference["max"][index])


def test_boxes_overlap_ignores_the_axes_only_one_side_names():
    """Silence is not a zero: an axis one side does not constrain cannot exclude anything."""
    slab = {"y": [0.0, 10.0], "x": [0.0, 10.0]}
    assert coords_logic.boxes_overlap(slab, {"z": [900.0, 910.0], "y": [1.0, 2.0], "x": [1.0, 2.0]})
    assert not coords_logic.boxes_overlap(slab, {"y": [90.0, 95.0], "x": [1.0, 2.0]})
    assert coords_logic.boxes_overlap(slab, {"y": [10.0, 10.0], "x": [0.0, 0.0]}), "closed bounds, so a plane probe on the edge still meets it"

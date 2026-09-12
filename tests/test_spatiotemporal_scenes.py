"""A scene is spatio-temporal, and every edge in it says what its numbers mean.

Time was expressible on a world system long before this -- `Axis.type` has a TIME member
and the ordering rule already put it first -- but the default world had nowhere to put it,
so a timelapse either dropped its clock at the registration or invented a per-scene
convention for it. Making the default `(t, z, y, x)` is the easy half.

The hard half is rank. A `(t,z,y,x)` world and a `(c,y,x)` dataset do not have the same
number of axes, and a square edge between them cannot say "I know where y and x go, and
nothing about t or z". BY_DIMENSION is that statement, `inputAxes`/`outputAxes` are how it
is read back, and the rank checks are what stop the previous silence: a 3-vector into a
4-axis world used to be written without complaint and composed into the wrong matrix.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed

CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) {
    id
    worldCoordinateSystem { id  epoch axes { name type unit } }
  }
}
"""

REGISTER = """
mutation Register($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id kind inputAxes outputAxes }
}
"""

LAYER_PLACEMENT = """
query LayerPlacement($id: ID!) {
  scene(id: $id) {
    layers {
      id
      placementValidity
      pathToWorld {
        inverted
        transformation { id kind inputAxes outputAxes }
      }
    }
  }
}
"""


async def _create_scene(ctx: HttpContext, **payload) -> dict:
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": "S", **payload}})
    return result


# --- 1. the default world is spatio-temporal --------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_is_spatio_temporal_by_default(authenticated_context: HttpContext):
    """createScene with no axes gives a world with a clock: t, then z, y, x."""
    result = await _create_scene(authenticated_context)
    assert not result.errors, result.errors

    axes = result.data["createScene"]["worldCoordinateSystem"]["axes"]
    assert [axis["name"] for axis in axes] == ["t", "z", "y", "x"]
    assert [axis["type"] for axis in axes] == ["TIME", "SPACE", "SPACE", "SPACE"]
    # Time first is not cosmetic: the RFC-5 ordering requires it, and the render-axis
    # derivation reads x/y/z off the *position* of the spatial axes.
    assert axes[0]["unit"] == "second"
    assert {axis["unit"] for axis in axes[1:]} == {"micrometer"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_purely_spatial_scene_is_still_allowed(authenticated_context: HttpContext):
    """The clock is a default, not a mandate: an explicit axis list still wins."""
    result = await _create_scene(
        authenticated_context,
        axes=[
            {"name": "y", "type": "SPACE", "unit": "micrometer"},
            {"name": "x", "type": "SPACE", "unit": "micrometer"},
        ],
    )
    assert not result.errors, result.errors
    assert [axis["name"] for axis in result.data["createScene"]["worldCoordinateSystem"]["axes"]] == ["y", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_epoch_anchors_scene_time_to_the_wall_clock(authenticated_context: HttpContext):
    """`t` is a relative coordinate; the epoch is the one place absolute time enters.

    It lives on the *world system*, not the scene: it is the origin of the space's time
    axis, and two compositions over one space cannot disagree about when its clock starts.
    """
    result = await _create_scene(authenticated_context, epoch="2026-07-14T09:00:00+00:00")
    assert not result.errors, result.errors
    assert result.data["createScene"]["worldCoordinateSystem"]["epoch"].startswith("2026-07-14T09:00:00")

    # And it stays optional: a scene whose acquisition time is unknown still composes.
    unanchored = await _create_scene(authenticated_context)
    assert unanchored.data["createScene"]["worldCoordinateSystem"]["epoch"] is None


# --- 2. the unit must measure what the axis is ------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_unit_must_have_its_type_s_dimension(authenticated_context: HttpContext):
    """A TIME axis in micrometres is a lie the arithmetic would happily propagate."""
    result = await _create_scene(
        authenticated_context,
        axes=[
            {"name": "t", "type": "TIME", "unit": "micrometer"},
            {"name": "y", "type": "SPACE", "unit": "micrometer"},
            {"name": "x", "type": "SPACE", "unit": "micrometer"},
        ],
    )
    assert result.errors, "a TIME axis measured in micrometres must be rejected"
    assert "must measure [time]" in str(result.errors[0])

    # ...and the same in reverse.
    spatial_seconds = await _create_scene(
        authenticated_context,
        axes=[
            {"name": "y", "type": "SPACE", "unit": "second"},
            {"name": "x", "type": "SPACE", "unit": "micrometer"},
        ],
    )
    assert spatial_seconds.errors
    assert "must measure [length]" in str(spatial_seconds.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_arbitrary_units_remain_the_escape_hatch(authenticated_context: HttpContext):
    """'a.u.' is for an axis whose unit genuinely is arbitrary, and it survives the check."""
    result = await _create_scene(
        authenticated_context,
        axes=[
            {"name": "y", "type": "SPACE", "unit": "a.u."},
            {"name": "x", "type": "SPACE", "unit": "a.u."},
        ],
    )
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_has_at_most_one_clock(authenticated_context: HttpContext):
    """Two TIME axes are legal today -- they share an ordering rank, so nothing complains."""
    result = await _create_scene(
        authenticated_context,
        axes=[
            {"name": "t", "type": "TIME", "unit": "second"},
            {"name": "tau", "type": "TIME", "unit": "second"},
            {"name": "y", "type": "SPACE", "unit": "micrometer"},
            {"name": "x", "type": "SPACE", "unit": "micrometer"},
        ],
    )
    assert result.errors, "a world with two clocks renders one of them and drops the other"
    assert "at most one TIME axis" in str(result.errors[0])


# --- 3. rank: the checks that were never there ------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_mismatched_registration_is_rejected(authenticated_context: HttpContext):
    """A scale with the wrong number of entries used to be written, and composed, without complaint.

    The dataset is seeded (z,y,x) rather than the default (c,y,x) so that the *only* thing wrong
    with the edge below is the length of its vector: a per-axis transform between two systems
    that name their axes differently is now refused on the names first, and this test is about
    the count.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "DS", axes=seed.ZYX_AXES)  # (z, y, x)
    scene = await seed.create_scene(authenticated_context, "Sc")  # (z, y, x)

    def systems():
        return dataset.intrinsic_coordinate_system, scene.world

    intrinsic, world = await sync_to_async(systems)()

    result = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": str(world.pk),
                "transform": {
                    "kind": "SCALE",
                    "scale": [1.0, 1.0],  # two entries for a three-axis input system
                },
            }
        },
    )
    assert result.errors, "a scale with the wrong number of entries writes into the homogeneous corner"
    assert "one entry per input axis" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_by_dimension_places_a_dataset_of_a_different_rank(authenticated_context: HttpContext):
    """The point of the whole design: a (c,y,x) dataset in a (t,z,y,x) world.

    The edge names y and x, and says nothing about t, z or c -- which is exactly what is
    known. `inputAxes`/`outputAxes` come back on the edge, so a client composing the path
    knows which axes the numbers belong to without a side index of the scene's systems.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "DS")  # (c, y, x)

    scene_result = await _create_scene(authenticated_context)  # the new default: (t, z, y, x)
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]
    world_id = scene_result.data["createScene"]["worldCoordinateSystem"]["id"]

    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": world_id,
                "transform": {
                    "kind": "BY_DIMENSION",
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["y", "x"],
                    "affine": [[1.0, 0.0, 10.0], [0.0, 1.0, 20.0]],  # 2 rows, 2+1 columns: the named subset
                },
            }
        },
    )
    assert not result.errors, result.errors
    edge = result.data["createTransformation"]
    assert edge["kind"] == "BY_DIMENSION"
    assert edge["inputAxes"] == ["y", "x"]
    assert edge["outputAxes"] == ["y", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_by_dimension_must_name_axes_that_exist(authenticated_context: HttpContext):
    """The naming IS the map, so a name that does not resolve is not a typo, it is a broken edge."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")
    scene = await seed.create_scene(authenticated_context, "Sc")
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    result = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": str(world.pk),
                "transform": {
                    "kind": "BY_DIMENSION",
                    "inputAxes": ["q"],
                    "outputAxes": ["y"],
                },
            }
        },
    )
    assert result.errors
    assert "do not exist" in str(result.errors[0])


# --- 4. every edge states its own axis order --------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_edge_states_the_axis_order_its_numbers_are_written_in(authenticated_context: HttpContext):
    """`scale`/`translation`/`affine` are ordered by the INPUT system's axes, not the reader's axis names.

    A client that indexes them against a layer's axis names misplaces them whenever the two
    orders differ -- silently, since the numbers are all still there. So the edge carries
    the order, and no side index of the scene's coordinate systems is needed to recover it.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "DS")  # (c, y, x)
    # Sliced, so the lens owns a system and a lens->intrinsic edge sits on the path.
    # (An unsliced lens owns no system: its space is the intrinsic space itself.)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])

    scene_result = await _create_scene(authenticated_context)  # (t, z, y, x)
    scene_id = scene_result.data["createScene"]["id"]
    scene = await sync_to_async(models.Scene.objects.get)(pk=scene_id)
    await seed.register_into_scene(authenticated_context, scene, dataset)

    created = await schema.execute(
        """
        mutation Make($input: CreateIntensityLayerInput!) {
          createIntensityLayer(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(LAYER_PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    steps = result.data["scene"]["layers"][0]["pathToWorld"]
    assert steps, "the layer's dataset has a path to world"
    for step in steps:
        # Non-null on every edge, whatever its kind -- that is what lets the client drop
        # its axis-order index.
        assert step["transformation"]["inputAxes"], step
        assert step["transformation"]["outputAxes"], step

    # The lens->intrinsic edge's params are written in the lens system's axis order.
    first = steps[0]["transformation"]
    assert first["inputAxes"] == ["c", "y", "x"]


# --- 5. a layer needs a place before it enters a scene -----------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unplaced_layer_is_rejected_until_someone_registers_its_source(authenticated_context: HttpContext):
    """A layer whose source has no path to world is refused, with the fix in the error.

    Nothing fabricates a placement any more: the registration is authored explicitly --
    exactly once, by createTransformation into the world -- and the layer mutation only
    checks it is there. The authored edge is MANUAL, and the layer's derived validity
    says so.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "DS")  # (c, y, x)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    scene_result = await _create_scene(authenticated_context)  # (t, z, y, x)
    scene_id = scene_result.data["createScene"]["id"]

    make = """
    mutation Make($input: CreateIntensityLayerInput!) {
      createIntensityLayer(input: $input) { id placementValidity }
    }
    """
    variables = {"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}}

    refused = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert refused.errors, "an unplaced source must be refused, not quietly composed"
    assert "createTransformation" in str(refused.errors[0]), "the error names the mutation that closes the gap"

    scene = await sync_to_async(models.Scene.objects.get)(pk=scene_id)
    await seed.register_into_scene(authenticated_context, scene, dataset)

    created = await schema.execute(make, context_value=authenticated_context, variable_values=variables)
    assert not created.errors, created.errors
    assert created.data["createIntensityLayer"]["placementValidity"] == "MANUAL"

    placement = await schema.execute(LAYER_PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not placement.errors, placement.errors

    layer = placement.data["scene"]["layers"][0]
    assert layer["pathToWorld"] is not None, "a layer placed in a scene is registered into it"

    registration = layer["pathToWorld"][-1]["transformation"]
    assert registration["kind"] == "BY_DIMENSION"
    # (c,y,x) into (t,z,y,x): y and x are shared, and the edge claims nothing else.
    assert registration["inputAxes"] == ["y", "x"]
    assert registration["outputAxes"] == ["y", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_creating_a_layer_writes_no_membership_edges(authenticated_context: HttpContext):
    """A layer mutation reads the graph, never writes it: the one registration stays the only one."""
    dataset = await seed.create_array_dataset(authenticated_context, "DS")
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Sc")  # (z, y, x)
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    authored = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": str(world.pk),
                "transform": {
                    "kind": "AFFINE",
                    "affine": [[1.0, 0.0, 0.0, 5.0], [0.0, 1.0, 0.0, 5.0], [0.0, 0.0, 1.0, 0.0]],
                },
            }
        },
    )
    assert not authored.errors, authored.errors

    created = await schema.execute(
        """
        mutation Make($input: CreateIntensityLayerInput!) {
          createIntensityLayer(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert not created.errors, created.errors

    edges = await sync_to_async(lambda: list(models.Transformation.objects.filter(output=world, parent__isnull=True)))()
    assert len(edges) == 1, "creating a layer must not add a registration of its own"
    assert edges[0].kind == enums.TransformKindChoices.AFFINE.value


# --- 6. intensity is a channel, not a clock ---------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_time_axis_cannot_be_rendered_as_intensity(authenticated_context: HttpContext):
    """`intensityAxis: "t"` composites every timepoint as a separate channel.

    A 16-frame timelapse becomes sixteen stacked slabs, and the time axis is consumed --
    so no time slider can appear either. The axis was only ever resolved by name, so this
    was accepted; nothing about the resulting render points back at the write.
    """
    axes = [
        seed.axis("t", enums.AxisType.TIME),
        seed.axis("c", enums.AxisType.CHANNEL),
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ]
    dataset = await seed.create_array_dataset(authenticated_context, "Timelapse", axes=axes, shapes=[[16, 2, 32, 32]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Sc")
    await seed.register_into_scene(authenticated_context, scene, dataset)

    async def make(intensity_axis: str):
        return await schema.execute(
            """
            mutation Make($input: CreateIntensityLayerInput!) {
              createIntensityLayer(input: $input) { id }
            }
            """,
            context_value=authenticated_context,
            variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": intensity_axis}},
        )

    rejected = await make("t")
    assert rejected.errors, "a time axis is something you navigate, not something you blend"
    assert "not a CHANNEL axis" in str(rejected.errors[0])

    accepted = await make("c")
    assert not accepted.errors, accepted.errors


# --- 7. an edge is only walked backwards if it has an inverse ---------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_changing_edge_is_not_walked_backwards(authenticated_context: HttpContext):
    """The BFS hands back `inverted: true` steps for the client to undo. That is only
    honest for a map that has an inverse.

    An edge authored *from* world *into* a dataset of a different rank collapses
    dimensions on the way back: it says nothing about where the world's extra axes came
    from. Walking it backwards would ask the client to invert a non-square matrix. So the
    path is null -- an honest "not placed" -- rather than a step that cannot be composed.
    """
    # Two levels, so the pyramid still stores a SCALE edge (level 0 no longer has one:
    # its space IS the intrinsic system, and there is nothing to map).
    dataset = await seed.create_array_dataset(authenticated_context, "DS", shapes=[[3, 64, 64], [3, 32, 32]])  # (c, y, x)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    scene_result = await _create_scene(authenticated_context)  # (t, z, y, x)
    scene_id = scene_result.data["createScene"]["id"]
    world_id = scene_result.data["createScene"]["worldCoordinateSystem"]["id"]

    def place():
        scene = models.Scene.objects.get(pk=scene_id)
        models.Layer.objects.create(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)
        return scene

    scene = await sync_to_async(place)()  # placed by ORM, so no auto-registration
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    # Authored backwards: world (4 axes) -> intrinsic (3 axes). Reaching world from the
    # dataset would mean traversing this against its direction.
    backwards = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": world_id,
                "output": str(intrinsic.pk),
                "transform": {
                    "kind": "AFFINE",
                    # 3 rows (the output's rank), 4+1 columns (the input's rank plus translation)
                    "affine": [[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0, 0.0]],
                },
            }
        },
    )
    assert not backwards.errors, backwards.errors

    result = await schema.execute(LAYER_PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    path = result.data["scene"]["layers"][0]["pathToWorld"]
    assert path is None, "a rank-changing edge has no inverse, so it is not a way back to world"

    def rank_verdicts() -> tuple[bool, bool]:
        # The pyramid's level-to-intrinsic SCALE is equal-rank, so it still inverts fine --
        # which is why this rule changes no path that resolved before BY_DIMENSION existed
        # (see test_inverted_step_is_flagged). The backwards registration does not.
        equal_rank = models.Transformation.objects.filter(kind=enums.TransformKindChoices.SCALE.value).first()
        rank_changing = models.Transformation.objects.get(pk=backwards.data["createTransformation"]["id"])
        return graph_logic.is_reverse_traversable(equal_rank), graph_logic.is_reverse_traversable(rank_changing)

    equal_rank_inverts, rank_changing_inverts = await sync_to_async(rank_verdicts)()
    assert equal_rank_inverts, "an edge whose two sides have the same rank has an inverse"
    assert not rank_changing_inverts, "a (t,z,y,x) -> (c,y,x) edge cannot say where t and z came from"


# --- 8. the bounding box the registration must not touch --------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_registration_does_not_hijack_the_walk_to_intrinsic(authenticated_context: HttpContext):
    """An ROI's box is expressed against intrinsic pixels, and a registration must not move it.

    The walk up to intrinsic took "the first edge out of this system", unordered -- so once
    a registration edge hung off the same system, it could be picked instead, wander into
    world, find no way on, and raise. `compute_intrinsic_bbox` catches that as "no chain"
    and leaves the box in the frame it was drawn in, silently labelled as intrinsic.
    """
    # (z,y,x), matching the world's names: the registration below is a per-axis TRANSLATION and
    # exists only to hang an edge off the lens' system. What this test is about is which edge the
    # walk follows, not how that edge is spelled.
    dataset = await seed.create_array_dataset(authenticated_context, "DS", axes=seed.ZYX_AXES)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    scene = await seed.create_scene(authenticated_context, "Sc")

    lens_system = await sync_to_async(lambda: lens.coordinate_system)()
    vectors = [[0.0, 0.0, 0.0], [0.0, 4.0, 4.0]]

    before = await sync_to_async(graph_logic.compute_intrinsic_bbox)(lens_system, vectors)
    assert before is not None

    # Register the *lens'* system into world -- an edge out of the very system the walk
    # starts from, which is what makes it a candidate to be followed by mistake.
    world = await sync_to_async(lambda: scene.world)()
    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(lens_system.pk),
                "output": str(world.pk),
                "transform": {
                    "kind": "TRANSLATION",
                    "translation": [1000.0, 1000.0, 1000.0],
                },
            }
        },
    )
    assert not registered.errors, registered.errors

    after = await sync_to_async(graph_logic.compute_intrinsic_bbox)(lens_system, vectors)
    assert after == before, "the ROI's intrinsic box is a fact about the dataset; a scene registration cannot move it"

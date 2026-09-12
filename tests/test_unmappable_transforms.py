"""UNMAPPABLE: the one edge that says nothing corresponds, and is believed.

Every other transform kind asserts that a point maps. There was no way to assert that none
does -- so data whose geometry a task destroyed (a phasor array whose arrival-time axis
collapsed, a table of per-object measurements) could only be recorded by lying with an
IDENTITY, or not recorded at all, which loses the lineage along with the geometry.

The kind is easy. What earns it is that the graph *acts* on it: the placement search
refuses the edge in both directions, the auto-registration refuses to invent a placement
around it, and the lineage stops there -- while discovery still returns it and
`derivedFrom` still reports it, because "why can this not be placed" is a question the
client is entitled to an answer to.

Every test below that pins a gate is written so that removing the gate makes it fail.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_derived_datasets import _derive

CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) { id worldCoordinateSystem { id } }
}
"""

REGISTER = """
mutation Register($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id kind }
}
"""

MAKE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers { id placement pathToWorld { transformation { id kind } } }
    worldCoordinateSystem { registrations { id kind name } }
  }
}
"""

DERIVED = """
query Derived($id: ID!) {
  arrayDataset(id: $id) {
    id
    derivedFrom { id kind ... on UnmappableTransformation { reason } output { id  } }
  }
}
"""

_AFFINE_3D = [
    [1.0, 0.0, 0.0, 5.0],
    [0.0, 1.0, 0.0, 5.0],
    [0.0, 0.0, 1.0, 0.0],
]


async def _orm_layer(ctx: HttpContext, scene_id: str, lens: models.Lens) -> None:
    """An image layer written straight to the ORM, bypassing the mutation's placement gate.

    The mutations now refuse a source with no path to world, so the query-time behavior
    of an unplaced layer -- which these tests exist to pin -- is only reachable this way
    (or by an edge disappearing after the layer was made, which this simulates).
    """

    def make() -> None:
        scene = models.Scene.objects.get(pk=scene_id)
        models.Layer.objects.create(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)

    await sync_to_async(make)()


async def _scene_with_registered_source(ctx: HttpContext, source: models.ArrayDataset) -> str:
    """A scene whose world the source dataset is registered into, by an affine someone measured."""
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": "Sc", "axes": [{"name": "z", "type": "SPACE", "unit": "micrometer"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]}})
    assert not result.errors, result.errors
    scene_id = result.data["createScene"]["id"]
    world_id = result.data["createScene"]["worldCoordinateSystem"]["id"]

    intrinsic = await sync_to_async(lambda: source.intrinsic_coordinate_system)()
    registered = await schema.execute(
        REGISTER,
        context_value=ctx,
        variable_values={"input": {"input": str(intrinsic.pk), "output": world_id, "transform": {"kind": "AFFINE", "affine": _AFFINE_3D}}},
    )
    assert not registered.errors, registered.errors
    return scene_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_edge_is_not_a_way_to_world(authenticated_context: HttpContext):
    """The search will not cross it, so the data it relates has no path -- which is the truth.

    ABLATION: un-gate the forward step in `SceneGraph.adjacency` and the BFS walks straight
    through the derivation into the source's systems and out to world, handing the client a
    composable path across a stated non-correspondence. It would look exactly like every
    other path.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])
    scene_id = await _scene_with_registered_source(authenticated_context, source)

    derived = await _derive(authenticated_context, "Phasor", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE", "reason": "phasor reduction over the arrival-time axis"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    # The mutation refuses this outright now; the query-time gate is what this test pins,
    # so the layer goes in through the ORM.
    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert made.errors and "UNMAPPABLE" in str(made.errors[0])
    await _orm_layer(authenticated_context, scene_id, lens)

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    layer = result.data["scene"]["layers"][0]
    assert layer["pathToWorld"] is None, "the source is placed, but nothing relates this data to the source -- so it is not placed"
    # And the client is told *which* kind of null this is.
    assert layer["placement"] == "UNMAPPABLE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_edge_in_a_scene_is_still_not_a_way_to_world(authenticated_context: HttpContext):
    """Even registered into the scene's composition, the search will not walk it.

    This is the case that pins the FORWARD gate specifically. The world's own edges are
    in every layer's adjacency, so an UNMAPPABLE edge authored straight into the world -- by
    a client that thought registering it was how you place the data -- sits one hop from
    world in the search's own edge set. Nothing but the gate stops the BFS taking it and
    reporting a placement, and the layer would render at the identity, definitely and
    wrongly.

    ABLATION: un-gate the forward step in `SceneGraph.adjacency` and `pathToWorld` resolves.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Phasor")
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    # A world sharing no axis NAME with the dataset, so that the auto-registration has
    # nothing to assume and the unmappable edge is the only candidate route to world. (That
    # auto-registration is itself gated, and its own test is below; this one is about the
    # search, and wants the search's edge set to contain exactly the edge under suspicion.)
    scene_result = await schema.execute(
        CREATE_SCENE,
        context_value=authenticated_context,
        variable_values={"input": {"name": "Sc", "axes": [{"name": "u", "type": "SPACE", "unit": "micrometer"}, {"name": "v", "type": "SPACE", "unit": "micrometer"}]}},
    )
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]
    world_id = scene_result.data["createScene"]["worldCoordinateSystem"]["id"]

    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={"input": {"input": str(intrinsic.pk), "output": world_id, "transform": {"kind": "UNMAPPABLE", "reason": "nothing about this data is anywhere"}}},
    )
    assert not registered.errors, registered.errors

    # The creation-time gate mirrors the search's: it sees only the UNMAPPABLE candidate
    # and refuses. The layer goes in through the ORM so the query-time gate is what's pinned.
    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert made.errors and "UNMAPPABLE" in str(made.errors[0])
    await _orm_layer(authenticated_context, scene_id, lens)

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    layer = result.data["scene"]["layers"][0]
    assert layer["pathToWorld"] is None, "an edge that maps nothing is not a route, wherever it is filed"
    assert layer["placement"] == "UNMAPPABLE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_derivation_is_refused_with_the_impossibility_message(authenticated_context: HttpContext):
    """The layer mutation refuses this data, and says WHY: nothing can ever place it.

    This dataset kept axes called y and x -- and the world has y and x -- exactly the
    name-coincidence an identity fabrication would have latched onto, manufacturing the
    very point correspondence its author declared does not exist. Nothing fabricates
    edges any more, and the refusal must not read as "go author the registration"
    either: there is no registration to author, and the error says so.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])

    derived = await _derive(authenticated_context, "Features", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    # Nothing is registered into this scene at all.
    scene_result = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "Sc"}})
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert made.errors, "data whose geometry did not survive cannot be composed into a shared scene"
    assert "UNMAPPABLE" in str(made.errors[0])
    assert "createTransformation" not in str(made.errors[0]), "there is no missing registration to send anyone after"

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    assert result.data["scene"]["worldCoordinateSystem"]["registrations"] == [], "and the refused mutation fabricated nothing on the way out"
    assert result.data["scene"]["layers"] == [], "no layer either: the refusal is atomic"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lineage_stops_but_the_provenance_does_not(authenticated_context: HttpContext):
    """Two halves that must not be confused: placement ends here, history does not.

    `lineage_ancestors` answers "who places this", and nothing places data across an
    UNMAPPABLE edge -- so this dataset is its own root. `derivedFrom` answers "where did
    this come from", and the answer is the edge itself. Gate the wrong one of the two and
    `derivedFrom` goes null, which is precisely the silence UNMAPPABLE exists to break.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])

    derived = await _derive(authenticated_context, "Phasor", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE", "reason": "phasor reduction"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])

    ancestors = await sync_to_async(graph_logic.lineage_ancestors)(dataset)
    root = await sync_to_async(graph_logic.primary_lineage_root)(dataset)
    assert ancestors == [], "nothing places this data, so it inherits no placement -- it is a root"
    assert root.pk == dataset.pk

    result = await schema.execute(DERIVED, context_value=authenticated_context, variable_values={"id": str(dataset.pk)})
    assert not result.errors, result.errors

    edges = result.data["arrayDataset"]["derivedFrom"]
    assert len(edges) == 1, "the lineage is the whole reason to record an unmappable relation"
    assert edges[0]["kind"] == "UNMAPPABLE"
    assert edges[0]["reason"] == "phasor reduction"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_edge_is_still_discoverable(authenticated_context: HttpContext):
    """Discovery is kind-blind; placement is not. That asymmetry is the design.

    A client that gets `pathToWorld: null` needs to find out *why*, and the edge is the
    answer. Filtering it out of `coordinateGraph` -- which someone will eventually be
    tempted to do, on the grounds that it "goes nowhere" -- would leave the client with a
    null and no way to interpret it.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])

    derived = await _derive(authenticated_context, "Phasor", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await schema.execute(
        """
        query Graph($id: ID!) {
          coordinateGraph(coordinateSystem: $id) {
            systems { id residents { __typename } }
            transformations { id kind }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(intrinsic.pk)},
    )
    assert not result.errors, result.errors

    graph = result.data["coordinateGraph"]
    assert "UNMAPPABLE" in [edge["kind"] for edge in graph["transformations"]]
    # And the source's systems are reachable *as nodes* -- relatedness is real, it is only
    # the coordinates that do not travel.
    source_intrinsic = await sync_to_async(lambda: source.intrinsic_coordinate_system)()
    assert str(source_intrinsic.pk) in [system["id"] for system in graph["systems"]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_displacement_field_is_not_walked_backwards(authenticated_context: HttpContext):
    """Rank was never the whole rule; it only looked like it was.

    Every kind that was creatable happened to be invertible, so "same number of axes on
    both sides" was a sufficient test -- and stopped being one the moment a FIELD became
    writable. A warp field maps N axes to N axes and has no closed-form inverse at all, so
    a rank-only gate hands the client an `inverted: true` step it cannot honour.

    ABLATION: revert `is_reverse_traversable` to the rank comparison and this passes a path
    back, inverting a displacement field.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Warped")

    def build() -> tuple[models.Transformation, models.Transformation]:
        intrinsic = dataset.intrinsic_coordinate_system
        world = models.CoordinateSystem.objects.create(name="Atlas", organization=authenticated_context.request.organization)
        for index, axis in enumerate(["c", "y", "x"]):
            models.Axis.objects.create(coordinate_system=world, order=index, name=axis, type=enums.AxisTypeChoices.SPACE.value if axis != "c" else enums.AxisTypeChoices.CHANNEL.value)

        # The warp field is a node now, not a store on the edge: its own space, carrying the
        # DISPLACEMENT value axis that says its numbers are offsets rather than positions.
        field = models.CoordinateSystem.objects.create(name="Warp field", organization=authenticated_context.request.organization)
        for index, (axis, kind) in enumerate((("y", enums.AxisTypeChoices.SPACE), ("x", enums.AxisTypeChoices.SPACE), ("d", enums.AxisTypeChoices.DISPLACEMENT))):
            models.Axis.objects.create(coordinate_system=field, order=index, name=axis, type=kind.value)

        # Authored world -> intrinsic, so reaching world from the data REQUIRES inverting it.
        warp = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=world,
            output=intrinsic,
            field=field,
            organization=authenticated_context.request.organization,
        )
        scale = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            input=intrinsic,
            output=world,
            params={"scale": [1.0, 2.0, 2.0]},
            organization=authenticated_context.request.organization,
        )
        return warp, scale

    warp, scale = await sync_to_async(build)()

    assert await sync_to_async(graph_logic.is_reverse_traversable)(warp) is False, "a displacement field has no closed-form inverse, whatever its rank"
    assert await sync_to_async(graph_logic.is_traversable)(warp) is True, "forwards it is a perfectly good map"

    # The fence: an equal-rank SCALE still inverts, so the gate has not simply been welded shut.
    assert await sync_to_async(graph_logic.is_reverse_traversable)(scale) is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sequence_is_invertible_only_if_its_children_are(authenticated_context: HttpContext):
    """A wrapper's kind says nothing about whether it can be undone. Its children do.

    A set-membership test on the wrapper's own kind -- the obvious implementation -- would
    wave this through: SEQUENCE is a perfectly invertible kind, right up until one of its
    steps is a warp field.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Warped")

    def build() -> tuple[models.Transformation, models.Transformation]:
        intrinsic = dataset.intrinsic_coordinate_system
        target = models.CoordinateSystem.objects.create(name="Atlas", organization=authenticated_context.request.organization)
        for index, axis in enumerate(["c", "y", "x"]):
            models.Axis.objects.create(coordinate_system=target, order=index, name=axis, type=enums.AxisTypeChoices.SPACE.value if axis != "c" else enums.AxisTypeChoices.CHANNEL.value)

        field = models.CoordinateSystem.objects.create(name="Warp field", organization=authenticated_context.request.organization)
        models.Axis.objects.create(coordinate_system=field, order=0, name="d", type=enums.AxisTypeChoices.DISPLACEMENT.value)

        honest = models.Transformation.objects.create(kind=enums.TransformKindChoices.SEQUENCE.value, input=intrinsic, output=target, organization=authenticated_context.request.organization)
        models.Transformation.objects.create(kind=enums.TransformKindChoices.SCALE.value, parent=honest, order=0, params={"scale": [1.0, 2.0, 2.0]}, organization=authenticated_context.request.organization)
        models.Transformation.objects.create(kind=enums.TransformKindChoices.TRANSLATION.value, parent=honest, order=1, params={"translation": [0.0, 1.0, 1.0]}, organization=authenticated_context.request.organization)

        warped = models.Transformation.objects.create(kind=enums.TransformKindChoices.SEQUENCE.value, input=intrinsic, output=target, organization=authenticated_context.request.organization)
        models.Transformation.objects.create(kind=enums.TransformKindChoices.SCALE.value, parent=warped, order=0, params={"scale": [1.0, 2.0, 2.0]}, organization=authenticated_context.request.organization)
        models.Transformation.objects.create(kind=enums.TransformKindChoices.FIELD.value, parent=warped, order=1, field=field, organization=authenticated_context.request.organization)

        return honest, warped

    honest, warped = await sync_to_async(build)()

    assert await sync_to_async(graph_logic.is_reverse_traversable)(honest) is True
    assert await sync_to_async(graph_logic.is_reverse_traversable)(warped) is False, "a sequence is only as invertible as its least invertible step"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_edge_does_not_poison_an_roi_box(authenticated_context: HttpContext):
    """An ROI's box is composed along the walk to intrinsic, and that walk must not take it.

    `_edge_towards_intrinsic` picks the first edge out of a system that stays inside the
    dataset. An UNMAPPABLE derivation is such an edge, and taking it would push the box
    through a map that does not exist -- or, since it has no matrix, raise, be swallowed by
    `compute_intrinsic_bbox`, and hand back a box in the wrong frame with an intrinsic
    label on it. Silently.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])

    derived = await _derive(authenticated_context, "Phasor", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])

    vectors = [[0.0, 0.0, 0.0], [2.0, 8.0, 8.0]]

    def boxes() -> tuple[dict, dict]:
        derived_box = graph_logic.compute_intrinsic_bbox(dataset.intrinsic_coordinate_system, vectors)
        # The same ROI on a dataset with no derivation at all: whatever the box convention
        # is (it pads to voxel bounds), the unmappable edge must not have changed it.
        plain_box = graph_logic.compute_intrinsic_bbox(source.intrinsic_coordinate_system, vectors)
        return derived_box, plain_box

    derived_box, plain_box = await sync_to_async(boxes)()
    assert derived_box == plain_box, "the walk to intrinsic must not step across an unmappable edge: the box would come back in another dataset's frame, labelled as this one's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_placement_distinguishes_a_gap_from_an_impossibility(authenticated_context: HttpContext):
    """PLACED, UNREGISTERED, UNMAPPABLE -- because a null `pathToWorld` meant two things.

    One of them is a gap in the data: nobody has registered this yet, and authoring the edge
    closes it. The other is a fact about the data: it can never be placed. A client that
    cannot tell them apart either badges real gaps as impossible or sends people looking for
    a registration that cannot exist.
    """
    source = await seed.create_array_dataset(authenticated_context, "Raw")
    source_lens = await seed.create_lens(authenticated_context, source, slices=[])
    scene_id = await _scene_with_registered_source(authenticated_context, source)

    # PLACED: registered, and the walk finds it.
    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(source_lens.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    # UNMAPPABLE: related to the placed data, and by an edge that maps nothing. The
    # mutation refuses it now, so it enters through the ORM -- the query must still badge it.
    derived = await _derive(authenticated_context, "Phasor", lens=source_lens, axes=seed.SIMPLE_AXES, shape=[3, 64, 64], transform={"kind": "UNMAPPABLE"})
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    await _orm_layer(authenticated_context, scene_id, lens)

    # UNREGISTERED: a perfectly placeable dataset that nobody has placed. It shares no axis
    # name with the world, so no registration exists and none can be assumed -- the gap
    # this arm is about. ORM again: the mutation would refuse the gap too.
    stranger = await seed.create_array_dataset(authenticated_context, "Stranger", axes=[seed.axis("object", enums.AxisType.INDEX)], shapes=[[12]])
    stranger_lens = await seed.create_lens(authenticated_context, stranger, slices=[])
    await _orm_layer(authenticated_context, scene_id, stranger_lens)

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not result.errors, result.errors

    states = [layer["placement"] for layer in result.data["scene"]["layers"]]
    assert states == ["PLACED", "UNMAPPABLE", "UNREGISTERED"], states


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_fusion_with_one_unmappable_parent_is_a_gap_not_an_impossibility(authenticated_context: HttpContext):
    """UNMAPPABLE is claimed only when the data reaches nowhere at all.

    A fusion whose two parents are related to it differently -- one across an edge that maps
    nothing, one across an ordinary scale -- can be placed: register the parent it does relate
    to, or the fusion itself, and the walk finds it. Reading the verdict off "any lineage edge
    is UNMAPPABLE" badged that as impossible and sent whoever read the badge away from a gap
    they could have closed in one mutation.

    The second parent is deliberately *not* registered into the world here, so the fusion is
    genuinely unplaced. What is being pinned is which of the two reasons it is given.
    """
    ctx = authenticated_context
    destroyed = await seed.create_array_dataset(ctx, "Phasor source")
    destroyed_lens = await seed.create_lens(ctx, destroyed, slices=[])
    intact = await seed.create_array_dataset(ctx, "Intact source")
    intact_lens = await seed.create_lens(ctx, intact, slices=[])

    fused = await _derive(
        ctx,
        "Fusion",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[
            # The primary parent must be one that places the data; the write path refuses an
            # UNMAPPABLE first entry, which is the same rule from the other side.
            {"kind": "LENS", "lens": str(intact_lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "LENS", "lens": str(destroyed_lens.pk), "transform": {"kind": "UNMAPPABLE"}},
        ],
    )
    assert not fused.errors, fused.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=fused.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(ctx, dataset, slices=[])

    # A scene over a world nothing in this lineage is registered into.
    scene = await schema.execute(
        CREATE_SCENE,
        context_value=ctx,
        variable_values={"input": {"name": "Empty world", "axes": [{"name": "z", "type": "SPACE", "unit": "micrometer"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]}},
    )
    assert not scene.errors, scene.errors
    scene_id = scene.data["createScene"]["id"]
    await _orm_layer(ctx, scene_id, lens)

    result = await schema.execute(PLACEMENT, context_value=ctx, variable_values={"id": scene_id})
    assert not result.errors, result.errors
    assert [layer["placement"] for layer in result.data["scene"]["layers"]] == ["UNREGISTERED"], "the intact parent is a route, so the registration is merely missing"

    # And creation-time refusal says the same thing, in the same terms -- the two read the
    # same predicate precisely so a client is never told to look for an edge and then told
    # there is nothing to look for.
    refused = await schema.execute(
        MAKE_LAYER,
        context_value=ctx,
        variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert refused.errors, "nothing places it yet"
    message = str(refused.errors[0])
    assert "Author the registration" in message, message
    assert "UNMAPPABLE" not in message, message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_write_path_refuses_a_map_on_an_unmappable_edge(authenticated_context: HttpContext):
    """It carries no parameters, and no rank constrains it. Both halves matter."""
    first = await seed.create_array_dataset(authenticated_context, "Image")
    axes = [seed.axis("object", enums.AxisType.INDEX)]
    second = await seed.create_array_dataset(authenticated_context, "Table", axes=axes, shapes=[[12]])

    image = await sync_to_async(lambda: first.intrinsic_coordinate_system)()
    table = await sync_to_async(lambda: second.intrinsic_coordinate_system)()

    # Rank-free: a (c,y,x) grid and a one-axis table of objects, related, with no map. Every
    # other kind would have been rejected here, and rightly.
    made = await schema.execute(REGISTER, context_value=authenticated_context, variable_values={"input": {"input": str(table.pk), "output": str(image.pk), "transform": {"kind": "UNMAPPABLE", "reason": "one row per segmented object"}}})
    assert not made.errors, made.errors
    assert made.data["createTransformation"]["kind"] == "UNMAPPABLE"

    # But it may not carry a map, in the same breath as denying there is one. The parse
    # layer's strict members are the gate that fires through the API now: UNMAPPABLE
    # reads `reason` and nothing else.
    lying = await schema.execute(REGISTER, context_value=authenticated_context, variable_values={"input": {"input": str(table.pk), "output": str(image.pk), "transform": {"kind": "UNMAPPABLE", "scale": [1.0]}}})
    assert lying.errors, "an UNMAPPABLE edge with a scale asserts a correspondence and denies one at once"
    assert "does not read `scale`" in str(lying.errors[0])

    # And it cannot be refined into one later, through the back door.
    refined = await schema.execute(
        """
        mutation Refine($input: UpdateTransformationInput!) {
          updateTransformation(input: $input) { id }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": made.data["createTransformation"]["id"], "affine": [[1.0, 0.0], [0.0, 1.0]]}},
    )
    assert refined.errors, "refining an unmappable edge would write parameters that nothing will ever read"

"""A transformation scoped to one position along one axis: chromatic and per-timepoint drift.

This is the thing the coordinate graph could not say. ``inputAxes`` and ``outputAxes`` select
axes *by name*, and nothing on a `Transformation` -- nor anywhere in either composer -- read a
coordinate **value** to choose parameters. So "for c=2, translate by (0.3, 0.1)" had no
representation, and neither did per-timepoint drift correction. The documented workaround was one
`Lens` per channel or timepoint, each with its own coordinate system, its own edge and, because
``Layer.lens`` is a single FK, **its own Layer** -- a hundred-frame drift correction was a hundred
layers, which also defeats the flat-in-layer-count design of `SceneGraph`.

A scoped edge is a *partial* map: it holds where the input coordinate along its axis equals its
index, and says nothing elsewhere. Several of them over one axis are one piecewise map, written as
the several facts they are, so refining one channel's correction moves one channel.

The rule that makes this safe is that a query crosses a scoped edge **only when it fixes that
coordinate**. Without `at` the answer genuinely depends on where you are standing, and inventing
one would be the same class of bug as the pk-ordered tie-break the widest-path search replaced.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext, UniversalRequest
from pytest import approx
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


AT_AFFINE = """
query AtAffine($id: ID!, $at: [CoordinateInput!]) {
  scene(id: $id) {
    layers {
      id
      asAffine(at: $at) { matrix inputAxes outputAxes total }
      pathToWorld(at: $at) { transformation { id selector { axis index } } }
    }
  }
}
"""

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id selector { axis index } }
}
"""


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity: the scene-graph memo lives on the context."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


async def _register(ctx: HttpContext, input_id: int, output_id: int, transform: dict, selector: dict | None = None) -> dict:
    payload: dict = {"input": str(input_id), "output": str(output_id), "transform": transform}
    if selector is not None:
        payload["selector"] = selector
    result = await schema.execute(CREATE_TRANSFORM, context_value=_fresh_request(ctx), variable_values={"input": payload})
    assert not result.errors, result.errors
    return result.data["createTransformation"]


async def _errors(ctx: HttpContext, input_id: int, output_id: int, transform: dict, selector: dict) -> list:
    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"input": str(input_id), "output": str(output_id), "transform": transform, "selector": selector}},
    )
    return list(result.errors or [])


async def _layer_at(ctx: HttpContext, scene_id: str, at: list | None) -> dict:
    result = await schema.execute(AT_AFFINE, context_value=_fresh_request(ctx), variable_values={"id": scene_id, "at": at})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer


MAKE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

STATE = """
query State($id: ID!, $at: [CoordinateInput!]) {
  scene(id: $id) {
    layers { id placement(at: $at) placementValidity(at: $at) placementInvariance(at: $at) }
  }
}
"""

IN_VIEW = """
query InView($id: ID!, $at: [CoordinateInput!]) {
  coordinateSystem(id: $id) {
    inView(region: {min: [-1000, -1000], max: [1000, 1000]}, at: $at) {
      extentState
      source { __typename ... on ArrayDataset { id } }
    }
  }
}
"""


def _translation(matrix: list[list[float]]) -> list[float]:
    """The offset column of an M x (N+1) matrix -- what a per-channel correction moves by."""
    return [row[-1] for row in matrix]


async def _chromatic_scene(ctx: HttpContext) -> tuple[models.Scene, models.ArrayDataset]:
    """A (c,y,x) dataset on one layer, with no unscoped route into its scene's world."""
    scene = await seed.create_scene(ctx, "Chromatic")
    dataset = await seed.create_array_dataset(ctx, "Stack", shapes=[[3, 64, 64]])
    lens = await seed.create_lens(ctx, dataset, slices=[])
    await sync_to_async(models.Layer.objects.create)(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)
    return scene, dataset


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_layer_resolves_differently_per_channel(db, authenticated_context: HttpContext):
    """The whole point: two channels, one layer, two placements.

    Each channel's correction is its own edge into the same world -- the shape that used to
    require a lens, a system, an edge and a layer per channel.
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    for index, offset in ((0, [0.0, 0.0]), (2, [3.0, 5.0])):
        await _register(
            ctx, source.pk, world.pk,
            {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": offset},
            selector={"axis": "c", "index": index},
        )

    first = await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 0}])
    third = await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 2}])

    assert _translation(first["asAffine"]["matrix"]) == approx([0.0, 0.0])
    assert _translation(third["asAffine"]["matrix"]) == approx([3.0, 5.0]), "the third channel is corrected; the first is not"
    assert first["asAffine"]["outputAxes"] == third["asAffine"]["outputAxes"] == ["y", "x"]

    # And the path reports which scoped edge it actually crossed, rather than leaving the
    # client to infer it from the numbers.
    assert [step["transformation"]["selector"] for step in third["pathToWorld"]] == [{"axis": "c", "index": 2}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_without_a_fixed_coordinate_a_scoped_edge_is_not_crossed(db, authenticated_context: HttpContext):
    """No `at`, no answer -- rather than an arbitrary one.

    Where the data sits depends on the channel. A query that has not said which channel has no
    single placement to be given, so it gets a null (the same null an unregistered layer gets,
    which `placement` distinguishes) instead of whichever edge happened to sort first.
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    await _register(
        ctx, source.pk, world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [3.0, 5.0]},
        selector={"axis": "c", "index": 2},
    )

    unfixed = await _layer_at(ctx, str(scene.id), None)
    assert unfixed["asAffine"] is None
    assert unfixed["pathToWorld"] is None

    # Fixing a *different* index on the same axis is equally not a match.
    elsewhere = await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 1}])
    assert elsewhere["asAffine"] is None, "channel 1 has no correction authored, and channel 2's is not it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unscoped_edge_still_answers_without_at(db, authenticated_context: HttpContext):
    """Every edge written before selectors existed is unscoped, and none of them changed.

    This is the compatibility claim the whole design rests on: `selector_admits` returns True for
    a null selector, so a graph with no per-index edges behaves exactly as it did.
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    await _register(ctx, source.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [1.0, 1.0]})

    unfixed = await _layer_at(ctx, str(scene.id), None)
    assert _translation(unfixed["asAffine"]["matrix"]) == approx([1.0, 1.0])
    assert [step["transformation"]["selector"] for step in unfixed["pathToWorld"]] == [None]

    # And it still answers when a coordinate *is* fixed: `at` narrows what may be crossed, it
    # does not require that everything be scoped.
    fixed = await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 2}])
    assert _translation(fixed["asAffine"]["matrix"]) == approx([1.0, 1.0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_selector_must_name_an_axis_that_can_be_indexed(db, authenticated_context: HttpContext):
    """Three rejections, all at write time where the author is still in the room."""
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()
    transform = {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [1.0, 1.0]}

    absent = await _errors(ctx, source.pk, world.pk, transform, {"axis": "t", "index": 0})
    assert absent and "does not have" in str(absent[0]), "the dataset is (c,y,x); it has no time axis to be at a position along"

    spatial = await _errors(ctx, source.pk, world.pk, transform, {"axis": "y", "index": 3})
    assert spatial and "measured rather than indexed" in str(spatial[0]), "a correction varying through space is a FIELD, not a piecewise map"

    negative = await _errors(ctx, source.pk, world.pk, transform, {"axis": "c", "index": -1})
    assert negative and "non-negative" in str(negative[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refining_one_channel_moves_only_that_channel(db, authenticated_context: HttpContext):
    """Piecewise as several facts, not one row with a list in it.

    Each index is its own edge, so it carries its own `version` and its own provenance, and a
    refinement is the ordinary in-place `updateTransformation` rather than a rewrite of a blob
    that every other channel shares.
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    for index, offset in ((0, [1.0, 1.0]), (1, [2.0, 2.0])):
        await _register(
            ctx, source.pk, world.pk,
            {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": offset},
            selector={"axis": "c", "index": index},
        )

    edge_zero = await sync_to_async(lambda: models.Transformation.objects.get(selector={"axis": "c", "index": 0}))()
    await sync_to_async(lambda: models.Transformation.objects.filter(pk=edge_zero.pk).update(params={"translation": [9.0, 9.0]}))()

    assert _translation((await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 0}]))["asAffine"]["matrix"]) == approx([9.0, 9.0])
    assert _translation((await _layer_at(ctx, str(scene.id), [{"name": "c", "value": 1}]))["asAffine"]["matrix"]) == approx([2.0, 2.0]), "channel 1 did not move"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_selector_predicate_is_the_only_reader_of_the_convention(db, authenticated_context: HttpContext):
    """A unit-level pin on `selector_admits`, so the three cases stay stated in one place."""
    unscoped = models.Transformation(selector=None)
    scoped = models.Transformation(selector={"axis": "c", "index": 2})

    assert graph_logic.selector_admits(unscoped, None) is True
    assert graph_logic.selector_admits(unscoped, {"c": 7}) is True
    assert graph_logic.selector_admits(scoped, None) is False
    assert graph_logic.selector_admits(scoped, {"t": 2}) is False, "a coordinate on another axis is not a match"
    assert graph_logic.selector_admits(scoped, {"c": 1}) is False
    assert graph_logic.selector_admits(scoped, {"c": 2}) is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_per_index_registration_is_a_registration_everywhere_it_is_asked(db, authenticated_context: HttpContext):
    """The plumbing around the selector, which the walk had and nothing else did.

    `adjacency_of` was the only reader of `selector_admits`, and every other consumer walked
    with no `at` -- so a scoped edge was not merely uncrossable, it was absent. A dataset whose
    only registration was per channel therefore read as *unregistered* to `createLayer`, to
    `placement`, to `placementValidity`, to `placementInvariance` and to `inView`: registered
    once per channel, and reported as registered nowhere.

    Existence and position are different questions. Whether this data has a place does not
    depend on where the asker is standing; only which place does.
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    for index, offset in ((0, [0.0, 0.0]), (2, [3.0, 5.0])):
        await _register(
            ctx, source.pk, world.pk,
            {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": offset},
            selector={"axis": "c", "index": index},
        )

    # Without a coordinate: a placement, said so, rather than a gap to go and close.
    unfixed = await schema.execute(STATE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.id), "at": None})
    assert not unfixed.errors, unfixed.errors
    (layer,) = unfixed.data["scene"]["layers"]
    assert layer["placement"] == "CONDITIONAL", "registered per channel is registered"
    assert layer["placementValidity"] != "UNKNOWN", "there is a registration, and how known it is can be read off it"
    assert layer["placementInvariance"] != "NONE", "a translation per channel is still a translation"

    # With one: the ordinary answers, about that channel.
    fixed = await schema.execute(STATE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.id), "at": [{"name": "c", "value": 2}]})
    assert not fixed.errors, fixed.errors
    (at_two,) = fixed.data["scene"]["layers"]
    assert at_two["placement"] == "PLACED"
    assert at_two["placementInvariance"] == "ISOMETRY", "a translation preserves distances"

    # A channel nobody corrected is the one real gap here, and keeps its own answer.
    elsewhere = await schema.execute(STATE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.id), "at": [{"name": "c", "value": 1}]})
    assert not elsewhere.errors, elsewhere.errors
    assert elsewhere.data["scene"]["layers"][0]["placement"] == "CONDITIONAL", "c=1 has no correction, but the data is still placed per index elsewhere"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_can_be_created_over_a_scoped_only_registration(db, authenticated_context: HttpContext):
    """`createLayer` refused the layer the feature exists to allow.

    Its gate asks `is_placeable_in`, whose walk could not see a scoped edge at all. The shape
    that exposes it is the one the model actually recommends for chromatic drift: a corrected
    space hanging off intrinsic by a channel-wise edge, and *that* space registered into the
    world. The scoped hop is then in the middle of the chain, where nothing rescues it -- with
    the scoped edge last, a separate branch of `is_placeable_in` never consulted the selector
    and let it through, so the same registration was accepted or refused depending only on how
    many hops it took.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Corrected")
    dataset = await seed.create_array_dataset(ctx, "Stack", shapes=[[3, 64, 64]])
    lens = await seed.create_lens(ctx, dataset, slices=[])
    world = await sync_to_async(lambda: scene.world)()
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()

    # The corrected space. Bare, because a `registrations` entry cannot carry a selector --
    # the per-index edge is authored on its own below.
    aligned = await schema.execute(
        """mutation M($input: CreateCoordinateSystemInput!) { createCoordinateSystem(input: $input) { id } }""",
        context_value=_fresh_request(ctx),
        variable_values={
            "input": {
                "name": "Aligned",
                # A channel axis, because a selector names an axis of the edge's *input* system
                # and this is where the scoped hop lands. 'a.u.' is how an axis with no measured
                # dimension carries a unit in a unit-carrying space.
                "axes": [{"name": "c", "type": "CHANNEL", "unit": "a.u."}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}],
            }
        },
    )
    assert not aligned.errors, aligned.errors
    aligned_id = int(aligned.data["createCoordinateSystem"]["id"])

    # intrinsic -> aligned, per channel. Then aligned -> world, unscoped: nothing unscoped
    # reaches world from the dataset, and the only thing that does is scoped.
    await _register(
        ctx, intrinsic.pk, aligned_id,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [3.0, 5.0]},
        selector={"axis": "c", "index": 2},
    )
    await _register(ctx, aligned_id, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.5, 0.5]})

    made = await schema.execute(
        MAKE_LAYER,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert not made.errors, str(made.errors and made.errors[0])

    # And it reads as the placement it is, resolving when the channel is fixed.
    state = await schema.execute(STATE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.id), "at": None})
    assert not state.errors, state.errors
    assert state.data["scene"]["layers"][0]["placement"] == "CONDITIONAL"

    fixed = await schema.execute(STATE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.id), "at": [{"name": "c", "value": 2}]})
    assert not fixed.errors, fixed.errors
    assert fixed.data["scene"]["layers"][0]["placement"] == "PLACED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scoped_source_is_in_view_of_the_space_it_is_registered_into(db, authenticated_context: HttpContext):
    """`inView` never passed `at`, so a per-channel registration was invisible to it.

    The source is in the space -- returning nothing for it was the graph withholding what it
    knew. What it cannot state without a coordinate is the *box*, and CONDITIONAL says exactly
    that rather than leaving an empty extent to be read as "unbounded" or "not here".
    """
    ctx = authenticated_context
    scene, dataset = await _chromatic_scene(ctx)
    world = await sync_to_async(lambda: scene.world)()
    source = await sync_to_async(lambda: dataset.coordinate_system)()

    await _register(
        ctx, source.pk, world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [3.0, 5.0]},
        selector={"axis": "c", "index": 2},
    )

    unfixed = await schema.execute(IN_VIEW, context_value=_fresh_request(ctx), variable_values={"id": str(world.pk), "at": None})
    assert not unfixed.errors, unfixed.errors
    seen = unfixed.data["coordinateSystem"]["inView"]
    assert [hit["source"]["id"] for hit in seen] == [str(dataset.pk)], "in the space, whether or not the question fixed a channel"
    assert seen[0]["extentState"] == "CONDITIONAL"

    fixed = await schema.execute(IN_VIEW, context_value=_fresh_request(ctx), variable_values={"id": str(world.pk), "at": [{"name": "c", "value": 2}]})
    assert not fixed.errors, fixed.errors
    assert fixed.data["coordinateSystem"]["inView"][0]["extentState"] == "KNOWN", "fixing the channel is what makes a box computable"

"""`Layer.asAffine`: a whole placement path condensed into one labelled affine map.

The graph ships edges rather than answers, deliberately -- the same data under two
registrations has two answers, so no composed matrix is stored anywhere. But a *layer*
belongs to exactly one scene, so its path already has a single right answer, and every
client was reimplementing the composition. They were getting it wrong in the same two
places, and both are pinned below:

**Zero-filling the axes a registration says nothing about.** A (c,y,x) dataset placed on
(y,x) into a (t,z,y,x) world constrains two axes. Writing a zero row for the other two
pins the data at their origin, which is a claim nobody made and which culls it out of
every other slice. There is simply no row.

**Not inverting a step at all.** `pathToWorld` flags a step walked backwards and leaves the
undoing to the reader. `asAffine` does it -- and `SpaceGraph`, which still does not, reports
`ExtentState.INVERTED` rather than guessing.

The third pin is a bug this field surfaced: a SEQUENCE wrapper keeps its map on its
children, so composing it from the wrapper's own (empty) params yields the *identity*,
silently. Every stepped lens and every offset pyramid level is such an edge.
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


AS_AFFINE = """
query AsAffine($id: ID!) {
  scene(id: $id) {
    layers {
      id
      placement
      placementInvariance
      pathToWorld { inverted transformation { id kind } }
      asAffine { matrix inputAxes outputAxes total }
    }
  }
}
"""

STRICT = """
query Strict($id: ID!) {
  scene(id: $id) {
    layers { id asAffine(strict: true) { matrix outputAxes total } }
  }
}
"""

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id }
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


async def _layer(ctx: HttpContext, scene_id: str, query: str = AS_AFFINE) -> dict:
    result = await schema.execute(query, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer


async def _errors(ctx: HttpContext, scene_id: str, query: str = AS_AFFINE) -> list:
    result = await schema.execute(query, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    return list(result.errors or [])


async def _image_layer(scene: models.Scene, lens: models.Lens) -> None:
    """A layer over a lens, written through the ORM: the render settings are not the subject."""
    await sync_to_async(models.Layer.objects.create)(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)


async def _register(ctx: HttpContext, input_id: int, output_id: int, transform: dict) -> str:
    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"input": str(input_id), "output": str(output_id), "transform": transform}},
    )
    assert not result.errors, result.errors
    return str(result.data["createTransformation"]["id"])


def _apply(matrix: list[list[float]], point: list[float]) -> list[float]:
    """Push a point through an M x (N+1) matrix, the way a client would."""
    return [sum(factor * value for factor, value in zip(row[:-1], point)) + row[-1] for row in matrix]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_partial_registration_condenses_over_the_axes_it_names(authenticated_context: HttpContext):
    """The ordinary case: a BY_DIMENSION on (y,x) into a (z,y,x) world, and no row for z.

    This is the shape `create_identity_registration` writes for every ordinary registration
    and the shape `to_matrix` flatly refuses -- so a fixed-rank composition would fail here,
    which is exactly why the field composes functionals instead.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Tile")  # (c, y, x)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    await _register(
        authenticated_context,
        intrinsic.pk,
        world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.5, 0.5], "translation": [10.0, 20.0]},
    )
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    affine = layer["asAffine"]

    assert layer["placement"] == "PLACED"
    assert affine["inputAxes"] == ["c", "y", "x"], "the columns are the layer's own source axis order"
    assert affine["outputAxes"] == ["y", "x"], "the world's z is untouched by this registration, so it has no row"
    assert affine["total"] is False, "a partial registration is not a total map, and says so"

    # y' = 0.5y + 10, x' = 0.5x + 20, and c does not reach the world at all (zero column).
    assert affine["matrix"] == [[0.0, 0.5, 0.0, 10.0], [0.0, 0.0, 0.5, 20.0]]
    assert _apply(affine["matrix"], [1.0, 100.0, 200.0]) == approx([60.0, 120.0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_strict_refuses_the_partial_map_and_names_the_axes_it_cannot_reach(authenticated_context: HttpContext):
    """`strict: true` is for a client that needs a total map and would rather be told than guess.

    The default is the partial answer, because the partial answer is the truth. Strict is the
    opt-in for a renderer that cannot place data along an unconstrained axis and would
    otherwise silently draw it at zero.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Tile")
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    await _register(authenticated_context, intrinsic.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]})
    await _image_layer(scene, lens)

    # Without strict, the same layer answers happily.
    assert (await _layer(authenticated_context, str(scene.pk)))["asAffine"]["total"] is False

    errors = await _errors(authenticated_context, str(scene.pk), STRICT)
    assert errors, "strict must refuse a map that does not cover every world axis"
    assert "says nothing about ['z']" in str(errors[0]), str(errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_total_registration_reports_total_and_composes_every_axis(authenticated_context: HttpContext):
    """The other side of `total`: a registration naming every world axis has a row for each.

    Without this, a change that made `total` always false would still pass the test above.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Volume", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    # (y, x) -> (z, y, x): three rows, two columns, plus the translation. A tilted section,
    # so z is a real function of y rather than a zero row -- and a 3 x 2 linear part, which
    # is what makes this a *rank-changing* AFFINE with no inverse to ask about.
    await _register(
        authenticated_context,
        intrinsic.pk,
        world.pk,
        {"kind": "AFFINE", "affine": [[0.25, 0.0, 3.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
    )
    await _image_layer(scene, lens)

    affine = (await _layer(authenticated_context, str(scene.pk)))["asAffine"]
    assert affine["outputAxes"] == ["z", "y", "x"]
    assert affine["total"] is True
    assert _apply(affine["matrix"], [8.0, 5.0]) == approx([5.0, 8.0, 5.0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stepped_lens_carries_its_crop_and_its_subsample(authenticated_context: HttpContext):
    """The SEQUENCE bug, pinned: a wrapper's map lives on its children, not in its params.

    `create_lens_edge` writes a SEQUENCE for a *stepped* lens -- scale on child 0, offset on
    child 1, the wrapper's own `params` empty -- so composing it from those params yields the
    identity and the crop and the subsample vanish without a word. That edge is the first hop
    to world of an ordinary multiscale image layer, so this is not an exotic path.

    The lens takes y from index 8 with a step of 2, so a lens coordinate `k` is dataset
    coordinate `2k + 8`, and the assertions below fail on an identity by exactly that.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Stepped")  # (c, y, x)
    lens = await seed.create_lens(
        authenticated_context,
        dataset,
        slices=[{"axis": "y", "start": 8, "stop": 64, "step": 2}],
    )
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    intrinsic, world, lens_system = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world, lens.coordinate_system))()
    assert lens_system.pk != intrinsic.pk, "a stepped lens owns a space of its own, or there is no SEQUENCE to test"

    await _register(authenticated_context, intrinsic.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]})
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    assert any(step["transformation"]["kind"] == "SEQUENCE" for step in layer["pathToWorld"]), "the fixture must actually put a SEQUENCE on the path"

    affine = layer["asAffine"]
    assert affine["outputAxes"] == ["y", "x"]
    # Lens (c, y, x) = (0, 0, 5) is dataset y = 8, and the registration is an identity on
    # both axes. An identity-composed SEQUENCE would answer y = 0 here.
    assert _apply(affine["matrix"], [0.0, 0.0, 5.0]) == approx([8.0, 5.0])
    assert _apply(affine["matrix"], [0.0, 10.0, 5.0]) == approx([28.0, 5.0]), "the step is a factor of 2, not 1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_path_walked_backwards_is_inverted_rather_than_refused(authenticated_context: HttpContext):
    """A step flagged `inverted` is undone, which is the whole thing clients could not do.

    Direction is always forward on the *row*, never on the walk: the registration here was
    authored world -> physical space rather than the other way, which is an ordinary thing
    for a client holding a stage-to-world map to write. The layer's data reaches the world
    only by walking that edge backwards, so the composition has to invert it.

    The matrix is checked by pushing a point through it against the composition worked out
    by hand -- a matrix that is right up to a reciprocal passes every structural assertion.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Stage", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    world = await sync_to_async(lambda: scene.world)()

    # intrinsic -> physical, stored forward: 0.1 micrometre per pixel.
    physical = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        scale=[0.1, 0.1],
    )
    # world -> physical, also stored forward: the world's units are half the physical space's.
    await _register(
        authenticated_context,
        world.pk,
        physical.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [2.0, 2.0]},
    )
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    assert layer["pathToWorld"] is not None, "the data reaches the world, backwards down the world's own edge"
    assert any(step["inverted"] for step in layer["pathToWorld"]), "the fixture must actually put an inverted step on the path"

    affine = layer["asAffine"]
    assert affine["outputAxes"] == ["y", "x"], "the world's z is never mentioned, in either direction"
    # Pixels to physical is times 0.1; physical to world is the times-2 edge undone, so halve.
    assert _apply(affine["matrix"], [100.0, 200.0]) == approx([5.0, 10.0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_step_errors_and_names_the_edge(authenticated_context: HttpContext):
    """A path that exists and does not condense is an error, never a null.

    Null already means two things -- unregistered, or unmappable -- and `placement` is what
    tells them apart. Adding a third meaning ("there is a path but I would not compose it")
    would make the null useless. A FIELD gives its map as the values of an array, so there is
    no closed form at all, and the error says which edge.
    """
    scene = await seed.create_scene(authenticated_context, "Composition")
    world = await sync_to_async(lambda: scene.world)()

    mask = await seed.create_array_dataset(authenticated_context, "Mask", axes=seed.YX_AXES, shapes=[[64, 64]])
    objects = await seed.create_array_dataset(
        authenticated_context,
        "Objects",
        axes=[seed.axis("i", enums.AxisType.INDEX)],
        shapes=[[128]],
    )
    lens = await seed.create_lens(authenticated_context, mask, slices=[])
    mask_system, object_system = await sync_to_async(lambda: (mask.intrinsic_coordinate_system, objects.intrinsic_coordinate_system))()

    # The mask's pixels ARE the map into the object space, and the object space is the world.
    field_id = await _register(
        authenticated_context,
        mask_system.pk,
        object_system.pk,
        {"kind": "FIELD", "field": str(mask_system.pk), "inputAxes": ["y", "x"], "outputAxes": ["i"]},
    )
    await _register(authenticated_context, object_system.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["i"], "outputAxes": ["z"]})
    await _image_layer(scene, lens)

    result = await schema.execute(AS_AFFINE, context_value=_fresh_request(authenticated_context), variable_values={"id": str(scene.pk)})
    assert result.errors, "a FIELD on the path has no closed form, and silence about that would be worse than an error"
    message = str(result.errors[0])
    assert f"transformation {field_id} (FIELD)" in message, message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_layer_is_null_exactly_as_its_path_is(authenticated_context: HttpContext):
    """Null when and only when `pathToWorld` is null, and `placement` still says which gap it is."""
    dataset = await seed.create_array_dataset(authenticated_context, "Unplaced")
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    assert layer["pathToWorld"] is None
    assert layer["asAffine"] is None, "the two nulls agree, because they are the same absence"
    assert layer["placement"] == "UNREGISTERED", "and `placement` is still what says which of the two it is"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_layer_is_null_too_and_placement_tells_it_apart(authenticated_context: HttpContext):
    """The second reason for a null, which is a fact to badge rather than a gap to close."""
    source = await seed.create_array_dataset(authenticated_context, "Source", axes=seed.YX_AXES, shapes=[[64, 64]])
    derived = await seed.create_array_dataset(authenticated_context, "Measured", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, derived, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")
    source_system, derived_system, world = await sync_to_async(
        lambda: (source.intrinsic_coordinate_system, derived.intrinsic_coordinate_system, scene.world)
    )()

    await _register(authenticated_context, source_system.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]})
    await _register(authenticated_context, derived_system.pk, source_system.pk, {"kind": "UNMAPPABLE", "reason": "one row per segmented object"})
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    assert layer["asAffine"] is None
    assert layer["placement"] == "UNMAPPABLE", "there is no registration to go and author here"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_as_affine_condenses_the_very_path_that_path_to_world_reports(authenticated_context: HttpContext):
    """The two fields must never be able to disagree: same universe, same walk, same tie-break.

    A composed answer that came from a *different* path than the one the client can inspect
    would be worse than no composed answer at all -- it would be unfalsifiable from outside.
    So `condensed_placement` is built on `placement_path`, and this holds it there by
    composing the reported steps by hand and comparing.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Chain", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    physical = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        scale=[0.1, 0.1],
    )
    await _register(
        authenticated_context,
        physical.pk,
        world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "translation": [5.0, -5.0]},
    )
    await _image_layer(scene, lens)

    layer = await _layer(authenticated_context, str(scene.pk))
    assert len(layer["pathToWorld"]) == 2, "the fixture must exercise a multi-hop path"

    affine = layer["asAffine"]
    # Composed by hand from the reported steps: scale by 0.1 into the physical space, then
    # offset into the world.
    assert _apply(affine["matrix"], [100.0, 200.0]) == approx([15.0, 15.0])

    # And the server's own composition of the same steps, through the logic layer, agrees.
    def composed() -> list[list[float]]:
        scene_graph = graph_logic.condense_path(
            [(models.Transformation.objects.get(pk=step["transformation"]["id"]), step["inverted"]) for step in layer["pathToWorld"]],
            source_axes=[axis.name for axis in intrinsic.axes.all()],
            destination_axes=[axis.name for axis in world.axes.all()],
        )
        return scene_graph.matrix

    assert await sync_to_async(composed)() == affine["matrix"]

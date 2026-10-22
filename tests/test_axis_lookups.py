"""A BY_DIMENSION edge's lookup: one axis read from a 1-D array, the rest still affine.

An event-triggered movie's frames were taken at uneven instants, so no scale and offset along
`t` states when frame 3 was. The instants are an array; a lookup says "the world `t` of frame
`i` is `times[i]`". Before this, the only way to say it was a FIELD edge, and a FIELD anywhere
in a path made `asAffine` error for the whole layer -- its pixel size included. The load-bearing
pins below are that the spatial rows survive a lookup beside them, that a lookup composes through
a lens slice and a later clock offset, and that what the server cannot check (the array's
values) is declared rather than invented.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from pytest import approx

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request, _image_layer

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) {
    id
    kind
    ... on ByDimensionTransformation { inputAxes scale lookup { axis outputAxis unit min max field { id } } }
  }
}
"""

AS_AFFINE = """
query AsAffine($id: ID!) {
  scene(id: $id) {
    layers {
      placement
      placementInvariance
      asAffine {
        matrix inputAxes outputAxes total
        lookups { outputAxis inputAxis innerScale innerOffset scale offset field { id } dataset { id } }
      }
    }
  }
}
"""

TYX = [seed.axis("t", enums.AxisType.TIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
T = [seed.axis("t", enums.AxisType.TIME)]


def _world_sync(ctx: HttpContext, name: str, axes: list[tuple[str, str, str]], epoch=None) -> models.Scene:  # noqa: ANN001 - a datetime or None
    world = models.CoordinateSystem.objects.create(name=f"{name}/world", epoch=epoch, creator=ctx.request.user, organization=ctx.request.organization)
    models.Axis.objects.bulk_create(
        [models.Axis(coordinate_system=world, order=index, name=axis, type=type_, unit=unit) for index, (axis, type_, unit) in enumerate(axes)]
    )
    return models.Scene.objects.create(name=name, world=world, organization=ctx.request.organization)


async def _scene(ctx: HttpContext, name: str = "Events", *, axes: list[tuple[str, str, str]] | None = None) -> models.Scene:
    """A scene whose world is (t s, y µm, x µm)."""
    return await sync_to_async(_world_sync)(ctx, name, axes or [("t", "TIME", "second"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")])


async def _system(dataset: models.ArrayDataset) -> models.CoordinateSystem:
    return await sync_to_async(lambda: dataset.coordinate_system)()


async def _create(ctx: HttpContext, input_id: int, output_id: int, transform: dict):
    return await schema.execute(
        CREATE_TRANSFORM,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"input": str(input_id), "output": str(output_id), "transform": transform}},
    )


def _lookup(field_id: int, *, axis: str = "t", unit: str = "second", low: float = 0.0, high: float = 7.5, **extra) -> dict:
    return {"axis": axis, "field": str(field_id), "unit": unit, "min": low, "max": high, **extra}


async def _layer(ctx: HttpContext, scene: models.Scene) -> dict:
    result = await schema.execute(AS_AFFINE, context_value=_fresh_request(ctx), variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer


async def _movie_and_times(ctx: HttpContext, frames: int = 5, times_length: int | None = None):
    movie = await seed.create_array_dataset(ctx, "Movie", axes=TYX, shapes=[[frames, 8, 8]])
    times = await seed.create_array_dataset(ctx, "Frame times", axes=T, shapes=[[times_length or frames]])
    return movie, times, await _system(movie), await _system(times)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookup_beside_a_pixel_size_condenses_both(authenticated_context: HttpContext):
    """The case the lookup exists for: y,x scaled, t read from an array -- and the matrix survives.

    ABLATION: drop the lookup branch of `compose_placement` and `t` simply vanishes (no row, no
    lookup, `total` false); route the edge through a FIELD and `asAffine` errors outright.
    """
    movie, times, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(
        authenticated_context,
        movie_system.pk,
        world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.5, 0.5], "lookup": _lookup(times_system.pk)},
    )
    assert not result.errors, result.errors
    edge = result.data["createTransformation"]
    assert edge["inputAxes"] == ["y", "x"], "the named axes are the ones the parameters act on; the looked-up one is not among them"
    assert edge["lookup"] == {"axis": "t", "outputAxis": "t", "unit": "second", "min": 0.0, "max": 7.5, "field": {"id": str(times_system.pk)}}

    lens = await seed.create_lens(authenticated_context, movie)
    await _image_layer(scene, lens)
    layer = await _layer(authenticated_context, scene)
    affine = layer["asAffine"]

    assert layer["placement"] == "PLACED"
    assert affine["outputAxes"] == ["y", "x"]
    assert affine["matrix"] == [[0.0, 0.5, 0.0, 0.0], [0.0, 0.0, 0.5, 0.0]]
    assert affine["total"] is True, "the lookup accounts for t, so the placement covers the world"
    (lookup,) = affine["lookups"]
    assert lookup["outputAxis"] == "t" and lookup["inputAxis"] == "t"
    assert (lookup["innerScale"], lookup["innerOffset"], lookup["scale"], lookup["offset"]) == (1.0, 0.0, 1.0, 0.0)
    assert lookup["field"]["id"] == str(times_system.pk)
    assert lookup["dataset"]["id"] == str(times.pk)
    assert layer["placementInvariance"] != "DIFFEOMORPHIC", "a lookup along time leaves space affine"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookup_converts_its_values_unit(authenticated_context: HttpContext):
    """Unlike a bare FIELD, a lookup states its values' unit, so milliseconds land in a seconds world."""
    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk, unit="millisecond", high=7500.0)})
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    (lookup,) = (await _layer(authenticated_context, scene))["asAffine"]["lookups"]
    assert lookup["scale"] == approx(0.001)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_slice_and_a_later_clock_offset_compose_into_the_lookup(authenticated_context: HttpContext):
    """Frames 2..4 of the movie, on a session clock that sits 10 s into the experiment's world.

    The slice moves the *inner* index (lens frame 0 is movie frame 2); the clock offset moves
    the *outer* value. Both are affine on the one axis, so both fold in and nothing is lost.
    """
    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()
    session = await sync_to_async(_world_sync)(authenticated_context, "Session", [("t", "TIME", "second"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")])
    session_world = await sync_to_async(lambda: session.world)()

    result = await _create(authenticated_context, movie_system.pk, session_world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk)})
    assert not result.errors, result.errors
    result = await _create(
        authenticated_context, session_world.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["t"], "outputAxes": ["t"], "translation": [10.0]}
    )
    assert not result.errors, result.errors

    lens = await seed.create_lens(authenticated_context, movie, slices=[{"axis": "t", "start": 2, "stop": 5}])
    await _image_layer(scene, lens)
    (lookup,) = (await _layer(authenticated_context, scene))["asAffine"]["lookups"]

    assert lookup["innerScale"] == approx(1.0)
    assert lookup["innerOffset"] == approx(2.0), "lens frame 0 reads the array at movie frame 2"
    assert lookup["scale"] == approx(1.0)
    assert lookup["offset"] == approx(10.0), "the session sits 10 s into the world"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("times_length", "lookup_extra", "transform_extra", "message"),
    [
        (4, {}, {}, "one value per position"),
        (5, {"unit": "micrometer"}, {}, "no conversion"),
        (5, {}, {"inputAxes": ["t", "y"], "outputAxes": ["t", "y"]}, "an axis has one map"),
        (5, {"axis": "z"}, {}, "does not have"),
    ],
)
async def test_a_lookup_the_rows_contradict_is_refused(authenticated_context: HttpContext, times_length, lookup_extra, transform_extra, message):
    """What the rows can say is checked: length against the axis, unit against the output, one map per axis."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context, times_length=times_length)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", **transform_extra, "lookup": {**_lookup(times_system.pk), **lookup_extra}})
    assert result.errors, "the lookup must be refused"
    assert message in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_declared_values_must_increase(authenticated_context: HttpContext):
    """The server never reads the array, so its writer declares the range -- and a range that does not increase is no lookup."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk, low=3.0, high=3.0)})
    assert result.errors
    assert "strictly increase" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_two_array_is_no_lookup(authenticated_context: HttpContext):
    movie = await seed.create_array_dataset(authenticated_context, "Movie", axes=TYX, shapes=[[5, 8, 8]])
    grid = await seed.create_array_dataset(authenticated_context, "Not times", axes=[seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL)], shapes=[[5, 2]])
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, (await _system(movie)).pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup((await _system(grid)).pk)})
    assert result.errors
    assert "rank one" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_a_by_dimension_carries_a_lookup(authenticated_context: HttpContext):
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "SCALE", "scale": [1.0, 1.0, 1.0], "lookup": _lookup(times_system.pk)})
    assert result.errors, "a SCALE member has no `lookup` and must reject it rather than drop it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookup_is_walked_forwards_only_and_protects_its_array(authenticated_context: HttpContext):
    """Its inverse is a search through values the server never reads; and deleting the array would unplace the movie."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk)})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])

    assert graph_logic.is_invertible(edge) is False
    assert graph_logic.is_reverse_traversable(edge) is False
    assert await sync_to_async(graph_logic.is_condensable)(edge) is True

    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        await sync_to_async(models.CoordinateSystem.objects.filter(pk=times_system.pk).delete)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookup_along_space_is_not_affine(authenticated_context: HttpContext):
    """Uneven z planes bend nothing a matrix can state: the edge is DIFFEOMORPHIC and the gate says so."""
    stack = await seed.create_array_dataset(
        authenticated_context, "Stack", axes=[seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)], shapes=[[4, 8, 8]]
    )
    planes = await seed.create_array_dataset(authenticated_context, "Plane heights", axes=[seed.axis("z", enums.AxisType.SPACE)], shapes=[[4]])
    scene = await seed.create_scene(authenticated_context)  # (z, y, x) micrometre
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(
        authenticated_context,
        (await _system(stack)).pk,
        world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.1, 0.1], "lookup": _lookup((await _system(planes)).pk, axis="z", unit="micrometer", high=3.2)},
    )
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])

    assert graph_logic.invariance_of(edge) == enums.TransformInvariance.DIFFEOMORPHIC.value
    assert await sync_to_async(graph_logic.is_condensable)(edge) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookup_places_frames_absolutely_in_a_world_with_an_epoch(authenticated_context: HttpContext):
    """The epoch is the world's reference; the values are absolute coordinates in it -- no offset anywhere.

    Frames at 12.0 .. 19.5 s land at exactly those coordinates of a world anchored to 09:00, so
    frame 0 was taken at 09:00:12. The movie's grid has no epoch and needs none.
    """
    import datetime

    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    epoch = datetime.datetime(2026, 10, 22, 9, 0, tzinfo=datetime.timezone.utc)
    scene = await sync_to_async(_world_sync)(authenticated_context, "Anchored", [("t", "TIME", "second"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")], epoch)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk, low=12.0, high=19.5)})
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    (lookup,) = (await _layer(authenticated_context, scene))["asAffine"]["lookups"]
    assert (lookup["scale"], lookup["offset"]) == (1.0, 0.0), "the values are the world coordinates as they stand"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lookups_array_is_not_a_root(authenticated_context: HttpContext):
    """The frame times are part of the movie's placement, so the home page lists the movie, not them.

    ABLATION: drop `_not_a_field_sql` from the dataset arm of `_roots_sql` and the times array
    shows up as a parentless dataset of its own.
    """
    from core.logic import lineage as lineage_logic

    movie, times, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    before = {(node.kind, node.pk) for node in await sync_to_async(lineage_logic.roots)(organization=authenticated_context.request.organization, kinds=["dataset"])}
    assert ("dataset", times.pk) in before, "unplaced, the times array is just a dataset"

    result = await _create(authenticated_context, movie_system.pk, world.pk, {"kind": "BY_DIMENSION", "lookup": _lookup(times_system.pk)})
    assert not result.errors, result.errors

    after = {(node.kind, node.pk) for node in await sync_to_async(lineage_logic.roots)(organization=authenticated_context.request.organization, kinds=["dataset"])}
    assert ("dataset", movie.pk) in after
    assert ("dataset", times.pk) not in after

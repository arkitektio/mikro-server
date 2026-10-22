"""A BY_DIMENSION is parts side by side: each maps its own axes by one kind, FIELD among them.

An event-triggered movie's frames were taken at uneven instants, so no scale and offset along
`t` states when frame 3 was. The instants are an array; a FIELD part says "the world `t` of frame
`i` is `times[i]`" beside a SCALE part giving the pixel size. A warp field is a FIELD part over
every spatial axis, and a label mask keying its object table is a FIELD part whose values are ids
-- a key. Each placing part composes: the axes it gives come back as `asAffine.fields`, the rest as
matrix rows. The load-bearing pins below are that the spatial rows survive a field beside them,
that nothing passes through unless a part says so, that a field composes through a lens slice and
a later clock offset, that two field parts sit on one edge with both arrays protected, that a warp
is placed rather than refused, that a key places nothing, and that what the server cannot check
(the array's values) is declared rather than invented.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.models import ProtectedError
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
    inputAxes
    outputAxes
    ... on ByDimensionTransformation {
      parts { kind inputAxes outputAxes scale field { unit min max values spatial dereference field { id } } }
    }
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
        fields { outputAxis inner base component scale offset inputAxis innerScale innerOffset field { id } dataset { id } }
      }
    }
  }
}
"""

TYX = [seed.axis("t", enums.AxisType.TIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
ZYX = [seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
T = [seed.axis("t", enums.AxisType.TIME)]
PIXEL = {"kind": "SCALE", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.5, 0.5]}
SAME_YX = {"kind": "IDENTITY", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}


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


async def _create(ctx: HttpContext, input_id: int, output_id: int, parts: list[dict]):
    return await schema.execute(
        CREATE_TRANSFORM,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"input": str(input_id), "output": str(output_id), "transform": {"kind": "BY_DIMENSION", "parts": parts}}},
    )


def _times(field_id: int, *, axis: str = "t", unit: str = "second", low: float | None = 0.0, high: float | None = 7.5) -> dict:
    """A FIELD part reading one axis from a 1-D times array -- a lookup."""
    field = {key: value for key, value in {"system": str(field_id), "unit": unit, "min": low, "max": high}.items() if value is not None}
    return {"kind": "FIELD", "inputAxes": [axis], "outputAxes": [axis], "field": field}


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
async def test_a_field_part_beside_a_scale_part_composes_both(authenticated_context: HttpContext):
    """The case the parts exist for: y,x scaled, t read from an array -- and the matrix survives.

    ABLATION: drop the FIELD branch of `compose_placement` and `t` simply vanishes (no row, no
    field, `total` false).
    """
    movie, times, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [PIXEL, _times(times_system.pk)])
    assert not result.errors, result.errors
    edge = result.data["createTransformation"]
    assert (edge["inputAxes"], edge["outputAxes"]) == (["y", "x", "t"], ["y", "x", "t"]), "the edge's axes are its parts', in part order"
    scale, field = edge["parts"]
    assert (scale["kind"], scale["scale"], scale["field"]) == ("SCALE", [0.5, 0.5], None)
    assert field["kind"] == "FIELD" and field["field"] == {
        "unit": "second",
        "min": 0.0,
        "max": 7.5,
        "values": "SCALAR",
        "spatial": False,
        "dereference": False,
        "field": {"id": str(times_system.pk)},
    }

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    layer = await _layer(authenticated_context, scene)
    affine = layer["asAffine"]

    assert layer["placement"] == "PLACED"
    assert affine["outputAxes"] == ["y", "x"]
    assert affine["matrix"] == [[0.0, 0.5, 0.0, 0.0], [0.0, 0.0, 0.5, 0.0]]
    assert affine["total"] is True, "the field accounts for t, so the placement covers the world"
    (placed,) = affine["fields"]
    assert placed["outputAxis"] == "t" and placed["inputAxis"] == "t"
    assert (placed["innerScale"], placed["innerOffset"], placed["scale"], placed["offset"]) == (1.0, 0.0, 1.0, 0.0)
    assert placed["inner"] == [[1.0, 0.0, 0.0, 0.0]] and placed["base"] == [0.0, 0.0, 0.0, 0.0] and placed["component"] is None
    assert placed["field"]["id"] == str(times_system.pk)
    assert placed["dataset"]["id"] == str(times.pk)
    assert layer["placementInvariance"] != "DIFFEOMORPHIC", "a field along time leaves space affine"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nothing_passes_through_unless_a_part_says_so(authenticated_context: HttpContext):
    """A FIELD part alone places t and says nothing about y and x; an IDENTITY part states them.

    ABLATION: give `_by_dimension_forms` back its implicit pass-through and the first layer gets
    y and x rows nobody wrote.
    """
    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    alone = await _scene(authenticated_context, "Alone")
    stated = await _scene(authenticated_context, "Stated")

    for scene, parts in ((alone, [_times(times_system.pk)]), (stated, [SAME_YX, _times(times_system.pk)])):
        world = await sync_to_async(lambda scene=scene: scene.world)()
        result = await _create(authenticated_context, movie_system.pk, world.pk, parts)
        assert not result.errors, result.errors
        await _image_layer(scene, await seed.create_lens(authenticated_context, movie))

    affine = (await _layer(authenticated_context, alone))["asAffine"]
    assert affine["outputAxes"] == [] and affine["total"] is False, "only t was stated"
    assert [field["outputAxis"] for field in affine["fields"]] == ["t"]

    affine = (await _layer(authenticated_context, stated))["asAffine"]
    assert affine["outputAxes"] == ["y", "x"] and affine["matrix"] == [[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    assert affine["total"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_converts_its_values_unit(authenticated_context: HttpContext):
    """A FIELD part states its values' unit, so milliseconds land in a seconds world."""
    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [_times(times_system.pk, unit="millisecond", high=7500.0)])
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    (field,) = (await _layer(authenticated_context, scene))["asAffine"]["fields"]
    assert field["scale"] == approx(0.001)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_slice_and_a_later_clock_offset_compose_into_the_field(authenticated_context: HttpContext):
    """Frames 2..4 of the movie, on a session clock that sits 10 s into the experiment's world.

    The slice moves the *inner* index (lens frame 0 is movie frame 2); the clock offset moves
    the *outer* value. Both are affine on the one axis, so both fold in and nothing is lost.
    """
    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()
    session = await sync_to_async(_world_sync)(authenticated_context, "Session", [("t", "TIME", "second"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")])
    session_world = await sync_to_async(lambda: session.world)()

    result = await _create(authenticated_context, movie_system.pk, session_world.pk, [_times(times_system.pk)])
    assert not result.errors, result.errors
    result = await _create(authenticated_context, session_world.pk, world.pk, [{"kind": "TRANSLATION", "inputAxes": ["t"], "outputAxes": ["t"], "translation": [10.0]}])
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie, slices=[{"axis": "t", "start": 2, "stop": 5}]))
    (field,) = (await _layer(authenticated_context, scene))["asAffine"]["fields"]

    assert field["innerScale"] == approx(1.0)
    assert field["innerOffset"] == approx(2.0), "lens frame 0 reads the array at movie frame 2"
    assert field["scale"] == approx(1.0)
    assert field["offset"] == approx(10.0), "the session sits 10 s into the world"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("times_length", "field_extra", "beside", "message"),
    [
        (4, {}, [], "one value per position"),
        (5, {"unit": "micrometer"}, [], "no conversion"),
        (5, {}, [{"kind": "SCALE", "inputAxes": ["t", "y"], "outputAxes": ["t", "y"], "scale": [1.0, 1.0]}], "an axis has one map"),
        (5, {"inputAxes": ["z"], "outputAxes": ["z"]}, [], "do not exist"),
        (5, {"field": "unbounded"}, [], "declare both"),
    ],
)
async def test_a_field_part_the_rows_contradict_is_refused(authenticated_context: HttpContext, times_length, field_extra, beside, message):
    """What the rows can say is checked: length against the axis, unit against the output, one map per axis, a lookup's declared range."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context, times_length=times_length)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    part = _times(times_system.pk, low=None, high=None) if field_extra.get("field") == "unbounded" else {**_times(times_system.pk), **field_extra}
    if "unit" in field_extra:
        part = {**_times(times_system.pk), "field": {**_times(times_system.pk)["field"], "unit": field_extra["unit"]}}
    result = await _create(authenticated_context, movie_system.pk, world.pk, [*beside, part])
    assert result.errors, "the part must be refused"
    assert message in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_declared_values_must_increase(authenticated_context: HttpContext):
    """The server never reads the array, so its writer declares the range -- and a range that does not increase is no lookup."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [_times(times_system.pk, low=3.0, high=3.0)])
    assert result.errors
    assert "strictly increase" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_indexed_by_the_wrong_axes_is_refused(authenticated_context: HttpContext):
    """A scalar field read along `t` is indexed by one axis; a (t, c) array is indexed by two."""
    movie = await seed.create_array_dataset(authenticated_context, "Movie", axes=TYX, shapes=[[5, 8, 8]])
    grid = await seed.create_array_dataset(authenticated_context, "Not times", axes=[seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL)], shapes=[[5, 2]])
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, (await _system(movie)).pk, world.pk, [_times((await _system(grid)).pk)])
    assert result.errors
    assert "one axis per consumed axis" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_each_part_kind_reads_only_its_own_field(authenticated_context: HttpContext):
    """A SCALE part has no array, and a FIELD part has no scale: a stray is refused, never dropped."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [{**PIXEL, "field": _times(times_system.pk)["field"]}])
    assert result.errors and "does not read `field`" in str(result.errors[0]), result.errors
    result = await _create(authenticated_context, movie_system.pk, world.pk, [{**_times(times_system.pk), "scale": [1.0]}])
    assert result.errors and "does not read `scale`" in str(result.errors[0]), result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_part_is_walked_forwards_only_and_protects_its_array(authenticated_context: HttpContext):
    """Its inverse is a search through values the server never reads; and deleting the array would unplace the movie."""
    _, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [_times(times_system.pk)])
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])

    assert graph_logic.is_invertible(edge) is False
    assert graph_logic.is_reverse_traversable(edge) is False
    assert await sync_to_async(graph_logic.is_condensable)(edge) is True

    with pytest.raises(ProtectedError, match="TransformationField.field"):
        await sync_to_async(models.CoordinateSystem.objects.filter(pk=times_system.pk).delete)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_field_parts_sit_on_one_edge_and_protect_both_arrays(authenticated_context: HttpContext):
    """Uneven frames AND uneven planes: two FIELD parts beside a pixel size, one edge, no intermediate space."""
    movie = await seed.create_array_dataset(authenticated_context, "Stack movie", axes=[seed.axis("t", enums.AxisType.TIME), *ZYX], shapes=[[5, 4, 8, 8]])
    times = await seed.create_array_dataset(authenticated_context, "Frame times", axes=T, shapes=[[5]])
    planes = await seed.create_array_dataset(authenticated_context, "Plane heights", axes=[seed.axis("z", enums.AxisType.SPACE)], shapes=[[4]])
    scene = await _scene(authenticated_context, axes=[("t", "TIME", "second"), ("z", "SPACE", "micrometer"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")])
    world = await sync_to_async(lambda: scene.world)()
    times_system, planes_system = await _system(times), await _system(planes)

    result = await _create(
        authenticated_context,
        (await _system(movie)).pk,
        world.pk,
        [PIXEL, _times(times_system.pk), _times(planes_system.pk, axis="z", unit="micrometer", high=3.2)],
    )
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    affine = (await _layer(authenticated_context, scene))["asAffine"]
    assert sorted(field["outputAxis"] for field in affine["fields"]) == ["t", "z"]
    assert affine["outputAxes"] == ["y", "x"] and affine["total"] is True

    for system in (times_system, planes_system):
        with pytest.raises(ProtectedError, match="TransformationField.field"):
            await sync_to_async(models.CoordinateSystem.objects.filter(pk=system.pk).delete)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_along_space_is_placed_and_diffeomorphic(authenticated_context: HttpContext):
    """Uneven z planes bend nothing a matrix can state: the edge is DIFFEOMORPHIC -- and still placed, its z in `fields`.

    Drawing it is a viewer's question; whether it composes is this server's, and it does.
    """
    stack = await seed.create_array_dataset(authenticated_context, "Stack", axes=ZYX, shapes=[[4, 8, 8]])
    planes = await seed.create_array_dataset(authenticated_context, "Plane heights", axes=[seed.axis("z", enums.AxisType.SPACE)], shapes=[[4]])
    scene = await seed.create_scene(authenticated_context)  # (z, y, x) micrometre
    world = await sync_to_async(lambda: scene.world)()
    stack_system = await _system(stack)

    result = await _create(
        authenticated_context,
        stack_system.pk,
        world.pk,
        [{"kind": "SCALE", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.1, 0.1]}, _times((await _system(planes)).pk, axis="z", unit="micrometer", high=3.2)],
    )
    assert not result.errors, result.errors
    assert result.data["createTransformation"]["parts"][1]["field"]["spatial"] is True
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])

    assert graph_logic.invariance_of(edge) == enums.TransformInvariance.DIFFEOMORPHIC.value
    assert await sync_to_async(graph_logic.is_condensable)(edge) is True
    assert await sync_to_async(graph_logic.is_placeable_in)(world, stack_system, require_affine=True) is True

    await _image_layer(scene, await seed.create_lens(authenticated_context, stack))
    layer = await _layer(authenticated_context, scene)
    assert layer["placementInvariance"] == "DIFFEOMORPHIC"
    assert layer["asAffine"]["outputAxes"] == ["y", "x"]
    (field,) = layer["asAffine"]["fields"]
    assert (field["outputAxis"], field["inputAxis"]) == ("z", "z")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_warp_field_is_placed(authenticated_context: HttpContext):
    """A (v, z, y, x) DISPLACEMENT field warping a (z, y, x) stack: every spatial axis given by the array.

    Each world axis reads its own component, at the stack's own position, and adds that position
    back -- an offset is an offset *from* somewhere.
    """
    stack = await seed.create_array_dataset(authenticated_context, "Moving", axes=ZYX, shapes=[[4, 8, 8]])
    warp = await seed.create_array_dataset(authenticated_context, "Warp", axes=[seed.axis("v", enums.AxisType.DISPLACEMENT), *ZYX], shapes=[[3, 4, 8, 8]])
    scene = await seed.create_scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()
    stack_system = await _system(stack)

    result = await _create(
        authenticated_context,
        stack_system.pk,
        world.pk,
        [{"kind": "FIELD", "inputAxes": ["z", "y", "x"], "outputAxes": ["z", "y", "x"], "field": {"system": str((await _system(warp)).pk)}}],
    )
    assert not result.errors, result.errors
    stored = result.data["createTransformation"]["parts"][0]["field"]
    assert (stored["values"], stored["spatial"], stored["dereference"]) == ("DISPLACEMENT", True, False)

    assert await sync_to_async(graph_logic.is_placeable_in)(world, stack_system, require_affine=True) is True

    await _image_layer(scene, await seed.create_lens(authenticated_context, stack))
    layer = await _layer(authenticated_context, scene)
    affine = layer["asAffine"]
    assert layer["placementInvariance"] == "DIFFEOMORPHIC"
    assert affine["outputAxes"] == [] and affine["total"] is True
    by_axis = {field["outputAxis"]: field for field in affine["fields"]}
    assert set(by_axis) == {"z", "y", "x"}
    identity = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    for component, axis in enumerate(["z", "y", "x"]):
        field = by_axis[axis]
        assert field["component"] == component
        assert field["inner"] == identity, "the field is read at the stack's own voxel"
        assert field["base"] == identity[component], "a displacement is added to the position it is read at"
        assert field["inputAxis"] is None, "a 3-D field is no lookup"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_key_places_nothing(authenticated_context: HttpContext):
    """A mask's pixels are ids: a FIELD part too -- one that dereferences, so no walk places anything through it.

    An INDEX axis has no metric, so a value landing on one is an id, never a position, whatever
    holds that axis. ABLATION: drop `dereference` from `is_condensable` and the mask composes
    into its objects' index space as though an object id were a coordinate.
    """
    mask = await seed.create_array_dataset(authenticated_context, "Mask", axes=seed.YX_AXES, shapes=[[8, 8]])
    objects = await seed.create_array_dataset(authenticated_context, "Objects", axes=[seed.axis("i", enums.AxisType.INDEX)], shapes=[[16]])
    mask_system, object_system = await _system(mask), await _system(objects)

    result = await _create(authenticated_context, mask_system.pk, object_system.pk, [{"kind": "FIELD", "inputAxes": ["y", "x"], "outputAxes": ["i"], "field": {"system": str(mask_system.pk)}}])
    assert not result.errors, result.errors
    (part,) = result.data["createTransformation"]["parts"]
    assert part["field"]["dereference"] is True
    assert part["field"]["field"]["id"] == str(mask_system.pk), "a self field reads as its input"
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])

    assert edge.parts[0]["field"] == "self", "a self field is stored as 'self', and links nothing"
    assert await sync_to_async(edge.field_links.count)() == 0
    assert graph_logic.is_dereference(edge) is True
    assert await sync_to_async(graph_logic.is_condensable)(edge) is False
    assert await sync_to_async(graph_logic.is_placeable_in)(object_system, mask_system, require_affine=True) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_places_frames_absolutely_in_a_world_with_an_epoch(authenticated_context: HttpContext):
    """The epoch is the world's reference; the values are absolute coordinates in it -- no offset anywhere.

    Frames at 12.0 .. 19.5 s land at exactly those coordinates of a world anchored to 09:00, so
    frame 0 was taken at 09:00:12. The movie's grid has no epoch and needs none.
    """
    import datetime

    movie, _, movie_system, times_system = await _movie_and_times(authenticated_context)
    epoch = datetime.datetime(2026, 10, 22, 9, 0, tzinfo=datetime.timezone.utc)
    scene = await sync_to_async(_world_sync)(authenticated_context, "Anchored", [("t", "TIME", "second"), ("y", "SPACE", "micrometer"), ("x", "SPACE", "micrometer")], epoch)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [_times(times_system.pk, low=12.0, high=19.5)])
    assert not result.errors, result.errors

    await _image_layer(scene, await seed.create_lens(authenticated_context, movie))
    (field,) = (await _layer(authenticated_context, scene))["asAffine"]["fields"]
    assert (field["scale"], field["offset"]) == (1.0, 0.0), "the values are the world coordinates as they stand"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_fields_array_is_not_a_root(authenticated_context: HttpContext):
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

    result = await _create(authenticated_context, movie_system.pk, world.pk, [_times(times_system.pk)])
    assert not result.errors, result.errors

    after = {(node.kind, node.pk) for node in await sync_to_async(lineage_logic.roots)(organization=authenticated_context.request.organization, kinds=["dataset"])}
    assert ("dataset", movie.pk) in after
    assert ("dataset", times.pk) not in after


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_diagonal_affine_part_keeps_what_its_scale_keeps(authenticated_context: HttpContext):
    """A pixel size and a stage position as one AFFINE part read SIMILARITY, as the scale alone would."""
    movie, _, movie_system, _ = await _movie_and_times(authenticated_context)
    scene = await _scene(authenticated_context)
    world = await sync_to_async(lambda: scene.world)()

    result = await _create(authenticated_context, movie_system.pk, world.pk, [{"kind": "AFFINE", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "affine": [[0.5, 0.0, 10.0], [0.0, 0.5, 20.0]]}])
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])
    assert graph_logic.invariance_of(edge) == enums.TransformInvariance.SIMILARITY.value

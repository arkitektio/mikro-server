"""Values that cannot mean anything are refused at the door, in prose.

The domain's *structural* rules were already enforced -- `assert_edge_rank` holds an edge's
parameters to its endpoints' ranks, `_assemble_edge_params` refuses a parameter that
contradicts the kind, `assert_axis_names_unique` refuses a space two axes of one name.
Its *values* were not: an opacity of 17, a background colour of one component, a scene
policy that materializes nothing and a scale factor of zero were all written without
complaint, and surfaced -- when they surfaced at all -- somewhere else entirely.

Two altitudes, deliberately. A rule that needs no database is a pydantic validator on the
input model, and reaches the client through `prose_errors` as the sentence the validator
wrote; a rule that needs the two endpoints of an edge stays in `core.logic.graph` beside
the rank check. What is *not* here matters as much: every refusal below is of a value with
no reading at all, never one that is merely unusual -- see `test_the_merely_unusual_is_left_alone`.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id }
}
"""

CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) { id }
}
"""

CREATE_SCENE_FROM_CS = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) { id }
}
"""

CREATE_ANNOTATION = """
mutation Draw($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id }
}
"""

CREATE_ANIMATION = """
mutation Tour($input: CreateAnimationInput!) {
  createAnimation(input: $input) { id }
}
"""

CREATE_MESH_LAYER = """
mutation MeshLayer($input: CreateMeshLayerInput!) {
  createMeshLayer(input: $input) { id }
}
"""

WORLD_AXES = [
    {"name": "y", "type": "SPACE", "unit": "micrometer"},
    {"name": "x", "type": "SPACE", "unit": "micrometer"},
]


async def _counts() -> tuple[int, int, int]:
    """The rows a refused write must not have added."""
    return (
        await sync_to_async(models.CoordinateSystem.objects.count)(),
        await sync_to_async(models.Scene.objects.count)(),
        await sync_to_async(models.Annotation.objects.count)(),
    )


# --------------------------------------------------------------------------------------
# Coordinate systems and axes


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_axes_of_one_space_cannot_share_a_name(authenticated_context: HttpContext) -> None:
    """A duplicate name is prose, where the unique_together alone gave a raw IntegrityError.

    The database has refused this all along; what it could not do is say why. A name is how
    every edge refers to an axis -- `inputAxes`, `outputAxes` -- so the duplicate is worth a
    sentence rather than a 500 naming a Postgres constraint.
    """
    result = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={"input": {"name": "World", "axes": [{"name": "x", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}], "registrations": []}},
    )
    assert result.errors, "two axes named 'x' must be refused"
    assert "named uniquely" in str(result.errors[0]) and "'x'" in str(result.errors[0]), str(result.errors[0])
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(name="World").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collections_axes_are_taken_in_the_order_given(authenticated_context: HttpContext) -> None:
    """No axis *type* ordering is required of anything, and this is the boundary that proves it.

    A collection's axes arrive straight from the client, so it was the strictest of the axis
    writers. What the ordering rule bought there was nothing: `resolve_render_axes` finds the
    time axis by type, so `x, t` and `t, x` derive the same answer, and refusing one of them
    turned away a declaration that describes real data.

    The axes are stored in the order given, because that order is the data's -- which is the
    rule that does mean something, and the one `Axis.order` records.
    """
    result = await schema.execute(
        "mutation M($input: CreateAnnotationCollectionInput!) { createAnnotationCollection(input: $input) { id coordinateSystem { axes { name order } } } }",
        context_value=authenticated_context,
        variable_values={"input": {"name": "Given order", "axes": [{"name": "y", "type": "SPACE"}, {"name": "t", "type": "TIME"}, {"name": "x", "type": "SPACE"}]}},
    )
    assert not result.errors, str(result.errors and result.errors[0])
    axes = result.data["createAnnotationCollection"]["coordinateSystem"]["axes"]
    assert [(axis["name"], axis["order"]) for axis in axes] == [("y", 0), ("t", 1), ("x", 2)], "written by enumeration, in the order declared"
    assert await sync_to_async(models.AnnotationCollection.objects.filter(name="Given order").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stated_derivation_needs_a_space_to_derive_from(authenticated_context: HttpContext) -> None:
    """A derivation with no source is now unrepresentable rather than merely refused.

    It used to be a `transform` beside a nullable `coordinateSystem`, so omitting the
    source left the writer skipping the edge in silence -- a success response, and the
    relationship the caller stated nowhere on record. A guard was added for it; the
    discriminated union deleted the shape instead, which is the stronger fix. The check
    that survives is the schema's own: `kind` is required, and every member requires its
    own source id.
    """
    result = await schema.execute(
        "mutation M($input: CreateAnnotationCollectionInput!) { createAnnotationCollection(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {"name": "Floating", "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}], "derivedFrom": [{"transform": {"kind": "SCALE", "scale": [2.0, 2.0]}}]},
        },
    )
    assert result.errors and "DerivationSourceKind" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.AnnotationCollection.objects.filter(name="Floating").exists)()

    # And naming the kind without its id is refused by the member, in prose.
    result = await schema.execute(
        "mutation M($input: CreateAnnotationCollectionInput!) { createAnnotationCollection(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {"name": "Floating", "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}], "derivedFrom": [{"kind": "DATASET"}]},
        },
    )
    assert result.errors and "requires `dataset`" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.AnnotationCollection.objects.filter(name="Floating").exists)()


# --------------------------------------------------------------------------------------
# Scenes, layers and animations


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("color", [[1.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0, 1.0]])
async def test_a_background_is_rgba_or_nothing(authenticated_context: HttpContext, color: list) -> None:
    """Length only: no component range is written down for this field, and both the 0..1
    and the 0..255 convention are in use in this schema. "RGBA" says four either way."""
    before = await _counts()
    result = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "Scene", "backgroundColor": color}})

    assert result.errors, f"{color} is not an RGBA colour"
    assert "RGBA colour" in str(result.errors[0]) and "4 components" in str(result.errors[0]), str(result.errors[0])
    assert await _counts() == before, "a refused scene must not have minted its world either"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_policy_that_draws_nothing_is_refused(authenticated_context: HttpContext) -> None:
    """`nchildren: 0` returned a scene with no layers, indistinguishable from an empty space.

    The build breaks out of its loop the moment it has made `nchildren` layers, so zero
    breaks on the first pass -- and the caller cannot tell "the policy excluded everything"
    from "there was nothing there".
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()

    result = await schema.execute(
        CREATE_SCENE_FROM_CS,
        context_value=authenticated_context,
        variable_values={"input": {"name": "Empty", "coordinateSystem": intrinsic, "policy": {"nchildren": 0}}},
    )
    assert result.errors and "at least 1" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.Scene.objects.filter(name="Empty").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("opacity", [-0.5, 1.5])
async def test_a_layer_alpha_stays_between_transparent_and_opaque(authenticated_context: HttpContext, opacity: float) -> None:
    """Documented as 0..1 on the field *and* on the column, and enforced by neither."""
    scene = await seed.create_scene(authenticated_context)
    result = await schema.execute(
        CREATE_MESH_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "meshCollection": "1", "opacity": opacity}},
    )
    assert result.errors, f"opacity {opacity} is not an alpha"
    assert "runs from 0 (transparent) to 1 (opaque)" in str(result.errors[0]), str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_contrast_limits_are_ordered_but_not_bounded(authenticated_context: HttpContext) -> None:
    """The window may sit anywhere on the intensity scale; it may not be inside out.

    A contrast limit is in the data's own units -- the array's actual values -- so there is
    no interval to hold it to and a limit of 4000 is an ordinary 12-bit reading. What has no
    reading at all is a lower limit above the upper one: every intensity falls outside the
    window, so the layer is empty rather than dark. `invert` is how the mapping is reversed.

    Restated on the three sugar mutations because they carry `clim` on the model itself
    rather than through a `TransferFunctionInput`, so the render-graph rule does not reach them.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset)
    scene = await seed.create_scene(authenticated_context)
    await seed.register_into_scene(authenticated_context, scene, dataset)

    create = "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }"
    target = {"scene": str(scene.id), "lens": str(lens.id)}

    result = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {**target, "climMin": 900.0, "climMax": 100.0}})
    assert result.errors and "cannot exceed `climMax`" in str(result.errors[0]), str(result.errors and result.errors[0])

    result = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {**target, "climMin": 100.0, "climMax": 4000.0}})
    assert not result.errors, "raw intensity limits are the contract; there is no 0..1 range"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_transfer_curve_is_readable_as_a_curve(authenticated_context: HttpContext) -> None:
    """`stops` generalizes the contrast window, so it inherits the window's rules -- and its freedoms.

    Each side of a stop lives on a different scale, and the refusals follow from that. `value`
    is what the colormap is indexed with, so it is bounded to 0..1; `position` is a raw
    detector reading like `climMin` is, so it is bounded by nothing. What is refused is a list
    that cannot be read as a curve at all: fewer than two points have no interval to
    interpolate over, and positions that run backwards say the intensity axis doubles back.

    Two stops at one position is *not* refused -- that is how a hard break in the curve is
    authored, the same way `test_the_merely_unusual_is_left_alone` protects the rest of the
    merely-surprising.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset)
    scene = await seed.create_scene(authenticated_context)
    await seed.register_into_scene(authenticated_context, scene, dataset)

    create = "mutation M($input: CreateLayerInput!) { createLayer(input: $input) { id } }"

    async def with_stops(stops: list) -> tuple:
        result = await schema.execute(
            create,
            context_value=authenticated_context,
            variable_values={
                "input": {
                    "scene": str(scene.id),
                    "lens": str(lens.id),
                    "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel", "transfer": {"colormap": "VIRIDIS", "stops": stops}}]}},
                }
            },
        )
        return result.errors, str(result.errors[0]) if result.errors else ""

    errors, message = await with_stops([{"position": 0.0, "value": 0.0}])
    assert errors and "at least two control points" in message, message

    errors, message = await with_stops([])
    assert errors and "at least two control points" in message, message

    errors, message = await with_stops([{"position": 900.0, "value": 0.0}, {"position": 100.0, "value": 1.0}])
    assert errors and "cannot go backwards" in message, message

    errors, message = await with_stops([{"position": 0.0, "value": 0.0}, {"position": 100.0, "value": 1.5}])
    assert errors and "runs from 0 to 1" in message, message

    assert not await sync_to_async(models.Layer.objects.exists)(), "every refusal above happens at to_pydantic(), before any row is written"

    errors, _ = await with_stops([{"position": 100.0, "value": 0.0}, {"position": 4000.0, "value": 1.0}])
    assert not errors, "a position is a raw intensity, so 4000 is an ordinary 12-bit reading"

    errors, _ = await with_stops([{"position": 100.0, "value": 0.0}, {"position": 500.0, "value": 0.2}, {"position": 500.0, "value": 0.9}])
    assert not errors, "two stops at one position is a hard break in the curve, not a contradiction"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tour_needs_a_stop_and_travels_forwards(authenticated_context: HttpContext) -> None:
    """An empty tour was created without complaint, and no viewer could play it.

    The negative duration was refused too -- by a Postgres check constraint, as a 500 several
    frames after the caller's mistake.
    """
    scene = await seed.create_scene(authenticated_context)

    result = await schema.execute(CREATE_ANIMATION, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.id), "name": "Empty", "waypoints": []}})
    assert result.errors and "needs at least one" in str(result.errors[0]), str(result.errors and result.errors[0])

    result = await schema.execute(
        CREATE_ANIMATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "name": "Backwards", "waypoints": [{"camera": {"position": {}}, "durationMs": -500}]}},
    )
    assert result.errors and "cannot be negative" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.Animation.objects.exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_camera_zoom_is_a_ratio_a_viewer_divides_by(authenticated_context: HttpContext) -> None:
    """World units per screen pixel: zero has no view to describe, negative mirrors it."""
    scene = await seed.create_scene(authenticated_context)
    result = await schema.execute(
        CREATE_ANIMATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "name": "Zoomed", "waypoints": [{"camera": {"position": {}, "crossSectionScale": 0.0}}]}},
    )
    assert result.errors and "world units per screen pixel" in str(result.errors[0]), str(result.errors and result.errors[0])


# --------------------------------------------------------------------------------------
# Annotations and shapes


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shapes_vertices_all_have_the_same_width(authenticated_context: HttpContext) -> None:
    """A ragged shape was an `IndexError` -- a 500 -- or a silently truncated box.

    `aabb` takes its dimension from the *first* vertex, so a longer later one overruns it
    and a shorter one loses its trailing components without a word.
    """
    scene = await seed.create_scene(authenticated_context)
    before = await _counts()

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "PATH", "vectors": [[1.0, 2.0, 3.0], [4.0, 5.0]]}},
    )
    assert result.errors and "mixes widths" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert await _counts() == before, "a refused shape must not have minted the scene's collection"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_has_enough_vertices_for_its_kind(authenticated_context: HttpContext) -> None:
    """One corner is not a rectangle, and two points do not enclose a polygon.

    The two-corner families read `vectors[:2]` as opposite corners and answer with an
    all-null box when there is only one, silently.
    """
    scene = await seed.create_scene(authenticated_context)

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "RECTANGLE", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert result.errors and "at least 2 vertices" in str(result.errors[0]), str(result.errors and result.errors[0])

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "POLYGON", "vectors": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]}},
    )
    assert result.errors and "at least 3 vertices" in str(result.errors[0]), str(result.errors and result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shapes_colors_are_rgba(authenticated_context: HttpContext) -> None:
    """Both colours are documented RGBA and were stored at any length, at any value."""
    scene = await seed.create_scene(authenticated_context)

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]], "strokeColor": [255, 0, 0]}},
    )
    assert result.errors and "strokeColor" in str(result.errors[0]) and "4 components" in str(result.errors[0]), str(result.errors and result.errors[0])

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]], "fillColor": [0, 0, 0, 300]}},
    )
    assert result.errors and "run from 0 to 255" in str(result.errors[0]), str(result.errors and result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_with_no_geometry_at_all_is_still_allowed(authenticated_context: HttpContext) -> None:
    """The one case that looks like it should be caught, and must not be.

    An empty `vectors` is a declared absence of geometry rather than a malformed shape:
    `bbox_along_chain` answers null for it by design and `nearestAnnotations` excludes it
    on purpose. So the per-kind minimum governs a shape that *has* vertices, and says
    nothing about one that has none.
    """
    scene = await seed.create_scene(authenticated_context)
    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "RECTANGLE", "vectors": []}},
    )
    assert not result.errors, result.errors


# --------------------------------------------------------------------------------------
# The line itself


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_merely_unusual_is_left_alone(authenticated_context: HttpContext) -> None:
    """Every value here is odd and every one of them is somebody's real intent.

    This test is the boundary of the whole pass, and it fails loudly if a later hardening
    turns a suspicious value into a refused one:

    - a **negative scale** is a mirrored axis, and `form_interval` takes min/max per term
      so a sign flip can never produce a stored `min > max`;
    - an **inverted pair of corners** normalises through the same path;
    - a **duration of zero** is an instant cut between two stops;
    - **extra vertices** on a two-corner kind are ignored by a reader that takes
      `vectors[:2]`, not a contradiction.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    scene = await seed.create_scene(authenticated_context)

    result = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={"input": {"name": "Mirrored", "axes": WORLD_AXES, "registrations": [{"dataset": str(dataset.pk), "transform": {"kind": "SCALE", "scale": [-1.0, 1.0]}}]}},
    )
    assert not result.errors, "a mirrored axis is a real thing"

    result = await schema.execute(
        CREATE_ANNOTATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "kind": "RECTANGLE", "vectors": [[9.0, 9.0, 9.0], [1.0, 1.0, 1.0], [5.0, 5.0, 5.0]]}},
    )
    assert not result.errors, "corners the wrong way round normalise, and a third vertex is ignored"

    result = await schema.execute(
        CREATE_ANIMATION,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "name": "Cut", "waypoints": [{"camera": {"position": {}}, "durationMs": 0}]}},
    )
    assert not result.errors, "a zero duration is an instant cut, not an error"

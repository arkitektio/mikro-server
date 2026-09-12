"""The transform input union: the member is the kind, and nothing rides along.

An authored edge arrives as the flat ``TransformInput`` -- a ``kind`` plus the union of
every kind's parameter fields -- and is matched to a strict per-kind member model that
forbids what is not its own. So a parameter that contradicts the kind (a `translation`
on a SCALE edge, a `reason` on anything but UNMAPPABLE, axis names on a kind that acts
on every axis) is an **error naming both**, where it used to be silently dropped -- and
in the axes case silently *wrong*, because stored axis names override what
``edge_axis_names`` reports as the parameter ordering.

The same contract holds at every altitude: the parse layer for the API, and the
logic-layer writers (`build_registration_edge`, `write_relation_edge`) for callers below
it -- the tests here pin both, so removing either gate makes something fail. The member
inputs published under ``@unionElementOf`` are codegen's copy of the same truth.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from core import enums, models
from core.input_unions import parse_union_member
from core.inputs.coords import TRANSFORM_MEMBERS, FieldTransformInputModel, MapAxisTransformInputModel
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id kind inputAxes outputAxes }
}
"""

UPDATE_TRANSFORM = """
mutation Update($input: UpdateTransformationInput!) {
  updateTransformation(input: $input) { id version }
}
"""

CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id }
}
"""

WORLD_AXES = [
    {"name": "y", "type": "SPACE", "unit": "micrometer"},
    {"name": "x", "type": "SPACE", "unit": "micrometer"},
]


async def _dataset_and_world(ctx: HttpContext) -> tuple[str, str]:
    """A y/x dataset's intrinsic system, and a y/x world with nothing registered into it."""
    dataset = await seed.create_array_dataset(ctx, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system.pk)()
    result = await schema.execute(CREATE_CS, context_value=ctx, variable_values={"input": {"name": "World", "axes": WORLD_AXES, "registrations": []}})
    assert not result.errors, result.errors
    return str(intrinsic), str(result.data["createCoordinateSystem"]["id"])


async def build_sync(builder):
    """Run a synchronous fixture builder from an async test."""
    return await sync_to_async(builder)()


async def _create(ctx: HttpContext, input_id: str, output_id: str, transform: dict) -> ExecutionResult:
    return await schema.execute(
        CREATE_TRANSFORM,
        context_value=ctx,
        variable_values={"input": {"input": input_id, "output": output_id, "transform": transform}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transform", "phrase"),
    [
        # A parameter of some other member: named, not dropped.
        ({"kind": "SCALE", "scale": [1.0, 1.0], "translation": [1.0, 1.0]}, "A SCALE transformation does not read `translation`"),
        ({"kind": "SCALE", "scale": [1.0, 1.0], "affine": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]}, "A SCALE transformation does not read `affine`"),
        ({"kind": "TRANSLATION", "translation": [1.0, 1.0], "scale": [2.0, 2.0]}, "A TRANSLATION transformation does not read `scale`"),
        ({"kind": "AFFINE", "affine": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "scale": [2.0, 2.0]}, "An AFFINE transformation does not read `scale`"),
        ({"kind": "ROTATION", "affine": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "translation": [1.0, 1.0]}, "A ROTATION transformation does not read `translation`"),
        ({"kind": "MAP_AXIS", "inputAxes": ["y", "x"], "outputAxes": ["x", "y"], "scale": [1.0, 1.0]}, "A MAP_AXIS transformation does not read `scale`"),
        # IDENTITY reads nothing at all.
        ({"kind": "IDENTITY", "scale": [1.0, 1.0]}, "takes no parameters at all"),
        # Axis names on a kind that acts on every axis: the silently-wrong case.
        ({"kind": "SCALE", "scale": [1.0, 1.0], "inputAxes": ["y", "x"]}, "A SCALE transformation does not read `inputAxes`"),
        ({"kind": "AFFINE", "affine": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "outputAxes": ["y", "x"]}, "An AFFINE transformation does not read `outputAxes`"),
        # `reason` belongs to UNMAPPABLE, `field` to FIELD.
        ({"kind": "SCALE", "scale": [1.0, 1.0], "reason": "because"}, "A SCALE transformation does not read `reason`"),
        ({"kind": "UNMAPPABLE", "scale": [1.0, 1.0]}, "An UNMAPPABLE transformation does not read `scale`"),
        # Missing the one parameter the kind requires.
        ({"kind": "SCALE"}, "A SCALE transformation requires `scale`"),
        ({"kind": "AFFINE"}, "An AFFINE transformation requires `affine`"),
        ({"kind": "ROTATION"}, "A ROTATION transformation requires `affine`"),
        ({"kind": "FIELD", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}, "A FIELD transformation requires `field`"),
        ({"kind": "MAP_AXIS", "inputAxes": ["y", "x"]}, "A MAP_AXIS transformation requires `outputAxes`"),
        ({"kind": "BY_DIMENSION", "outputAxes": ["y"]}, "A BY_DIMENSION transformation requires `inputAxes`"),
    ],
)
async def test_a_parameter_that_contradicts_the_kind_is_an_error_not_a_drop(authenticated_context: HttpContext, transform: dict, phrase: str) -> None:
    """Every mismatch is named after both the kind and the field, and nothing is written."""
    intrinsic, world = await _dataset_and_world(authenticated_context)
    before = await sync_to_async(models.Transformation.objects.count)()

    result = await _create(authenticated_context, intrinsic, world, transform)

    assert result.errors, f"expected an error for {transform}"
    assert phrase in str(result.errors[0]), str(result.errors[0])
    assert await sync_to_async(models.Transformation.objects.count)() == before, "a refused edge must write nothing"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transform", "phrase"),
    [
        # A zero factor collapses its axis onto a point. `_scale_invariance` classifies a
        # scale by whether its entries are *equal*, so [0, 0] used to be reported as a
        # SIMILARITY -- angles and length ratios preserved -- for a map that preserves
        # nothing, and `is_invertible` is kind-only, so the client was then handed an
        # `inverted: true` step it could not honour.
        ({"kind": "SCALE", "scale": [0.0, 1.0]}, "no factor may be zero"),
        ({"kind": "SCALE", "scale": [0.0, 0.0]}, "no factor may be zero"),
        ({"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [1.0, 0.0]}, "no factor may be zero"),
        # A row whose linear part is all zeros sends every input to one value. The last
        # column is the translation and is excluded: an offset does not un-collapse a row.
        ({"kind": "AFFINE", "affine": [[0.0, 0.0, 5.0], [0.0, 1.0, 0.0]]}, "no row's linear part may be all zeros"),
        ({"kind": "ROTATION", "affine": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]}, "no row's linear part may be all zeros"),
    ],
)
async def test_a_map_that_collapses_an_axis_is_refused(authenticated_context: HttpContext, transform: dict, phrase: str) -> None:
    """The value rules, at the API altitude. Only a collapse -- never a merely odd number."""
    intrinsic, world = await _dataset_and_world(authenticated_context)
    before = await sync_to_async(models.Transformation.objects.count)()

    result = await _create(authenticated_context, intrinsic, world, transform)

    assert result.errors, f"expected an error for {transform}"
    assert phrase in str(result.errors[0]), str(result.errors[0])
    assert await sync_to_async(models.Transformation.objects.count)() == before, "a refused edge must write nothing"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_refinement_cannot_collapse_an_axis_either(authenticated_context: HttpContext) -> None:
    """`updateTransformation` gates values exactly as creation does, and nearly did not.

    It is the one write that reaches neither gate on its own: its parameters arrive flat --
    there is no `TransformInput`, so the union members' validators never run -- and it
    assembles its params dict by hand rather than through `_assemble_edge_params`. Refining
    a good SCALE edge to `[0, 0]` was the way left to store a collapsing map.
    """
    ctx = authenticated_context
    intrinsic, world = await _dataset_and_world(ctx)

    result = await _create(ctx, intrinsic, world, {"kind": "SCALE", "scale": [0.5, 0.5]})
    assert not result.errors, result.errors
    edge_id = result.data["createTransformation"]["id"]

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": edge_id, "scale": [0.0, 0.5]}})
    assert result.errors and "no factor may be zero" in str(result.errors[0]), str(result.errors and result.errors[0])

    edge = await sync_to_async(models.Transformation.objects.get)(pk=edge_id)
    assert edge.params == {"scale": [0.5, 0.5]}, "a refused refinement writes nothing"
    assert await sync_to_async(edge.provenance_entries.count)() == 1, "and leaves no history row behind either"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mirrored_axis_and_an_offset_of_zero_are_left_alone(authenticated_context: HttpContext) -> None:
    """The boundary of the rule above: a negative factor is a flip, and zero is an offset.

    A sign flip cannot produce a stored `min > max` -- `form_interval` takes min/max per
    term and `transformed_bbox` enumerates every corner -- so there is nothing to protect
    against, and refusing it would refuse a real acquisition geometry.
    """
    intrinsic, world = await _dataset_and_world(authenticated_context)

    result = await _create(authenticated_context, intrinsic, world, {"kind": "SCALE", "scale": [-1.0, 1.0]})
    assert not result.errors, result.errors

    result = await _create(authenticated_context, intrinsic, world, {"kind": "TRANSLATION", "translation": [0.0, 0.0]})
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_per_axis_edge_cannot_cross_a_rank_boundary(authenticated_context: HttpContext) -> None:
    """A scale carries one number per input axis, so its matrix is square at that rank.

    Only the *input* rank was checked, so a two-entry scale from a (y,x) grid into a
    four-axis world was written without complaint -- and surfaced nowhere near its author:
    `to_matrix` raises `NonAffineTransformError`, which the extent walk swallows into
    `ExtentState.NON_AFFINE`, leaving the source unboundable in every spatial query over
    that space forever.

    An AFFINE is deliberately *not* held to this: its matrix is M x (N+1) and rectangular
    by design, which is exactly how a rank-crossing edge is written.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()

    big = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": "Big", "axes": [{"name": "t", "type": "TIME", "unit": "second"}, {"name": "z", "type": "SPACE", "unit": "micrometer"}, *WORLD_AXES], "registrations": []}},
    )
    assert not big.errors, big.errors
    world = str(big.data["createCoordinateSystem"]["id"])

    result = await _create(ctx, intrinsic, world, {"kind": "SCALE", "scale": [0.1, 0.1]})
    assert result.errors and "relates spaces of equal rank" in str(result.errors[0]), str(result.errors and result.errors[0])

    # The same pair, said the way the model provides for: name the axes it acts on.
    result = await _create(ctx, intrinsic, world, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [0.1, 0.1]})
    assert not result.errors, result.errors

    # And a whole matrix crosses ranks unbothered: 4 rows out, 2+1 columns in.
    result = await _create(ctx, intrinsic, world, {"kind": "AFFINE", "affine": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]})
    assert result.errors and "all zeros" in str(result.errors[0]), "the zero rows are caught, not the rank"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scale_edge_also_gets_a_field_named_as_not_its_own(authenticated_context: HttpContext) -> None:
    """`field` is the FIELD member's alone; on any other kind it is a named stray."""
    intrinsic, world = await _dataset_and_world(authenticated_context)
    result = await _create(authenticated_context, intrinsic, world, {"kind": "SCALE", "scale": [1.0, 1.0], "field": world})
    assert result.errors and "A SCALE transformation does not read `field`" in str(result.errors[0]), str(result.errors and result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_wrapper_kinds_are_not_in_the_creatable_enum(authenticated_context: HttpContext) -> None:
    """SEQUENCE is unrepresentable in TransformInput, not merely refused.

    The ingest builds it together with its children; a client naming it gets an enum coercion
    error before any resolver runs. The logic-layer gate for internal callers is pinned
    separately below. BIJECTION stood beside it here until the kind was deleted (item 15 / D2);
    `test_the_deleted_kind_is_gone_from_both_enums` is what replaced that half.
    """
    intrinsic, world = await _dataset_and_world(authenticated_context)
    for kind in ("SEQUENCE",):
        result = await _create(authenticated_context, intrinsic, world, {"kind": kind})
        assert result.errors, f"{kind} must not be creatable"
        assert "CreatableTransformKind" in str(result.errors[0]), str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_registration_entry_is_validated_like_a_transformation(authenticated_context: HttpContext) -> None:
    """`registrations` lowers through the same union, so the same strays are refused."""
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    dataset_id = str(dataset.pk)

    result = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "World",
                "axes": WORLD_AXES,
                "registrations": [{"dataset": dataset_id, "transform": {"kind": "SCALE", "scale": [1.0, 1.0], "translation": [2.0, 2.0]}}],
            }
        },
    )
    assert result.errors and "A SCALE transformation does not read `translation`" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(name="World").exists)(), "a refused registration must roll the space back with it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_omitted_transform_registers_an_identity(authenticated_context: HttpContext) -> None:
    """Omitting `transform` on a registration entry means IDENTITY, as the docs promise."""
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system.pk)()

    result = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={"input": {"name": "World", "axes": WORLD_AXES, "registrations": [{"dataset": str(dataset.pk)}]}},
    )
    assert not result.errors, result.errors

    edge = await sync_to_async(models.Transformation.objects.get)(input_id=intrinsic, output_id=result.data["createCoordinateSystem"]["id"])
    assert edge.kind == enums.TransformKindChoices.IDENTITY.value
    assert edge.params == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_each_member_persists_exactly_its_own_shape(authenticated_context: HttpContext) -> None:
    """The row holds what the kind reads: params for the metric kinds, axes only for the axis kinds."""
    ctx = authenticated_context

    intrinsic, world = await _dataset_and_world(ctx)
    result = await _create(ctx, intrinsic, world, {"kind": "SCALE", "scale": [0.5, 0.5]})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])
    assert edge.params == {"scale": [0.5, 0.5]}
    assert edge.input_axes is None and edge.output_axes is None, "a metric edge stores no axis names: stored names would override the systems' ordering"

    result = await _create(ctx, world, world, {"kind": "MAP_AXIS", "inputAxes": ["y", "x"], "outputAxes": ["x", "y"]})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])
    assert edge.params == {} and edge.input_axes == ["y", "x"] and edge.output_axes == ["x", "y"]

    result = await _create(ctx, intrinsic, world, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "affine": [[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]]})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])
    assert edge.params == {"affine": [[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]]}, "a BY_DIMENSION's optional matrix rides in params"

    result = await _create(ctx, intrinsic, world, {"kind": "UNMAPPABLE", "reason": "one row per object"})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=result.data["createTransformation"]["id"])
    assert edge.params == {"reason": "one row per object"} and edge.input_axes is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_refinement_must_match_the_edges_kind(authenticated_context: HttpContext) -> None:
    """`updateTransformation` gates parameters exactly as creation does.

    Before this gate a stray `affine` merged onto any edge was stored, never read by
    `to_matrix` -- and on a childless composite it demoted the reported invariance,
    because `invariance_of` classifies those by params keys.
    """
    ctx = authenticated_context
    intrinsic, world = await _dataset_and_world(ctx)
    result = await _create(ctx, intrinsic, world, {"kind": "SCALE", "scale": [0.5, 0.5]})
    assert not result.errors, result.errors
    edge_id = result.data["createTransformation"]["id"]

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": edge_id, "translation": [1.0, 1.0]}})
    assert result.errors and "A SCALE transformation does not read `translation`" in str(result.errors[0]), str(result.errors and result.errors[0])
    assert "refining it would write a number nothing reads" in str(result.errors[0])

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": edge_id, "scale": [0.51, 0.51]}})
    assert not result.errors, result.errors
    edge = await sync_to_async(models.Transformation.objects.get)(pk=edge_id)
    assert edge.params == {"scale": [0.51, 0.51]}
    assert await sync_to_async(edge.provenance_entries.count)() == 2, "the refused refinement leaves no history row; the accepted one does -- creation plus one refinement"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_wrapper_refuses_refinement_toward_its_children(authenticated_context: HttpContext) -> None:
    """A SEQUENCE's parameters live on its children, and the update mutation says so."""
    ctx = authenticated_context
    intrinsic, world = await _dataset_and_world(ctx)

    def build_wrapper() -> models.Transformation:
        creation = seed._creation(ctx)
        return graph_logic._sequence(
            input_system=models.CoordinateSystem.objects.get(pk=intrinsic),
            output_system=models.CoordinateSystem.objects.get(pk=world),
            scale=[2.0, 2.0],
            translation=[0.5, 0.5],
            ctx=creation,
        )

    wrapper = await sync_to_async(build_wrapper)()
    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(wrapper.pk), "scale": [1.0, 1.0]}})
    assert result.errors and "its parameters live on its children" in str(result.errors[0]), str(result.errors and result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transform", "phrase"),
    [
        # Rank-deficient with no zero row anywhere: `assert_no_collapsed_rows` cannot see
        # it, and `is_invertible` is kind-only -- so this edge used to be written, offered
        # for backwards traversal, and handed to a client as an `inverted: true` step it
        # could not honour. `is_invertible`'s own docstring named it as uncaught.
        ({"kind": "AFFINE", "affine": [[1.0, 1.0, 0.0], [2.0, 2.0, 0.0]]}, "is singular"),
        ({"kind": "ROTATION", "affine": [[1.0, 1.0, 0.0], [1.0, 1.0, 0.0]]}, "is singular"),
        # The case most worth catching: a BY_DIMENSION maps its named axes one for one, so
        # its matrix is always square and a childless one is invertible *by kind*.
        ({"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "affine": [[1.0, 1.0, 0.0], [2.0, 2.0, 0.0]]}, "is singular"),
    ],
)
async def test_a_singular_map_is_refused_though_no_row_of_it_is_zero(authenticated_context: HttpContext, transform: dict, phrase: str) -> None:
    """A projection written as a full matrix: no zero factor, no zero row, and no inverse."""
    intrinsic, world = await _dataset_and_world(authenticated_context)
    before = await sync_to_async(models.Transformation.objects.count)()

    result = await _create(authenticated_context, intrinsic, world, transform)

    assert result.errors, f"expected an error for {transform}"
    assert phrase in str(result.errors[0]), str(result.errors[0])
    assert await sync_to_async(models.Transformation.objects.count)() == before, "a refused edge must write nothing"


async def _volume_world(ctx: HttpContext, name: str, axes=("z", "y", "x")) -> str:
    """A world of the named axes, with nothing registered into it."""
    result = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": name, "axes": [{"name": n, "type": "SPACE", "unit": "micrometer"} for n in axes], "registrations": []}},
    )
    assert not result.errors, result.errors
    return str(result.data["createCoordinateSystem"]["id"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_changing_affine_is_still_accepted(authenticated_context: HttpContext) -> None:
    """The negative of the singularity rule, and why it reads the shape rather than the kind.

    An AFFINE is rectangular *by design* -- `assert_edge_rank` admits M x (N+1) between
    spaces of different rank deliberately -- so its linear part is not square and there is
    no inverse to ask about. A check keyed on `kind == AFFINE` would refuse this, which is
    an ordinary authored registration.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    world = await _volume_world(authenticated_context, "Volume")

    # Three rows (the world's z, y, x), three columns (the dataset's y, x, plus translation),
    # so the linear part is 3 x 2 and has no determinant to take. The z row is a real slope,
    # not a zero row -- a tilted section, and `assert_no_collapsed_rows` would refuse a zero
    # one anyway, on the older rule that a dropped axis is stated with BY_DIMENSION.
    result = await _create(authenticated_context, intrinsic, world, {"kind": "AFFINE", "affine": [[0.5, 0.0, 5.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]})
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rotation_between_spaces_of_different_rank_is_refused(authenticated_context: HttpContext) -> None:
    """A rotation is an element of one space's orthogonal group; there is no such thing between two.

    `_PER_AXIS_KINDS` holds only SCALE and TRANSLATION, so a ROTATION carrying a whole
    matrix escaped that rule and landed in the rectangular M x (N+1) check, which happily
    accepted a "rotation" from a 2-axis grid into a 3-axis world.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    world = await _volume_world(authenticated_context, "Volume")

    result = await _create(authenticated_context, intrinsic, world, {"kind": "ROTATION", "affine": [[0.5, 0.0, 0.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]]})
    assert result.errors, "a rotation between spaces of different rank is not a rotation"
    assert "A ROTATION is a rotation *of* a space" in str(result.errors[0]), str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_map_axis_between_different_axis_sets_is_refused(authenticated_context: HttpContext) -> None:
    """A permutation relabels; it does not reshape -- and the read path could only say so with a stack trace.

    This used to pass every write check and then raise `NonAffineTransformError` from inside
    `permutation_matrix`, at read, in a message about a matrix rather than about the edge
    somebody authored. It is also the precondition `invert_step` relies on to invert a
    MAP_AXIS by swapping its two lists rather than solving anything.
    """
    dataset = await seed.create_array_dataset(authenticated_context, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    elsewhere = await _volume_world(authenticated_context, "Elsewhere", axes=("a", "b"))

    result = await _create(authenticated_context, intrinsic, elsewhere, {"kind": "MAP_AXIS", "inputAxes": ["y", "x"], "outputAxes": ["a", "b"]})
    assert result.errors, "a MAP_AXIS between disjoint axis sets is not a permutation"
    assert "permutes the axes of one coordinate vector" in str(result.errors[0]), str(result.errors[0])

    # The same pair, stated as what it actually is, is accepted.
    ok = await _create(authenticated_context, intrinsic, elsewhere, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["a", "b"]})
    assert not ok.errors, ok.errors


@pytest.mark.django_db(transaction=True)
def test_the_logic_layer_holds_the_same_line_for_internal_callers(authenticated_context: HttpContext) -> None:
    """The writers below the API refuse what the union makes unrepresentable above it.

    The parse layer is the API's gate; these are the same checks in
    `build_registration_edge` / `write_relation_edge`, so an internal caller cannot
    reintroduce the silent drop the union removed.
    """
    ctx = seed._creation(authenticated_context)

    def system(name: str) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_pixel_axes(made, seed.YX_AXES)
        return made

    a, b = system("a"), system("b")

    with pytest.raises(ValueError, match="does not read `translation`"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="SCALE", scale=[1.0, 1.0], translation=[1.0, 1.0], ctx=ctx)
    with pytest.raises(ValueError, match="takes no `inputAxes`"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="SCALE", scale=[1.0, 1.0], input_axes=["y", "x"], ctx=ctx)
    with pytest.raises(ValueError, match="belongs to an UNMAPPABLE edge"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="SCALE", scale=[1.0, 1.0], reason="because", ctx=ctx)
    with pytest.raises(ValueError, match="cannot be created directly"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="SEQUENCE", ctx=ctx)
    with pytest.raises(ValueError, match="does not read `affine`"):
        graph_logic.write_relation_edge(name="d", input_system=a, output_system=b, kind="IDENTITY", affine=[[1.0]], ctx=ctx)

    # The value rules hold here too: the union makes a collapsing map unrepresentable
    # through GraphQL, and nothing makes it unrepresentable to an internal writer.
    with pytest.raises(ValueError, match="no factor may be zero"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="SCALE", scale=[0.0, 1.0], ctx=ctx)
    with pytest.raises(ValueError, match="no row's linear part may be all zeros"):
        graph_logic.write_relation_edge(name="d", input_system=a, output_system=b, kind="AFFINE", affine=[[0.0, 0.0, 1.0], [0.0, 1.0, 0.0]], ctx=ctx)

    # Including the collapse no zero row betrays: rank-deficient, every row non-zero.
    with pytest.raises(ValueError, match="is singular"):
        graph_logic.build_registration_edge(input_system=a, output_system=b, kind="AFFINE", affine=[[1.0, 1.0, 0.0], [2.0, 2.0, 0.0]], ctx=ctx)

    # And the two rank rules, which only the endpoints can decide and so live only here.
    three = models.CoordinateSystem.objects.create(name="zyx", creator=ctx.user, organization=ctx.organization)
    graph_logic.create_pixel_axes(three, seed.SIMPLE_AXES)
    with pytest.raises(ValueError, match="ROTATION is a rotation"):
        graph_logic.build_registration_edge(
            input_system=a, output_system=three, kind="ROTATION", affine=[[0.5, 0.0, 0.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]], ctx=ctx
        )
    with pytest.raises(ValueError, match="permutes the axes of one coordinate vector"):
        graph_logic.build_registration_edge(input_system=a, output_system=three, kind="MAP_AXIS", input_axes=["y", "x"], output_axes=["y", "x"], ctx=ctx)

    # The one deliberate loosening: a BY_DIMENSION derivation's optional parameters now
    # persist, exactly as the registration path always stored them.
    edge = graph_logic.write_relation_edge(
        name="projection",
        input_system=a,
        output_system=b,
        kind="BY_DIMENSION",
        input_axes=["y", "x"],
        output_axes=["y", "x"],
        scale=[2.0, 2.0],
        ctx=ctx,
    )
    assert edge.params == {"scale": [2.0, 2.0]}


def test_a_derivation_may_state_any_creatable_kind() -> None:
    """The derivation subset is gone: one union, and MAP_AXIS/FIELD parse like any member.

    This is the parse every ``TransformInput.to_pydantic`` runs -- derivations included,
    since ``DerivationInput``/``DerivedFromInput`` carry the same union now.
    """
    member = parse_union_member(TRANSFORM_MEMBERS, {"kind": "MAP_AXIS", "input_axes": ["x", "y"], "output_axes": ["y", "x"]}, noun="transformation")
    assert isinstance(member, MapAxisTransformInputModel)

    member = parse_union_member(TRANSFORM_MEMBERS, {"kind": "FIELD", "field": "1", "input_axes": ["y", "x"], "output_axes": ["i"]}, noun="transformation")
    assert isinstance(member, FieldTransformInputModel)


def test_the_union_is_published_for_codegen() -> None:
    """The SDL carries the members, their annotations, and the flat unions they describe.

    The member inputs are referenced by no field -- dropping them from `types=[...]`
    would silently erase them from the SDL, so their presence is pinned here, exactly
    like the polymorphic read-side subtypes.
    """
    sdl = schema.as_str()
    assert "directive @unionElementOf(union: String!, discriminator: String!, key: String!) repeatable on INPUT_OBJECT" in sdl
    for member in [
        # IDENTITY included: the discriminator is a field, so the input is not empty,
        # and a client that cannot build this member can only say "same grid" by
        # omitting the transform -- which this schema reads as UNMAPPABLE, the opposite.
        "IdentityTransformInput",
        "ScaleTransformInput",
        "TranslationTransformInput",
        "AffineTransformInput",
        "RotationTransformInput",
        "ByDimensionTransformInput",
        "UnmappableTransformInput",
        "MapAxisTransformInput",
        "FieldTransformInput",
    ]:
        start = sdl.find(f"input {member} ")
        assert start >= 0, f"{member} missing from the SDL"
        header = sdl[start : sdl.find("{", start)]
        assert '@unionElementOf(union: "TransformInput", discriminator: "kind", key: ' in header, f"{member} lacks its annotation"

        # A member declares the parent's common fields as well as its own -- for this union
        # that is `kind` alone, and it defaults to the member's own key. GraphQL input types
        # have no inheritance, so a member that omits it generates a type a client cannot
        # construct without threading the discriminator in by hand.
        body = sdl[start : sdl.find("\n}", start)]
        key = header[header.find('key: "') + 6 : header.rfind('"')]
        assert f"kind: CreatableTransformKind! = {key}" in body, f"{member} does not declare `kind` defaulting to {key}"

    assert "RelationInput" not in sdl, "the derivation subset is gone: one union input, everywhere"
    assert "transform: TransformInput!" in sdl, "createTransformation requires its transform"
    assert "transform: TransformInput" in sdl
    creatable = sdl[sdl.find("enum CreatableTransformKind") : sdl.find("}", sdl.find("enum CreatableTransformKind"))]
    assert "SEQUENCE" not in creatable, "wrapper kinds stay out of the creatable enum"
    assert "BIJECTION" not in creatable and "BIJECTION" not in sdl, "the deleted kind is gone from the whole published schema"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sequence_child_answers_to_its_wrappers_rank(authenticated_context: HttpContext) -> None:
    """A wrapper child has no endpoints, and until 2026-08-21 that meant no rank check at all.

    `updateTransformation` guarded `assert_edge_rank` with `if transformation.input and
    transformation.output:`. `_sequence` creates its children with both null -- *"The children
    omit input and output: the wrapping sequence supplies them"* -- so the guard was false for
    every one of them, and `updatable_params` reads the *child's* kind, so a SCALE child
    accepted a vector of any length. `to_matrix` then wrote a two-entry scale's last number
    into `matrix[1][1]` and left the remaining axes unscaled: no error, wrong picture.

    Live when this was written: 225 wrapper children, 216 of them refinable -- every stepped
    lens and every downsampled pyramid level -- with their ids exposed through
    `SequenceTransformation.transformations`.
    """
    ctx = authenticated_context
    intrinsic, world = await _dataset_and_world(ctx)

    def build() -> models.Transformation:
        return graph_logic._sequence(
            input_system=models.CoordinateSystem.objects.get(pk=intrinsic),
            output_system=models.CoordinateSystem.objects.get(pk=world),
            scale=[2.0, 2.0],
            translation=[0.5, 0.5],
            ctx=seed._creation(ctx),
        )

    wrapper = await build_sync(build)
    child = await sync_to_async(lambda: wrapper.children.get(kind="SCALE"))()

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [3.0]}})
    assert result.errors, "a one-entry scale on a two-axis wrapper must be refused"
    assert "one entry per input axis: expected 2, got 1" in str(result.errors[0]), str(result.errors[0])

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [3.0, 3.0]}})
    assert not result.errors, result.errors
    refreshed = await sync_to_async(models.Transformation.objects.get)(pk=child.pk)
    assert refreshed.params == {"scale": [3.0, 3.0]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "world_axes",
    [
        # Different rank: isolates the equal-rank refusal, which recommends BY_DIMENSION by name.
        ["t", "z", "y", "x"],
        # Same rank, different names: isolates the same-names refusal. Without this case the
        # rank half of the subset rule carries the whole test and the name half ships unproven --
        # every live BY_DIMENSION wrapper names its full axis set, so nothing else would catch it.
        ["z", "y", "x"],
    ],
    ids=["rank-differs", "names-differ"],
)
async def test_a_by_dimension_child_answers_to_the_named_subset_not_the_whole_space(authenticated_context: HttpContext, world_axes: list[str]) -> None:
    """The anti-regression test, and the reason the fix reads the *parent's* kind.

    A BY_DIMENSION applies its children to the axes it names: `_sub_matrix` composes them at
    `len(acts_on_input)` and `_by_dimension_forms` labels the rows by `acts_on_output`. So a
    child's parameters are bound to that subset, not to the full space.

    Inheriting the parent's endpoints *without* that distinction is the obvious fix and it is
    wrong: the child's own kind is SCALE, so `assert_edge_rank` takes the per-axis branch and
    derives `rank_in` from the parent's whole system -- rejecting a perfectly good two-entry
    scale over `["y", "x"]` inside a three-axis wrapper. That would be a regression
    manufactured by the fix, on rows that exist today.

    Live data cannot catch this: all 9 BY_DIMENSION wrappers with children name their *full*
    axis set, so subset and whole coincide. The subset has to be constructed.

    **The two systems differ in rank *and* in ordered names, deliberately.** A wrapper from
    the same system to itself would make the subset rule the only thing under test, and the
    two name-comparing guards -- the equal-rank refusal and the same-names refusal -- would
    both pass for free because a system trivially matches itself. Relating ``(c,y,x)`` to
    ``(t,z,y,x)`` over the two axes they share is not an exotic fixture: it is the ordinary
    registration BY_DIMENSION exists to express, and the error messages of both those guards
    recommend BY_DIMENSION by name for exactly it.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])

    def build() -> models.Transformation:
        creation = seed._creation(ctx)
        system = models.CoordinateSystem.objects.get(pk=dataset.coordinate_system.pk)
        world = models.CoordinateSystem.objects.create(name="Volume", creator=creation.user, organization=creation.organization)
        models.Axis.objects.bulk_create(
            [
                models.Axis(
                    coordinate_system=world,
                    order=index,
                    name=name,
                    type=(enums.AxisTypeChoices.TIME.value if name == "t" else enums.AxisTypeChoices.SPACE.value),
                    unit=("second" if name == "t" else "micrometer"),
                )
                for index, name in enumerate(world_axes)
            ]
        )
        wrapper = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.BY_DIMENSION.value,
            input=system,
            output=world,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            params={},
            creator=creation.user,
            organization=creation.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            parent=wrapper,
            order=0,
            params={"scale": [1.0, 1.0]},
            creator=creation.user,
            organization=creation.organization,
        )
        return wrapper

    wrapper = await build_sync(build)
    child = await sync_to_async(lambda: wrapper.children.get(kind="SCALE"))()

    # Two entries, for the two named axes -- not the three the system has.
    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [2.0, 2.0]}})
    assert not result.errors, f"a subset-rank scale must be accepted: {result.errors}"

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [2.0, 2.0, 2.0]}})
    assert result.errors, "three entries is the whole space, not the named subset"
    assert "expected 2, got 3" in str(result.errors[0]), str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_identity_child_still_dies_before_the_rank_check(authenticated_context: HttpContext) -> None:
    """Guard ordering, pinned: the parameter gate runs before the rank gate.

    `updatable_params("IDENTITY")` is empty, so an IDENTITY child is refused for taking a
    parameter at all -- which is why the live count of *refinable* wrapper children is 216 and
    not 225. Resolving endpoints from the parent must not move that refusal.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.YX_AXES, shapes=[[64, 64]])

    def build() -> models.Transformation:
        creation = seed._creation(ctx)
        system = models.CoordinateSystem.objects.get(pk=dataset.coordinate_system.pk)
        wrapper = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.BY_DIMENSION.value,
            input=system, output=system, input_axes=["y", "x"], output_axes=["y", "x"],
            params={}, creator=creation.user, organization=creation.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.IDENTITY.value, parent=wrapper, order=0,
            params={}, creator=creation.user, organization=creation.organization,
        )
        return wrapper

    wrapper = await build_sync(build)
    child = await sync_to_async(lambda: wrapper.children.get(kind="IDENTITY"))()

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [2.0, 2.0]}})
    assert result.errors
    assert "takes no parameters at all" in str(result.errors[0]), str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_per_axis_edge_between_differently_ordered_spaces_is_refused(authenticated_context: HttpContext) -> None:
    """A SCALE binds its i-th number to the i-th axis of each system, so their orders are the
    whole of what the numbers mean.

    `(z,y,x)` into `(x,y,z)` was accepted and the factor meant for z landed on x -- no error at
    write, none at read, and the transposition then rode into every extent, `asAffine` and
    `inView` answer. IDENTITY already held itself to ordered equality; this closes the gap.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    # The same two axes, named the other way round -- both SPACE, both micrometer, same rank.
    reordered = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": "Reordered", "axes": [
            {"name": "x", "type": "SPACE", "unit": "micrometer"},
            {"name": "y", "type": "SPACE", "unit": "micrometer"},
        ], "registrations": []}},
    )
    assert not reordered.errors, reordered.errors
    world = str(reordered.data["createCoordinateSystem"]["id"])

    result = await _create(ctx, intrinsic, world, {"kind": "SCALE", "scale": [2.0, 3.0]})
    assert result.errors, "a SCALE between differently-ordered axis names must be refused"
    message = str(result.errors[0])
    assert "must name their axes the same way" in message
    assert "BY_DIMENSION" in message, "point at the kind that can state a reorder honestly"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_name_changing_affine_is_still_accepted(authenticated_context: HttpContext) -> None:
    """The anti-over-fix test. AFFINE must NOT get the name rule.

    `_forms_from_matrix` labels an affine's rows by the output axes and its columns by the
    input axes -- both orders are the author's explicit statement, and a rank- and
    name-changing AFFINE is legal by design. Applying the per-axis rule to it would be a guess
    dressed as a check, and would break the ordinary `(t,z,y,x) -> (c,y,x)` registration.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    other = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": "Two axis", "axes": [
            {"name": "v", "type": "SPACE", "unit": "micrometer"},
            {"name": "u", "type": "SPACE", "unit": "micrometer"},
        ], "registrations": []}},
    )
    assert not other.errors, other.errors
    world = str(other.data["createCoordinateSystem"]["id"])

    # Two output axes, three input axes plus the translation column.
    result = await _create(ctx, intrinsic, world, {"kind": "AFFINE", "affine": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]})
    assert not result.errors, f"a rank-changing AFFINE is legal by design: {result.errors}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rotation_between_differently_named_spaces_of_equal_rank_is_refused(authenticated_context: HttpContext) -> None:
    """ROTATION's line in the name rule, on its own, so it can be reverted on its own.

    ROTATION is the one entry in `_NAME_ORDERED_KINDS` that carries a whole matrix rather than
    one number per axis, so it does not follow from SCALE's argument and is not covered by
    SCALE's test. It is included on a different ground: a rotation is an element of *one*
    space's orthogonal group, so its two endpoints are the same space -- and two spaces that
    name their axes differently are not the same space, whatever their rank.

    The shape this refuses that someone may nonetheless have meant is a rotation between a
    pixel frame and a differently-named physical frame of equal rank. That is a BY_DIMENSION,
    or a MAP_AXIS followed by a rotation, and the message says so. If that judgement turns out
    to be wrong, take ROTATION out of `_NAME_ORDERED_KINDS` and delete this test: SCALE and
    TRANSLATION stand without it.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    physical = await _volume_world(ctx, "Slide", axes=("v", "u"))

    # A genuine 90-degree rotation: orthonormal, square, and the right rank on both sides.
    # Nothing about the matrix is wrong -- only what it claims to relate.
    result = await _create(ctx, intrinsic, physical, {"kind": "ROTATION", "affine": [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0]]})
    assert result.errors, "a ROTATION between two differently-named spaces must be refused"
    message = str(result.errors[0])
    assert "must name their axes the same way" in message, message
    assert "BY_DIMENSION" in message, "point at the kind that can state a reorder honestly"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_by_dimension_child_is_not_refused_for_an_index_axis_its_parent_left_alone(authenticated_context: HttpContext) -> None:
    """The INDEX guard follows the same subset rule as the three checks below it.

    `_METRIC_KINDS` refuses arithmetic on an INDEX axis, and rightly: the distance between
    object 3 and object 4 means nothing. But a refinement is checked against the *child's*
    kind, so a SCALE child under a BY_DIMENSION wrapper reached that guard with its parent's
    whole systems -- and was refused for an axis its parent deliberately did not name.

    The shape that refuses is item 7's product space: an `object_id` INDEX axis alongside
    `y, x`, registered into a purely spatial physical space over the two axes they share. The
    wrapper itself is creatable today (BY_DIMENSION is not a metric kind), so this bit only
    the child -- one guard further up than the rank and name checks, and the same failure.
    """
    ctx = authenticated_context

    def build() -> models.Transformation:
        creation = seed._creation(ctx)
        objects = models.CoordinateSystem.objects.create(name="Objects", creator=creation.user, organization=creation.organization)
        models.Axis.objects.bulk_create(
            [
                models.Axis(coordinate_system=objects, order=0, name="object_id", type=enums.AxisTypeChoices.INDEX.value, unit=None),
                models.Axis(coordinate_system=objects, order=1, name="y", type=enums.AxisTypeChoices.SPACE.value, unit="micrometer"),
                models.Axis(coordinate_system=objects, order=2, name="x", type=enums.AxisTypeChoices.SPACE.value, unit="micrometer"),
            ]
        )
        physical = models.CoordinateSystem.objects.create(name="Slide", creator=creation.user, organization=creation.organization)
        models.Axis.objects.bulk_create(
            [
                models.Axis(coordinate_system=physical, order=index, name=name, type=enums.AxisTypeChoices.SPACE.value, unit="micrometer")
                for index, name in enumerate(["y", "x"])
            ]
        )
        wrapper = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.BY_DIMENSION.value,
            input=objects,
            output=physical,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            params={},
            creator=creation.user,
            organization=creation.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            parent=wrapper,
            order=0,
            params={"scale": [1.0, 1.0]},
            creator=creation.user,
            organization=creation.organization,
        )
        return wrapper

    wrapper = await build_sync(build)
    child = await sync_to_async(lambda: wrapper.children.get(kind="SCALE"))()

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [0.5, 0.5]}})
    assert not result.errors, f"the child scales y and x, and touches no INDEX axis: {result.errors}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_index_axis_the_edge_does_act_on_is_still_refused(authenticated_context: HttpContext) -> None:
    """The negative of the rule above: narrowing the scan to the subset must not disarm it.

    Same two systems, but the wrapper names `object_id` -- so the child's first number really
    does scale an index, and the guard must still fire.
    """
    ctx = authenticated_context

    def build() -> models.Transformation:
        creation = seed._creation(ctx)
        objects = models.CoordinateSystem.objects.create(name="Objects", creator=creation.user, organization=creation.organization)
        models.Axis.objects.bulk_create(
            [
                models.Axis(coordinate_system=objects, order=0, name="object_id", type=enums.AxisTypeChoices.INDEX.value, unit=None),
                models.Axis(coordinate_system=objects, order=1, name="y", type=enums.AxisTypeChoices.SPACE.value, unit="micrometer"),
            ]
        )
        elsewhere = models.CoordinateSystem.objects.create(name="Elsewhere", creator=creation.user, organization=creation.organization)
        models.Axis.objects.bulk_create(
            [
                models.Axis(coordinate_system=elsewhere, order=0, name="object_id", type=enums.AxisTypeChoices.INDEX.value, unit=None),
                models.Axis(coordinate_system=elsewhere, order=1, name="y", type=enums.AxisTypeChoices.SPACE.value, unit="micrometer"),
            ]
        )
        wrapper = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.BY_DIMENSION.value,
            input=objects,
            output=elsewhere,
            input_axes=["object_id", "y"],
            output_axes=["object_id", "y"],
            params={},
            creator=creation.user,
            organization=creation.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            parent=wrapper,
            order=0,
            params={"scale": [1.0, 1.0]},
            creator=creation.user,
            organization=creation.organization,
        )
        return wrapper

    wrapper = await build_sync(build)
    child = await sync_to_async(lambda: wrapper.children.get(kind="SCALE"))()

    result = await schema.execute(UPDATE_TRANSFORM, context_value=ctx, variable_values={"input": {"id": str(child.pk), "scale": [2.0, 2.0]}})
    assert result.errors, "object 3 x 2 = object 6 is not a thing, and the wrapper named the axis"
    assert "is an INDEX axis" in str(result.errors[0]), str(result.errors[0])


def test_the_deleted_kind_is_gone_from_both_enums() -> None:
    """BIJECTION is removed, not merely uncreatable — item 15 / D2.

    Zero rows, no writer anywhere in the server, and `step_forms` had no branch for it: one
    would have fallen through to `_step_matrix`, which composes *all* the children when there
    are any, so a forward map times its own given inverse multiplied out to the identity. A
    silent no-op in the one kind whose entire purpose was to carry a map that cannot be
    derived. Advertising a kind nobody can produce is the objection this codebase makes
    elsewhere, so it went rather than getting a branch.

    Pinned on the *storage* enum as well as the published one: `tests/test_schema.py` asserts
    the two are equal, so a half-removal fails there — but this says which direction is meant.
    An inverse that cannot be derived is still expressible, as a FIELD whose values are the
    map in whichever direction the author needs.
    """
    assert not hasattr(enums.TransformKindChoices, "BIJECTION"), "the storage enum still carries the deleted kind"
    assert not hasattr(enums.TransformKind, "BIJECTION"), "the GraphQL enum still carries the deleted kind"
    assert "BIJECTION" not in {choice.value for choice in enums.TransformKindChoices}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_number_free_edge_may_not_relate_two_different_units(authenticated_context: HttpContext):
    """An IDENTITY between a micrometre space and a nanometre one is a claim that they are equal.

    It used to be accepted, because the IDENTITY branch compared axis *names* and nothing on the
    write path ever read `Axis.unit`. The two composers then disagreed about that one edge by a
    factor of 1000: the axis-keyed one (`step_forms`, behind `Layer.asAffine`) applied
    `_pass_through_factor`, while the fixed-rank one (`to_matrix`, behind the annotation bounding
    box) has no units in its signature at all. One edge, two stored answers, three orders of
    magnitude apart -- so the edge is refused rather than a second composer taught about units.
    """
    ctx = seed._creation(authenticated_context)

    def space(name: str, unit: str) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_physical_axes(made, [seed.physical_axis("y", enums.AxisType.SPACE, unit), seed.physical_axis("x", enums.AxisType.SPACE, unit)])
        return made

    microns, nanos = await sync_to_async(space)("Microns", "micrometer"), await sync_to_async(space)("Nanos", "nanometer")

    with pytest.raises(ValueError, match="carries no numbers"):
        await sync_to_async(graph_logic.build_registration_edge)(input_system=microns, output_system=nanos, kind="IDENTITY", ctx=ctx)

    # The repair the message names: a map that *states* its factor is allowed to relate them.
    await sync_to_async(graph_logic.build_registration_edge)(input_system=microns, output_system=nanos, kind="SCALE", scale=[1000.0, 1000.0], ctx=ctx)

    # And two spaces that agree, or decline to claim, are untouched.
    same = await sync_to_async(space)("AlsoMicrons", "micrometer")
    await sync_to_async(graph_logic.build_registration_edge)(input_system=microns, output_system=same, kind="IDENTITY", ctx=ctx)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_correspondence_may_not_cross_axis_kinds(authenticated_context: HttpContext):
    """`inputAxes: ["c"] -> outputAxes: ["z"]` maps a channel index onto a position. It is refused.

    The named-subset rules establish that the axes exist and pair one for one; none of them looked
    at what the axes *were*, so a channel could be mapped onto a spatial axis and nothing
    downstream could catch it -- by then it is two names and a matrix.

    INDEX stays exempt on either side, because where an enumeration's objects sit is not a
    property it carries but the thing a registration establishes.
    """
    ctx = seed._creation(authenticated_context)

    def space(name: str, axes) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_pixel_axes(made, axes)
        return made

    channelled = await sync_to_async(space)("Channelled", seed.SIMPLE_AXES)
    volume = await sync_to_async(space)("Volume", [seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)])

    with pytest.raises(ValueError, match="relates two different kinds of axis"):
        await sync_to_async(graph_logic.build_registration_edge)(
            input_system=channelled, output_system=volume, kind="BY_DIMENSION", input_axes=["c"], output_axes=["z"], ctx=ctx
        )

    # Same-kind correspondences are unaffected, whatever the axes are named.
    await sync_to_async(graph_logic.build_registration_edge)(
        input_system=channelled, output_system=volume, kind="BY_DIMENSION", input_axes=["y", "x"], output_axes=["y", "x"], ctx=ctx
    )

    # An INDEX axis is the deliberate wildcard: this is the ordinary product-space placement.
    objects = await sync_to_async(space)("Objects", [seed.axis("i", enums.AxisType.INDEX)])
    await sync_to_async(graph_logic.build_registration_edge)(
        input_system=objects, output_system=volume, kind="BY_DIMENSION", input_axes=["i"], output_axes=["z"], ctx=ctx
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_clocks_anchored_differently_may_not_be_related_silently(authenticated_context: HttpContext):
    """`epoch` says `wall_clock = epoch + t * unit`, and nothing ever read it.

    So a path across two spaces with different epochs treated their `t = 0` as the same instant:
    a 09:00 acquisition aligned against an 11:00 one was two hours wrong, with no error anywhere.
    Refused rather than composed -- an offset folded in from a column neither endpoint's
    parameters mention is a fact stored where no query can find it. Say it as a TRANSLATION.
    """
    import datetime

    ctx = seed._creation(authenticated_context)

    def clock(name: str, epoch) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, epoch=epoch, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_physical_axes(made, [seed.physical_axis("t", enums.AxisType.TIME, "second")])
        return made

    nine = datetime.datetime(2026, 8, 21, 9, 0, tzinfo=datetime.timezone.utc)
    eleven = datetime.datetime(2026, 8, 21, 11, 0, tzinfo=datetime.timezone.utc)

    morning, later = await sync_to_async(clock)("Morning", nine), await sync_to_async(clock)("Later", eleven)
    with pytest.raises(ValueError, match="anchor it to different instants"):
        await sync_to_async(graph_logic.build_registration_edge)(input_system=morning, output_system=later, kind="IDENTITY", ctx=ctx)

    # Sharing an epoch, or declining to name one, is unaffected.
    same = await sync_to_async(clock)("AlsoMorning", nine)
    await sync_to_async(graph_logic.build_registration_edge)(input_system=morning, output_system=same, kind="IDENTITY", ctx=ctx)
    unanchored = await sync_to_async(clock)("Unanchored", None)
    await sync_to_async(graph_logic.build_registration_edge)(input_system=morning, output_system=unanchored, kind="IDENTITY", ctx=ctx)

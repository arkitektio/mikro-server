"""A FIELD edge: a map given by the values of an array, where the array is a node.

The label-mask case is the point. Segment a channel and you get a mask whose pixel values
are object ids; measure the objects and you get a table whose `i` column holds those same
ids. The correspondence is total and exact -- pixel value 7 IS row i=7 -- and it is not
geometric, so before this it could only be written UNMAPPABLE, which says the opposite.

The load-bearing tests here are the dereference one (a mask keys a table through an edge
whose `field` is the mask's own system), the rank one (a FIELD's consumed/produced axes must
account for the endpoints exactly, which is what makes the map checkable rather than
conventional), the refusal ones (no metric kind over an index space; no scalar field
claiming to produce two axes), and the inversion one (a FIELD never walks backwards, because
an object is a set of pixels and the reverse would ask for a point where there is a set).
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) {
    id
    kind
    input { id }
    output { id }
    ... on FieldTransformation { field { id name axes { name type } } }
  }
}
"""

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    columns { name role axisType }
    coordinateSystem { id  axes { name type unit order } }
  }
}
"""


async def _parquet(ctx: HttpContext, key: str, columns: list[tuple[str, str]] | None = None) -> models.ParquetStore:
    """A finished store carrying the file's own schema.

    The schema is load-bearing since 3b: `createTableDataset` reads a column's name and type
    off the store rather than from the caller, so a store that records none has nothing for a
    table to be declared over -- and `_resolve_store` refuses it rather than reaching for an S3
    no unit test has.
    """
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization,
        populated=True, columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in (columns or [])],
    )


async def _objects_table(ctx: HttpContext, name: str = "nuclei morphology") -> dict:
    """A table whose own space IS the space of object ids: one INDEX coordinate column."""
    store = await _parquet(ctx, name.replace(" ", "-"), [("i", "BIGINT"), ("area", "DOUBLE")])
    result = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={
            "input": {
                "name": name,
                "data": str(store.pk),
                "columns": [
                    {"name": "i", "dtype": "BIGINT", "axisType": "INDEX"},
                    {"name": "area", "dtype": "DOUBLE"},
                ],
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_id_column_is_the_tables_own_axis(authenticated_context: HttpContext):
    """A COORDINATE column's values ARE its coordinates -- for `i` in object ids as for `x` in nanometres.

    That consistency is the whole argument for admitting INDEX here. Declared as a plain ID
    role the column is data, and the table degenerates to an `object` axis enumerating *rows*
    -- a different enumeration from the ids, which is why row 3 may hold i=42 and why nothing
    could join the two.
    """
    table = await _objects_table(authenticated_context)

    axes = table["coordinateSystem"]["axes"]
    assert [a["name"] for a in axes] == ["i"], "the id column is the axis, not a degenerate `object` row counter"
    assert axes[0]["type"] == "INDEX"
    assert axes[0]["unit"] is None, "an INDEX axis has no metric, so nothing to measure in"
    assert next(c for c in table["columns"] if c["name"] == "area")["role"] == "ATTRIBUTE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_index_coordinate_column_refuses_a_unit(authenticated_context: HttpContext):
    """The distance between object 3 and object 4 is not a small number, it is not a number.

    ABLATION: `assert_unit_matches_type` cannot catch this -- INDEX is absent from its
    dimension map, which reads as "any unit is fine" -- so drop the check in
    `_validate_columns` and 'nanometer' rides onto an axis that measures nothing.
    """
    store = await _parquet(authenticated_context, "united", [("i", "BIGINT")])
    result = await schema.execute(
        CREATE_TABLE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "bad",
                "data": str(store.pk),
                "columns": [{"name": "i", "dtype": "BIGINT", "axisType": "INDEX", "unit": "nanometer"}],
            }
        },
    )
    assert result.errors, "an INDEX axis with a unit must be refused"
    assert "no metric" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mask_dereferences_into_a_table_of_objects(authenticated_context: HttpContext):
    """The label mask IS the map: a FIELD whose `field` is the input's own system.

    (y,x) is consumed, `i` is produced by the value at each pixel, and nothing passes through.
    This is the edge that used to be UNMAPPABLE -- lineage kept, map lost.
    """
    mask = await seed.create_array_dataset(authenticated_context, "nuclei labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _objects_table(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": str(mask_system.pk),
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i"],
                },
            }
        },
    )
    assert not result.errors, result.errors

    edge = result.data["createTransformation"]
    assert edge["kind"] == "FIELD"
    assert edge["field"]["id"] == edge["input"]["id"], "a mask's own pixels are the map: field == input"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_is_never_walked_backwards(authenticated_context: HttpContext):
    """An object is a set of pixels, so the reverse asks for a point where there is a set.

    The existing `_INVERTIBLE_KINDS` gate gives this for free, knowing nothing about
    segmentation -- which is the evidence that a dereference really is a field and not a
    kind wearing a field's clothes.
    """
    mask = await seed.create_array_dataset(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _objects_table(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    def build() -> models.Transformation:
        return models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=mask_system,
            output=models.CoordinateSystem.objects.get(pk=table["coordinateSystem"]["id"]),
            field=mask_system,
            input_axes=["y", "x"],
            output_axes=["i"],
            organization=authenticated_context.request.organization,
        )

    edge = await sync_to_async(build)()

    assert await sync_to_async(graph_logic.is_traversable)(edge) is True, "forwards, a pixel has exactly one object"
    assert await sync_to_async(graph_logic.is_reverse_traversable)(edge) is False, "backwards, an object is a set of pixels"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_edge_must_account_for_its_endpoints(authenticated_context: HttpContext):
    """output == (input - consumed) + produced. The axes it does not consume pass through.

    A timelapse mask (t,y,x) consuming (y,x) and producing `i` implies (t,i) -- `t` survives
    because the edge did not name it. Claiming to produce into a bare (i) is then a rank
    change nothing else would catch: a FIELD has no parameters for `assert_edge_rank` to
    measure, so without this branch the edge is written and the missing `t` is discovered
    by whoever reads it.
    """
    mask = await seed.create_array_dataset(
        authenticated_context,
        "timelapse labels",
        axes=[seed.axis("t", enums.AxisType.TIME), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        shapes=[[10, 64, 64]],
    )
    table = await _objects_table(authenticated_context)  # space is (i) alone
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": str(mask_system.pk),
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i"],
                },
            }
        },
    )
    assert result.errors, "(t,y,x) consuming (y,x) implies (t,i), not (i): the unconsumed t passes through"
    assert "pass through by name" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scalar_field_produces_exactly_one_axis(authenticated_context: HttpContext):
    """No value axis means scalar, and a scalar value is one coordinate.

    The elision is deliberate -- a mask is a plain (y,x) array and giving it a length-1
    COORDINATE axis to satisfy a schema would be a phantom dimension nothing stores -- so
    the rule it implies has to be enforced instead.
    """
    mask = await seed.create_array_dataset(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    def target() -> models.CoordinateSystem:
        system = models.CoordinateSystem.objects.create(name="two-axis target", organization=authenticated_context.request.organization)
        for index, name in enumerate(["i", "j"]):
            models.Axis.objects.create(coordinate_system=system, order=index, name=name, type=enums.AxisTypeChoices.INDEX.value)
        return system

    two_axis = await sync_to_async(target)()

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": str(two_axis.pk),
                "transform": {
                    "kind": "FIELD",
                    "field": str(mask_system.pk),
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i", "j"],
                },
            }
        },
    )
    assert result.errors, "a scalar array cannot produce two coordinates"
    assert "no value axis" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_metric_kind_is_refused_over_an_index_space(authenticated_context: HttpContext):
    """Object 3 x 2 = object 6 is not a wrong number, it is a meaningless one.

    ABLATION: this is exactly what the rank check waves through -- `scale: [2.0]` has one
    entry per axis, which is all `assert_edge_rank` ever asked. Admitting a non-metric space
    to the graph is what makes the question live, and this refusal is its whole cost.
    """
    table = await _objects_table(authenticated_context)
    other = await _objects_table(authenticated_context, name="other objects")

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": table["coordinateSystem"]["id"],
                "output": other["coordinateSystem"]["id"],
                "transform": {"kind": "SCALE", "scale": [2.0]},
            }
        },
    )
    assert result.errors, "an INDEX axis has no metric to scale"
    assert "no metric" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_requires_its_array_and_other_kinds_refuse_one(authenticated_context: HttpContext):
    """The map is the array. Without it the edge claims a correspondence it cannot produce."""
    mask = await seed.create_array_dataset(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _objects_table(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    missing = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i"],
                },
            }
        },
    )
    assert missing.errors, "a FIELD without its array is an edge with no map"
    assert "requires `field`" in str(missing.errors[0])

    # And the converse: a kind whose map is a formula has no business carrying an array.
    spurious = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": str(mask_system.pk),
                "transform": {"kind": "IDENTITY", "field": str(mask_system.pk)},
            }
        },
    )
    assert spurious.errors, "an IDENTITY's map is in its parameters, not in an array"
    assert "does not read `field`" in str(spurious.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_composite_key_orders_time_before_index(authenticated_context: HttpContext):
    """`i` is per-frame, so the key is (t, i) -- and the table gets that order either way.

    `(t, i)` is the order the key is written in, and it is now simply stored -- a table's axes
    are no longer held to the RFC-5 type ordering at all. Worth pinning: the pairing is what
    lets a timelapse mask consume (y,x) and pass t through, and it does not depend on where the
    TIME axis sits, because `resolve_render_axes` finds it by a type scan.

    This used to assert that `(i, t)` was *refused*. It is not any more, and the change is
    deliberate: an array's axis order is its zarr's dimension order, so declaring it wrongly
    describes different bytes, but a table column's position is arbitrary and refusing one
    for it protected nothing -- `x, y, t` was refused while `t, x, y` was accepted and the
    two derived identical render axes. See item 14 of the proposals doc. Axis order is the
    axis-typed columns in file order now, so "either way" here means: whichever order the
    file writes the two columns in, the table takes it -- no type-order gate.
    """
    orders = (("i-then-t", [("i", "INDEX"), ("t", "TIME")]), ("t-then-i", [("t", "TIME"), ("i", "INDEX")]))
    for label, declared in orders:
        store = await _parquet(authenticated_context, f"per-frame-{label}", [(name, "BIGINT") for name, _ in declared])
        result = await schema.execute(
            CREATE_TABLE,
            context_value=authenticated_context,
            variable_values={"input": {
                "name": label,
                "data": str(store.pk),
                "columns": [{"name": name, "dtype": "BIGINT", "axisType": axis_type} for name, axis_type in declared],
            }},
        )
        assert not result.errors, result.errors
        axes = [a["name"] for a in result.data["createTableDataset"]["coordinateSystem"]["axes"]]
        assert axes == [name for name, _ in declared], f"{label} put the axes in {axes}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dereferencing_a_mask_does_not_pin_it_forever(authenticated_context: HttpContext):
    """Keying a table off a mask must not make the mask undeletable.

    `field` is PROTECT, which is right for a *separate* array: deleting a warp field would
    take a registration nobody named. But a self-dereference is a fact ABOUT the mask, and
    `input`'s CASCADE already removes it. Written as a real self-FK, PROTECT wins that race
    and the headline feature silently makes its own subject permanent.

    Under residence the CASCADE lives one level down: deleting the mask *dataset* no longer
    removes its space, so the sequence is "the data moves out, then the space goes" -- and
    the edge goes with the space, because `Transformation.input` still cascades.

    ABLATION: store `field=input_system` instead of null in `build_registration_edge` and
    deleting the space raises ProtectedError on its own field FK -- the self-PROTECT
    deadlock the null-means-self convention exists to avoid.
    """
    mask = await seed.create_array_dataset(authenticated_context, "labels", axes=seed.YX_AXES, shapes=[[64, 64]])
    table = await _objects_table(authenticated_context)
    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(mask_system.pk),
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": str(mask_system.pk),
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i"],
                },
            }
        },
    )
    assert not result.errors, result.errors
    edge_id = result.data["createTransformation"]["id"]

    # Stored as null -- the input is its own field, by definition...
    stored = await sync_to_async(lambda: models.Transformation.objects.get(pk=edge_id))()
    assert stored.field_id is None, "a self-dereference owns no field FK, exactly as a level-0 DataArray owns no system"
    # ...and read back as the input, so the client never sees the convention.
    assert await sync_to_async(lambda: stored.effective_field.pk)() == mask_system.pk

    # The dataset moves out first: its space is PROTECTed while it lives there.
    await sync_to_async(mask.delete)()
    await sync_to_async(mask_system.delete)()
    assert not await sync_to_async(models.Transformation.objects.filter(pk=edge_id).exists)(), "the dereference is a fact about the mask's space, so it goes with it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_separate_field_array_is_protected_from_deletion(authenticated_context: HttpContext):
    """The fence: PROTECT still does its job for an array that is NOT its edge's input.

    Deleting a warp field would leave a registration claiming a map it cannot produce --
    something the caller never named. Refused, per the same rule that refuses cascading a
    shared space in use.
    """
    warped = await seed.create_array_dataset(authenticated_context, "Warped", axes=seed.YX_AXES, shapes=[[64, 64]])
    warp = await seed.create_array_dataset(authenticated_context, "Warp field", axes=seed.YX_AXES, shapes=[[64, 64]])
    warped_system = await sync_to_async(lambda: warped.intrinsic_coordinate_system)()
    warp_system = await sync_to_async(lambda: warp.intrinsic_coordinate_system)()

    def build() -> None:
        atlas = models.CoordinateSystem.objects.create(name="Atlas", organization=authenticated_context.request.organization)
        for index, name in enumerate(["y", "x"]):
            models.Axis.objects.create(coordinate_system=atlas, order=index, name=name, type=enums.AxisTypeChoices.SPACE.value)
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.FIELD.value,
            input=atlas,
            output=warped_system,
            field=warp_system,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(build)()

    from django.db.models import ProtectedError

    # The warp field's *space* is what the edge points at, and it is protected -- deleting it
    # would leave a registration claiming a map it cannot produce. The dataset may move out
    # (residence, RFC-9); the frame its values were expressed in may not go with it.
    await sync_to_async(warp.delete)()
    with pytest.raises(ProtectedError):
        await sync_to_async(warp_system.delete)()


CREATE_ADATASET = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id intrinsicSystem { id } }
}
"""

CREATE_LENS = """
mutation Create($input: CreateLensInput!) {
  createLens(input: $input) { id }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_documented_sequence_runs_end_to_end(authenticated_context: HttpContext):
    """The exact call sequence in docs/field-transforms-api.md, through the real mutations.

    A doc that names a field the schema does not have is worse than no doc: it reads as
    verified. This runs it -- stack, mask, table, dereference -- so the sequence cannot rot
    into fiction without a red test.
    """

    async def _zarr(key: str, shape: list[int]) -> models.ZarrStore:
        return await models.ZarrStore.objects.acreate(
            organization=authenticated_context.request.organization,
            key=key,
            bucket="zarr",
            shape=shape,
            chunks=shape,
            version="3",
            dtype="uint8",
            populated=True,
        )

    stack_store = await _zarr("raw", [3, 512, 512])
    mask_store = await _zarr("nuclei-labels", [512, 512])

    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        # 1. The stack: c,y,x, in canonical order.
        stack = await schema.execute(
            CREATE_ADATASET,
            context_value=authenticated_context,
            variable_values={
                "input": {
                    "name": "raw",
                    "data": str(stack_store.id),
                    "scales": [],
                    "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                }
            },
        )
        assert not stack.errors, stack.errors

        # 1b. A lens over it: a derivation names the lens it was computed from, not the dataset.
        lens = await schema.execute(
            CREATE_LENS,
            context_value=authenticated_context,
            variable_values={"input": {"dataset": stack.data["createArrayDataset"]["id"], "slices": []}},
        )
        assert not lens.errors, lens.errors

        # 2. The mask: BY_DIMENSION drops c, CATEGORIZED says the values became labels.
        mask = await schema.execute(
            CREATE_ADATASET,
            context_value=authenticated_context,
            variable_values={
                "input": {
                    "name": "nuclei labels",
                    "data": str(mask_store.id),
                    "scales": [],
                    "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                    "derivedFrom": [{"kind": "LENS", "lens": lens.data["createLens"]["id"], "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}, "valueRelation": "CATEGORIZED"}],
                }
            },
        )
        assert not mask.errors, mask.errors

    mask_system = mask.data["createArrayDataset"]["intrinsicSystem"]["id"]

    # 3. The table: its axis IS its id column.
    table = await _objects_table(authenticated_context)

    # 4. The dereference: the mask's own pixels are the map.
    edge = await schema.execute(
        CREATE_TRANSFORM,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": mask_system,
                "output": table["coordinateSystem"]["id"],
                "transform": {
                    "kind": "FIELD",
                    "field": mask_system,
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["i"],
                },
                "validity": "VALIDATED",
                "name": "nuclei labels -> morphology",
            }
        },
    )
    assert not edge.errors, edge.errors
    assert edge.data["createTransformation"]["field"]["id"] == mask_system

"""A long-form table: a CHANNEL coordinate, and what each column varies over.

A per-channel measurement can be written wide, one column per channel (`mean_dapi`,
`mean_gfp`), or long, one row per (object, channel) with the channel a coordinate column:
`(cell, c, volume, mean)`. The long form puts the channel in the table's frame, as a CHANNEL
axis an image's `c` can be identified with. A key from a mask supplies the object; the channel
is the reader's to pick, or passes through from a source that has a `c` of its own. Each data
column says which channel axes its value changes along (`variesOver`), so a per-cell `volume`
is counted once per cell, not once per channel.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums
from mikro_server.schema import schema
from tests import seed
from tests.test_keyed_by import PLANS, _create, _mask

TABLE = """
query T($id: ID!) {
  tableDataset(id: $id) {
    coordinateSystem { axes { name type unit } }
    columns { name variesOver }
    keyedBy { produces passesThrough }
  }
}
"""

LONG_FORM = [
    {"name": "cell", "dtype": "BIGINT", "axisType": "INDEX"},
    {"name": "c", "dtype": "BIGINT", "axisType": "CHANNEL"},
    {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE", "variesOver": []},
    {"name": "mean", "dtype": "DOUBLE", "role": "ATTRIBUTE", "variesOver": ["c"]},
    {"name": "kind", "dtype": "VARCHAR", "role": "LABEL"},
]


async def _ok(ctx: HttpContext, document: str, variables: dict) -> dict:
    result = await schema.execute(document, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    return result.data


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> dict:
    result = await _create(ctx, name, columns, **extra)
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_channel_is_a_coordinate_and_columns_say_what_varies_along_it(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await _table(ctx, "per channel", LONG_FORM)
    read = (await _ok(ctx, TABLE, {"id": table["id"]}))["tableDataset"]
    assert [(axis["name"], axis["type"]) for axis in read["coordinateSystem"]["axes"]] == [("cell", "INDEX"), ("c", "CHANNEL")], "the frame; the values stay columns"
    assert {column["name"]: column["variesOver"] for column in read["columns"]} == {"cell": None, "c": None, "volume": [], "mean": ["c"], "kind": None}


#: A mask's frames are 2D: a per-object, per-channel table is keyed by the object alone.
YX_AXES = [seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
CYX_AXES = [seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mask_keys_a_long_form_table_by_the_object_alone(authenticated_context: HttpContext):
    """`(y, x)` keys `(cell, c)` by `cell`: no source supplies the channel, and no plan pretends one id finds one row."""
    ctx = authenticated_context
    mask = await _mask(ctx, axes=YX_AXES, shapes=[[64, 64]])
    table = await _table(ctx, "per channel", LONG_FORM, identified_by={"cell": [{"kind": "DATASET", "dataset": str(mask.pk)}]})
    keyed = (await _ok(ctx, TABLE, {"id": table["id"]}))["tableDataset"]["keyedBy"]
    assert keyed == [{"produces": ["cell"], "passesThrough": []}]

    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    plans = (await _ok(ctx, PLANS, {"system": str(mask_system.pk)}))["attributePlans"]
    assert plans == [], "an id alone finds one row per channel: a slice, which no plan states yet"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mask_with_channels_passes_the_channel_through(authenticated_context: HttpContext):
    """A `(c, y, x)` mask shares `c` by name: the key passes it through, and the plan binds it like `t`."""
    ctx = authenticated_context
    mask = await _mask(ctx, axes=CYX_AXES, shapes=[[2, 64, 64]])
    table = await _table(ctx, "per channel", LONG_FORM, identified_by={"cell": [{"kind": "DATASET", "dataset": str(mask.pk)}]})
    keyed = (await _ok(ctx, TABLE, {"id": table["id"]}))["tableDataset"]["keyedBy"]
    assert keyed == [{"produces": ["cell"], "passesThrough": ["c"]}]

    mask_system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    (plan,) = (await _ok(ctx, PLANS, {"system": str(mask_system.pk)}))["attributePlans"]
    assert [(key["axis"], key["column"]["name"]) for key in plan["hops"][0]["lookup"]["keyColumns"]] == [("cell", "cell"), ("c", "c")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_long_form_declarations_are_checked(authenticated_context: HttpContext):
    ctx = authenticated_context

    unit = await _create(ctx, "bad unit", [LONG_FORM[0], {**LONG_FORM[1], "unit": "nanometer"}, LONG_FORM[2]])
    assert unit.errors and "CHANNEL axis" in str(unit.errors[0]) and "no unit" in str(unit.errors[0])

    on_frame = await _create(ctx, "bad frame", [{**LONG_FORM[0], "variesOver": ["c"]}, LONG_FORM[1], LONG_FORM[2]])
    assert on_frame.errors and "coordinate column" in str(on_frame.errors[0])

    unknown = await _create(ctx, "unknown", [LONG_FORM[0], LONG_FORM[1], {**LONG_FORM[3], "variesOver": ["cell"]}])
    assert unknown.errors and "not a CHANNEL coordinate" in str(unknown.errors[0])

    twice = await _create(ctx, "twice", [LONG_FORM[0], LONG_FORM[1], {**LONG_FORM[3], "variesOver": ["c", "c"]}])
    assert twice.errors and "more than once" in str(twice.errors[0])

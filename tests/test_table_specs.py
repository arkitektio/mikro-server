"""A table states what it structurally is: its spec, materialized from its declared columns.

The array spec's sibling, with the one difference that decides everything about it: an
array's spec counts an axis only when it has more than one position, and a table's cannot,
because the server never scans the rows. It is read off the declaration alone.

The derivation is oracle-tested without a database; the rest holds the three places it is
read to it -- the field, the filter, and the migration that gave it to the tables written
before it was stored.
"""


import pytest
from kante.context import HttpContext

from core import models
from core.enums import TableDatasetSpec as Spec
from core.logic.coords import ColumnSpec, specs_for_columns
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id spec }
}
"""

FILTER = """
query Tables($spec: [TableDatasetSpec!]!) {
  tableDatasets(filters: { spec: $spec }) { id }
}
"""


def _space(dtype: str = "DOUBLE") -> ColumnSpec:
    return ColumnSpec(name="x", role="COORDINATE", axis_type="SPACE", dtype=dtype)


def _time() -> ColumnSpec:
    return ColumnSpec(name="t", role="COORDINATE", axis_type="TIME", dtype="DOUBLE")


def _index(**facts: object) -> ColumnSpec:
    return ColumnSpec(name="i", role="COORDINATE", axis_type="INDEX", dtype="BIGINT", **facts)


def _data(role: str = "ATTRIBUTE", dtype: str = "DOUBLE", **facts: object) -> ColumnSpec:
    return ColumnSpec(name="v", role=role, axis_type=None, dtype=dtype, **facts)


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        ([], [Spec.RECORDS]),
        ([_data(), _data()], [Spec.RECORDS]),
        ([_space(), _data(role="LABEL", dtype="VARCHAR")], [Spec.POINTS_1D]),
        ([_space(), _space()], [Spec.POINTS_2D]),
        ([_space(), _space(), _space()], [Spec.POINTS_3D]),
        ([_space()] * 5, [Spec.POINTS_ND]),
        ([_time(), _space(), _space(), _data(role="TRACK_ID", dtype="BIGINT")], [Spec.POINTS_2D, Spec.TIMED, Spec.TRACKED]),
        ([_space(), _space(), _space(), _data(role="GROUP_ID", dtype="BIGINT")], [Spec.POINTS_3D, Spec.GROUPED]),
        ([_index(references=True), _data(role="LABEL", dtype="VARCHAR")], [Spec.RECORDS, Spec.KEYED]),
        ([_data(role="ID", dtype="BIGINT", references=True)], [Spec.RECORDS, Spec.KEYED]),
        ([_index(), _index(node_references=7)], [Spec.RECORDS, Spec.NODE_TABLE]),
        ([_index(), _index(node_references=7), _index(node_references=7)], [Spec.RECORDS, Spec.EDGE_TABLE]),
        ([_index(), _index(node_references=7), _index(node_references=7), _index(node_references=9)], [Spec.RECORDS, Spec.NODE_TABLE, Spec.EDGE_TABLE]),
    ],
)
def test_the_spec_is_one_placement_member_then_a_modifier_per_declared_fact(columns: list[ColumnSpec], expected: list[Spec]) -> None:
    assert specs_for_columns(columns) == expected


@pytest.mark.parametrize(
    ("columns", "is_series"),
    [
        ([_time(), _data()], True),
        ([_space(), _data(dtype="DECIMAL(18,3)")], True),
        ([_time(), _index(), _data()], True),
        # Nothing to draw: a position with no value, or a value that is not a number.
        ([_time()], False),
        ([_time(), _data(dtype="VARCHAR")], False),
        ([_time(), _data(role="LABEL")], False),
        # Nothing to draw it against, or two things: which one a chart lays along is not the table's to say.
        ([_data()], False),
        ([_index(), _data()], False),
        ([_time(), _space(), _data()], False),
        ([_space(), _space(), _data()], False),
    ],
)
def test_a_series_is_one_measured_coordinate_and_a_numeric_value(columns: list[ColumnSpec], is_series: bool) -> None:
    assert (Spec.SERIES in specs_for_columns(columns)) is is_series


async def _create(ctx: HttpContext, name: str, columns: list[dict]) -> dict:
    result = await schema.execute(CREATE, context_value=ctx, variable_values={"input": await seed.table_input(ctx, name, columns)})
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


TRACKS = [
    {"name": "t", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "TIME", "unit": "second"},
    {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "track", "dtype": "BIGINT", "role": "TRACK_ID"},
    {"name": "photons", "dtype": "DOUBLE"},
]
POINTS = [
    {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
]
READOUT = [
    {"name": "t", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "TIME", "unit": "second"},
    {"name": "intensity", "dtype": "DOUBLE"},
]
MEASUREMENTS = [{"name": "area", "dtype": "DOUBLE"}, {"name": "label", "dtype": "VARCHAR", "role": "LABEL"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_answers_with_the_spec_its_columns_declare(authenticated_context: HttpContext):
    """The mutation answers with it, and the row holds the raw values in the same order."""
    tracks = await _create(authenticated_context, "tracks", TRACKS)
    assert tracks["spec"] == ["POINTS_2D", "TIMED", "TRACKED"]
    assert (await models.TableDataset.objects.aget(pk=tracks["id"])).stored_spec == ["POINTS_2D", "TIMED", "TRACKED"]

    assert (await _create(authenticated_context, "readout", READOUT))["spec"] == ["RECORDS", "TIMED", "SERIES"]
    assert (await _create(authenticated_context, "measurements", MEASUREMENTS))["spec"] == ["RECORDS"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_keyed_to_another_says_so(authenticated_context: HttpContext):
    objects = await _create(authenticated_context, "objects", [{"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "area", "dtype": "DOUBLE"}])
    assert objects["spec"] == ["RECORDS"], "being referenced is a fact about the other table"

    result = await schema.execute(
        CREATE,
        context_value=authenticated_context,
        variable_values={
            "input": await seed.table_input(
                authenticated_context,
                "spots",
                [*POINTS, {"name": "cell", "dtype": "BIGINT", "role": "ID"}],
                identified_by={"cell": [{"kind": "TABLE", "table": objects["id"]}]},
            )
        },
    )
    assert not result.errors, result.errors
    assert result.data["createTableDataset"]["spec"] == ["POINTS_2D", "KEYED"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_spec_filter_keeps_the_tables_that_satisfy_every_member(authenticated_context: HttpContext):
    tracks = await _create(authenticated_context, "tracks", TRACKS)
    points = await _create(authenticated_context, "points", POINTS)
    readout = await _create(authenticated_context, "readout", READOUT)

    async def matching(*spec: str) -> set[str]:
        result = await schema.execute(FILTER, context_value=authenticated_context, variable_values={"spec": list(spec)})
        assert not result.errors, result.errors
        return {table["id"] for table in result.data["tableDatasets"]}

    assert await matching("POINTS_2D") == {tracks["id"], points["id"]}
    assert await matching("POINTS_2D", "TRACKED") == {tracks["id"]}
    assert await matching("TIMED") == {tracks["id"], readout["id"]}
    assert await matching("SERIES") == {readout["id"]}
    assert await matching("POINTS_2D", "RECORDS") == set(), "a table has one placement member"
    assert await matching() == {tracks["id"], points["id"], readout["id"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stored_spec_this_build_does_not_know_is_left_out(authenticated_context: HttpContext):
    """The enum grows, and a rolled-back release is started on rows a newer one wrote."""
    points = await _create(authenticated_context, "points", POINTS)
    await models.TableDataset.objects.filter(pk=points["id"]).aupdate(stored_spec=["POINTS_2D", "NOT_YET_NAMED"])

    result = await schema.execute("query T($id: ID!) { tableDataset(id: $id) { spec } }", context_value=authenticated_context, variable_values={"id": points["id"]})
    assert not result.errors, result.errors
    assert result.data["tableDataset"]["spec"] == ["POINTS_2D"]

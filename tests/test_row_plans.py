"""`rowPlan(table:)`: what a picked row reaches -- a point of a chart of values.

A chart draws rows, and a picked point holds its row's id, so the plan has no sample: it starts
at the table, bound by its INDEX column, and walks the schema from there as an attribute plan's
chain does. Two facts only a row's plan takes:

- **REVERSE** -- a `Column.references` read backwards: a cluster row reaches every member row
  whose column names it (MANY);
- **SAME_ROWS** -- the edge between two tables' frames: a measurement row reaches its PCA row,
  and through a relabel (`cell_id = cell + 1`) the plan says how the id maps.

What a row IS stays where it was declared, on `keyedBy`, which now names the structure a keyed
row resolves to (`@mikro/meshobject`).
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from core.logic import join_walk
from mikro_server.schema import schema
from tests.test_charts import REGISTER
from tests.test_keyed_by import SHAPE_COLUMNS, ZYX_MESH_AXES, _create, _mesh_collection

ROW_PLAN = """
query R($table: ID!, $depth: Int!) {
  rowPlan(table: $table, maxJoinDepth: $depth) {
    table { id }
    key { axis column { name } }
    hops {
      index parent cardinality
      via { column { name } direction }
      table { id name keyedBy { structure } }
      lookup { keyColumns { axis column { name } } attributes { name } keyMap { scale offset } }
    }
  }
}
"""


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> dict:
    result = await _create(ctx, name, columns, **extra)
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


async def _plan(ctx: HttpContext, table: str, depth: int = 1) -> dict:
    result = await schema.execute(ROW_PLAN, context_value=ctx, variable_values={"table": table, "depth": depth})
    assert not result.errors, result.errors
    return result.data["rowPlan"]


async def _refused(ctx: HttpContext, table: str) -> str:
    result = await schema.execute(ROW_PLAN, context_value=ctx, variable_values={"table": table, "depth": 1})
    assert result.errors, result.data
    return str(result.errors[0])


CLUSTERS = [
    {"name": "cluster_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "size", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_row_is_its_own_landing(authenticated_context: HttpContext):
    ctx = authenticated_context
    clusters = await _table(ctx, "clusters", CLUSTERS)
    plan = await _plan(ctx, clusters["id"], depth=0)
    assert plan["key"] == {"axis": "cluster_id", "column": {"name": "cluster_id"}}
    (landing,) = plan["hops"]
    assert (landing["index"], landing["parent"], landing["table"]["id"]) == (0, None, clusters["id"])
    assert landing["lookup"]["keyColumns"] == [{"axis": "cluster_id", "column": {"name": "cluster_id"}}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_cluster_row_reaches_its_members(authenticated_context: HttpContext):
    """REVERSE: every member row whose `cluster` column names the picked cluster, with their own ids."""
    ctx = authenticated_context
    clusters = await _table(ctx, "clusters", CLUSTERS)
    members = await _table(
        ctx,
        "members",
        [
            {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "cluster", "dtype": "BIGINT", "role": "GROUP_ID", "references": clusters["id"]},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    plan = await _plan(ctx, clusters["id"])
    (reverse,) = [hop for hop in plan["hops"] if hop["via"] and hop["via"]["direction"] == "REVERSE"]
    assert reverse["table"]["id"] == members["id"]
    assert (reverse["cardinality"], reverse["parent"]) == ("MANY", 0)
    assert reverse["via"]["column"] == {"name": "cluster_id"}, "the parent's INDEX is what binds"
    assert reverse["lookup"]["keyColumns"] == [{"axis": "cluster_id", "column": {"name": "cluster"}}], "into the child's referencing column"
    assert "i" in [attribute["name"] for attribute in reverse["lookup"]["attributes"]], "the members' own ids come back, to say what each row is"

    # From the member's side the same reference is FORWARD, as it always was.
    forward = await _plan(ctx, members["id"])
    assert [hop["via"]["direction"] for hop in forward["hops"][1:]] == ["FORWARD"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_scenes_walk_takes_no_reverse_hops(authenticated_context: HttpContext):
    """The picker and the scene's plans walk forward only: a stored `joinPath` cannot name a reverse step."""
    ctx = authenticated_context
    clusters = await _table(ctx, "clusters", CLUSTERS)
    await _table(ctx, "members", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "cluster", "dtype": "BIGINT", "role": "GROUP_ID", "references": clusters["id"]}])

    def walk() -> list:
        table = models.TableDataset.objects.get(pk=clusters["id"])
        return join_walk.walk_joins([join_walk.JoinRoot(table=table)], table.organization, max_join_depth=2)[0]

    assert await sync_to_async(walk)() == []


MEASUREMENTS = [
    {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_derived_table_holds_the_same_rows(authenticated_context: HttpContext):
    """SAME_ROWS, both ways: a PCA derived row for row reaches its measurement row, and back."""
    ctx = authenticated_context
    measurements = await _table(ctx, "measurements", MEASUREMENTS)
    pca = await _table(
        ctx,
        "pca",
        [{"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "pc1", "dtype": "DOUBLE", "role": "ATTRIBUTE"}],
        derivedFrom=[{"kind": "TABLE_DATASET", "tableDataset": measurements["id"], "transform": {"kind": "IDENTITY"}, "valueRelation": "TRANSFORMED"}],
    )

    for source, target in ((measurements, pca), (pca, measurements)):
        plan = await _plan(ctx, source["id"])
        (same,) = [hop for hop in plan["hops"] if hop["via"] and hop["via"]["direction"] == "SAME_ROWS"]
        assert same["table"]["id"] == target["id"]
        assert same["cardinality"] == "ONE"
        assert same["lookup"]["keyColumns"] == [{"axis": "cell_id", "column": {"name": "cell_id"}}]
        assert same["lookup"]["keyMap"] is None, "an IDENTITY keeps the id"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_relabel_says_how_the_id_maps(authenticated_context: HttpContext):
    """`cell_id = cell + 1` between two frames: from a `cell` row the other row is `held + 1`, and back `held - 1`."""
    ctx = authenticated_context
    measurements = await _table(ctx, "measurements", MEASUREMENTS)
    zero_based = await _table(ctx, "zero based", [{"name": "cell", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "score", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    tables = {table.pk: table for table in await sync_to_async(lambda: list(models.TableDataset.objects.filter(pk__in=[measurements["id"], zero_based["id"]])))()}
    result = await schema.execute(
        REGISTER,
        context_value=ctx,
        variable_values={
            "input": {
                "input": str(tables[int(zero_based["id"])].coordinate_system_id),
                "output": str(tables[int(measurements["id"])].coordinate_system_id),
                "transform": {"kind": "BY_DIMENSION", "parts": [{"kind": "AFFINE", "inputAxes": ["cell"], "outputAxes": ["cell_id"], "affine": [[1.0, 1.0]]}]},
            }
        },
    )
    assert not result.errors, result.errors

    (forward,) = [hop for hop in (await _plan(ctx, zero_based["id"]))["hops"] if hop["via"] and hop["via"]["direction"] == "SAME_ROWS"]
    assert forward["lookup"]["keyMap"] == {"scale": 1.0, "offset": 1.0}
    (back,) = [hop for hop in (await _plan(ctx, measurements["id"]))["hops"] if hop["via"] and hop["via"]["direction"] == "SAME_ROWS"]
    assert back["lookup"]["keyMap"] == {"scale": 1.0, "offset": -1.0}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_keyed_row_names_its_structure(authenticated_context: HttpContext):
    """A mesh collection's rows are mesh objects (`<collection>:<object>`); a mask's labels are nothing openable yet."""
    ctx = authenticated_context
    collection = await _mesh_collection(ctx, ZYX_MESH_AXES)
    shapes = await _table(ctx, "shape stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})
    plan = await _plan(ctx, shapes["id"], depth=0)
    assert plan["hops"][0]["table"]["keyedBy"] == [{"structure": "@mikro/meshobject"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_without_an_index_has_no_row_plan(authenticated_context: HttpContext):
    """A synthetic `object` frame has no column a row's id could be looked up by: refused, saying how to fix it."""
    ctx = authenticated_context
    table = await _table(ctx, "unkeyed", [{"name": "label", "dtype": "BIGINT", "role": "ID"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    assert "INDEX coordinate column" in await _refused(ctx, table["id"])

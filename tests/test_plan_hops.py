"""A plan's hops through matrices: mask -> matrix -> table -> matrix -> table, each bound from the last.

The table-to-table hop is asserted beside the other plan tests (`tests/test_attribute_plans.py`);
this file is the sparse side of the walk (`core/logic/join_walk.py`), where the two things a
matrix changes show up: a slice returns a *set* of positions, so the hop out of it binds a list
(`cardinality: MANY`), and a table can hop *into* a matrix only along an axis a layout indexes.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import plan_sql
from mikro_server.schema import schema
from tests import seed

PLANS = """
query Plans($system: ID!, $maxJoinDepth: Int) {
  attributePlans(system: $system, maxJoinDepth: $maxJoinDepth) {
    sample { produces }
    hops {
      index parent cardinality
      via { column { name } axis }
      table { name }
      sparseDataset { name }
      joinPath { column { name } }
      lookup { kind keyColumns { axis column { name } } attributes { name } sparseArray { path } keyAxis keyHeld valueAxes }
    }
  }
}
"""

CREATE_SPARSE = """
mutation Create($input: CreateSparseDatasetInput!) {
  createSparseDataset(input: $input) { id coordinateSystem { id } }
}
"""

YX_AXES = [seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]


async def _mask(ctx: HttpContext, name: str = "cell labels") -> models.ArrayDataset:
    """A label mask whose level-0 array has a zarr store, so a plan has something to name."""
    dataset = await seed.create_array_dataset(ctx, name, axes=YX_AXES, shapes=[[64, 64]])

    def attach() -> None:
        store = models.ZarrStore.objects.create(path=f"s3://zarr/{name}", bucket="zarr", key=name.replace(" ", "-"), organization=ctx.request.organization)
        array = dataset.data_arrays.get(level=0)
        array.store = store
        array.save()

    await sync_to_async(attach)()
    return dataset


async def _table(ctx: HttpContext, name: str, key: str, *attributes: str) -> str:
    """A table one position identifies a row of: a single INDEX column, and some attributes."""
    columns = [{"name": key, "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}] + [{"name": attribute, "dtype": "VARCHAR", "role": "LABEL"} for attribute in attributes]
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns)},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _matrix(ctx: HttpContext, name: str, axes: list[dict], *, shape: list[int], layouts: tuple[int, ...]) -> dict:
    store = await seed.create_sparse_store(ctx, f"{name}-store", axes=layouts, shape=shape)
    result = await schema.execute(CREATE_SPARSE, context_value=ctx, variable_values={"input": {"name": name, "store": str(store.pk), "axes": axes}})
    assert not result.errors, result.errors
    return result.data["createSparseDataset"]


def _by_table(table: str) -> list[dict]:
    return [{"kind": "TABLE", "table": table}]


async def _expression(ctx: HttpContext, mask: models.ArrayDataset, genes: str, *, layouts: tuple[int, ...] = (0, 1)) -> dict:
    """cell x gene, keyed by the mask on `cell`, `gene` identified by the genes table. Shape is (gene, cell)."""
    return await _matrix(
        ctx,
        "expression",
        [{"name": "gene", "identifiedBy": _by_table(genes)}, {"name": "cell", "identifiedBy": [{"kind": "DATASET", "dataset": str(mask.pk)}]}],
        shape=[40, 12],
        layouts=layouts,
    )


async def _plans(ctx: HttpContext, mask: models.ArrayDataset, **variables: object) -> list[dict]:
    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=ctx, variable_values={"system": str(system.pk), **variables})
    assert not result.errors, result.errors
    return result.data["attributePlans"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_landing_hops_into_the_table_its_value_axis_references(authenticated_context: HttpContext):
    """The slice comes back as positions along `gene`; the hop binds them, plural, in the genes table."""
    mask = await _mask(authenticated_context)
    genes = await _table(authenticated_context, "genes", "gene_id", "symbol")
    await _expression(authenticated_context, mask, genes)

    (plan,) = await _plans(authenticated_context, mask)
    landing, hop = plan["hops"]

    assert landing["lookup"]["kind"] == "SPARSE" and landing["lookup"]["keyAxis"] == "cell" and landing["lookup"]["keyHeld"] == "cell"
    assert landing["via"] is None, "the landing's crossing is the plan's edge"

    assert hop["parent"] == 0 and hop["index"] == 1
    assert hop["cardinality"] == "MANY", "a slice is every position along the value axis, so the hop binds a set"
    assert hop["via"] == {"column": None, "axis": "gene"}, "crossed the matrix axis the genes table identifies"
    assert hop["table"]["name"] == "genes"
    assert hop["lookup"]["keyColumns"] == [{"axis": "gene", "column": {"name": "gene_id"}}], "held under the axis' name, bound to the target's INDEX column"
    assert hop["joinPath"] == [], "no picker entry can name a chain that crossed a matrix"
    assert plan_sql.build_lookup_sql(hop["lookup"], cardinality=hop["cardinality"]) == 'SELECT "gene_id", "symbol" FROM read_parquet(?) WHERE "gene_id" IN (SELECT unnest(?))'


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_hop_into_a_matrix_without_the_layout_indexing_the_hop_axis(authenticated_context: HttpContext):
    """A table hops into a matrix only along an axis a layout compresses; from the other layout it is a scan."""
    mask = await _mask(authenticated_context)
    genes = await _table(authenticated_context, "genes", "gene_id", "symbol")
    pathways = await _table(authenticated_context, "pathways", "pathway_id", "label")
    await _expression(authenticated_context, mask, genes)
    membership_axes = [{"name": "gene", "identifiedBy": _by_table(genes)}, {"name": "pathway", "identifiedBy": _by_table(pathways)}]
    # Indexed on axis 1 (pathway) only: a gene id cannot select one contiguous range here.
    await _matrix(authenticated_context, "membership", membership_axes, shape=[40, 7], layouts=(1,))

    (plan,) = await _plans(authenticated_context, mask, maxJoinDepth=4)
    assert [hop["table"]["name"] if hop["table"] else hop["sparseDataset"]["name"] for hop in plan["hops"]] == ["expression", "genes"], "the membership matrix is not offered from its unindexed axis"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plan_walks_mask_to_matrix_to_table_to_matrix_to_table(authenticated_context: HttpContext):
    """The whole chain: pixel -> cell -> its genes -> the pathways those genes are in -> their names.

    Four hops, each bound from the one before under the name its `via` states. The landing matrix
    is never re-entered: it is in the visited set from the start, or `genes -> expression` would
    reappear one level down as a hop back to where the worker already stands.
    """
    mask = await _mask(authenticated_context)
    genes = await _table(authenticated_context, "genes", "gene_id", "symbol")
    pathways = await _table(authenticated_context, "pathways", "pathway_id", "label")
    await _expression(authenticated_context, mask, genes)
    membership_axes = [{"name": "gene", "identifiedBy": _by_table(genes)}, {"name": "pathway", "identifiedBy": _by_table(pathways)}]
    await _matrix(authenticated_context, "membership", membership_axes, shape=[40, 7], layouts=(0,))

    (plan,) = await _plans(authenticated_context, mask, maxJoinDepth=3)
    hops = plan["hops"]
    assert [hop["parent"] for hop in hops] == [None, 0, 1, 2], "a chain: each hop binds from the one before"
    assert [hop["cardinality"] for hop in hops] == ["ONE", "MANY", "MANY", "MANY"], "once a slice has been read the worker holds sets"

    landing, to_genes, to_membership, to_pathways = hops
    assert landing["sparseDataset"]["name"] == "expression"

    assert to_genes["via"] == {"column": None, "axis": "gene"} and to_genes["table"]["name"] == "genes"

    assert to_membership["via"] == {"column": {"name": "gene_id"}, "axis": "gene"}, "a table enters a matrix along the axis it identifies, holding its INDEX column"
    assert to_membership["sparseDataset"]["name"] == "membership"
    assert to_membership["lookup"]["kind"] == "SPARSE"
    assert to_membership["lookup"]["keyAxis"] == "gene" and to_membership["lookup"]["keyHeld"] == "gene_id", "bound to the axis, held under the parent row's column name"
    assert to_membership["lookup"]["sparseArray"]["path"] == "layouts/axis0", "the layout whose indptr indexes gene"
    assert to_membership["lookup"]["valueAxes"] == ["pathway"]

    assert to_pathways["via"] == {"column": None, "axis": "pathway"} and to_pathways["table"]["name"] == "pathways"
    assert to_pathways["lookup"]["keyColumns"] == [{"axis": "pathway", "column": {"name": "pathway_id"}}]

    shallower = await _plans(authenticated_context, mask, maxJoinDepth=1)
    assert len(shallower[0]["hops"]) == 2, "`maxJoinDepth` bounds the chain"
    bare = await _plans(authenticated_context, mask, maxJoinDepth=0)
    assert len(bare[0]["hops"]) == 1, "zero is the landing alone"

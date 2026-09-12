"""Per-node and per-edge table identification on network collections.

The shape under test is the one `write_key_edges`' own two-ids refusal points at: a row keyed
by the (object, node) pair is ONE produced axis -- the object axis, keyed by the collection --
plus axes the table identifies itself, here by `NETWORK_COLLECTION_NODES`. So the composite
key lands without touching either gate that refuses a two-produce edge, and the table becomes
a product space: dropped from every object-level walk (an object id alone cannot address its
rows) and reachable only through the network picker's own door, where the validator stamps
each entry's `target` from the table's shape.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import attribute_plans as attribute_plans_logic
from mikro_server.schema import schema
from tests import seed

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id columns { name nodeReferences { id } } }
}
"""

CREATE_LAYER = """
mutation Create($input: CreateNetworkLayerInput!) {
  createNetworkLayer(input: $input) {
    id
    colorBys { kind table column attribute target colormap min max label joinPath { table column } }
    filterBys { kind table column attribute target min max values exclude label }
    activeColorBy
    activeFilterBys
  }
}
"""

OPTIONS = """
query Options($collection: ID!) {
  networkColorByOptions(networkCollection: $collection) {
    graphAttribute
    target
    table { id }
    column { name }
  }
}
"""

XYZ_AXES = [{"name": "x", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "z", "type": "SPACE"}]

NODE_COLUMNS = [
    {"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "node_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "dist_um", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]

EDGE_COLUMNS = [
    {"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "source_node", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "target_node", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "length_um", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


def node_axes(collection_id: str, columns: list[dict]) -> dict[str, list[dict]]:
    """The (object, nodes...) identification shape: the object axis keys, the rest enumerate."""
    identified: dict[str, list[dict]] = {}
    for column in columns:
        if column.get("axisType") != "INDEX":
            continue
        if column["name"] == "object_id":
            identified[column["name"]] = [{"kind": "NETWORK_COLLECTION", "networkCollection": collection_id}]
        else:
            identified[column["name"]] = [{"kind": "NETWORK_COLLECTION_NODES", "networkCollection": collection_id}]
    return identified


async def _collection(ctx: HttpContext) -> models.NetworkCollection:
    store = await seed.create_konnektion_store(ctx)
    result = await schema.execute(
        "mutation Create($input: CreateNetworkCollectionInput!) { createNetworkCollection(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"version": "v1", "store": str(store.pk), "axes": XYZ_AXES}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.NetworkCollection.objects.select_related("store", "coordinate_system").get)(id=result.data["createNetworkCollection"]["id"])


async def _scene_for(ctx: HttpContext, collection: models.NetworkCollection) -> models.Scene:
    scene = await seed.create_scene(ctx, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind="IDENTITY",
            input=collection.coordinate_system,
            output=scene.world,
            params={},
            organization=ctx.request.organization,
        )

    await sync_to_async(register)()
    return scene


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object):
    await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{name}", bucket="parquet", key=name, organization=ctx.request.organization, populated=True)
    return await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, **extra)},
    )


async def _layer(ctx: HttpContext, scene, collection, **extra: object):
    return await schema.execute(
        CREATE_LAYER,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "networkCollection": str(collection.pk), **extra}},
    )


# --------------------------------------------------------------------------- #
# the shape: one produced axis, the rest identified
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_node_table_creates_with_one_object_edge(authenticated_context: HttpContext):
    """(object, node) lands as ONE FIELD edge producing the object axis alone.

    The two-ids refusal never fires because the node axis is one 'the table identifies
    itself' -- exactly the sparse pattern, on a new substrate.
    """
    collection = await _collection(authenticated_context)
    result = await _table(authenticated_context, "node-stats", NODE_COLUMNS, identified_by=node_axes(str(collection.pk), NODE_COLUMNS))
    assert not result.errors, result.errors

    by_name = {column["name"]: column for column in result.data["createTableDataset"]["columns"]}
    assert by_name["node_id"]["nodeReferences"] == {"id": str(collection.pk)}
    assert by_name["object_id"]["nodeReferences"] is None, "the object axis keys; it does not enumerate nodes"

    def edge_facts() -> tuple[int, list[str]]:
        edges = list(
            models.Transformation.objects.filter(
                input=collection.coordinate_system, kind=enums.TransformKindChoices.FIELD.value
            )
        )
        return len(edges), list(edges[0].output_axes or [])

    count, produced = await sync_to_async(edge_facts)()
    assert count == 1, "one edge, however many axes the table has"
    assert produced == ["object_id"], f"the edge supplies the one id a source supplies, got {produced}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_edge_table_is_emergent_from_shape(authenticated_context: HttpContext):
    """Two node axes over one collection are its edges' (source, target) -- no third kind."""
    collection = await _collection(authenticated_context)
    result = await _table(authenticated_context, "edge-stats", EDGE_COLUMNS, identified_by=node_axes(str(collection.pk), EDGE_COLUMNS))
    assert not result.errors, result.errors
    by_name = {column["name"]: column for column in result.data["createTableDataset"]["columns"]}
    assert by_name["source_node"]["nodeReferences"] == {"id": str(collection.pk)}
    assert by_name["target_node"]["nodeReferences"] == {"id": str(collection.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_shape_refusals(authenticated_context: HttpContext):
    """Every way the declaration can lie about the geometry, refused with its reason."""
    collection = await _collection(authenticated_context)
    cid = str(collection.pk)

    # An unscoped node axis: without the object axis a row's node is ambiguous.
    unscoped = await _table(
        authenticated_context,
        "unscoped",
        [NODE_COLUMNS[1], NODE_COLUMNS[2]],
        identified_by={"node_id": [{"kind": "NETWORK_COLLECTION_NODES", "networkCollection": cid}]},
    )
    assert unscoped.errors
    assert "no sibling axis is keyed" in str(unscoped.errors[0])

    # Three node axes: a hyperedge, which pairwise edges cannot address.
    three_columns = EDGE_COLUMNS[:3] + [
        {"name": "third_node", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        EDGE_COLUMNS[3],
    ]
    hyper = await _table(authenticated_context, "hyper", three_columns, identified_by=node_axes(cid, three_columns))
    assert hyper.errors
    assert "hyperedge" in str(hyper.errors[0])

    # A node identification on a SPACE axis: positions are not ids.
    space_columns = [
        {"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "xpos", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
    ]
    spaced = await _table(
        authenticated_context,
        "spaced",
        space_columns,
        identified_by={
                "object_id": [{"kind": "NETWORK_COLLECTION", "networkCollection": cid}],
                "xpos": [{"kind": "NETWORK_COLLECTION_NODES", "networkCollection": cid}],
            },
    )
    assert spaced.errors
    assert "positions rather than ids" in str(spaced.errors[0])

    # Two answers on one axis: an enumeration enumerates one thing.
    other = await _table(authenticated_context, "plain", [{"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}])
    assert not other.errors, other.errors
    doubled = await _table(
        authenticated_context,
        "doubled",
        NODE_COLUMNS,
        identified_by={
                "object_id": [{"kind": "NETWORK_COLLECTION", "networkCollection": cid}],
                "node_id": [
                    {"kind": "NETWORK_COLLECTION_NODES", "networkCollection": cid},
                    {"kind": "TABLE", "table": other.data["createTableDataset"]["id"]},
                ],
            },
    )
    assert doubled.errors
    assert "more than one enumeration" in str(doubled.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_axis_refuses_node_identification(authenticated_context: HttpContext):
    """Deferral as refusal: a matrix axis has no sibling-column convention to scope a node id."""
    collection = await _collection(authenticated_context)
    store = await sync_to_async(models.SparseStore.objects.create)(
        path="s3://zarr/per-node",
        bucket="zarr",
        key="per-node",
        organization=authenticated_context.request.organization,
        populated=True,
        spec="1",
        shape=[8, 8],
        # One real layout, so the store-shape checks pass and the refusal on trial -- the
        # identification's -- is the one that fires.
        layouts=[
            {
                "path": "axis0",
                "encoding": "csr_matrix",
                "encoding_version": "0.1.0",
                "indexed_axis": 0,
                "index_order": [1],
                "nnz": 8,
                "dtype": "float32",
                "chunks": {"data": 32768, "indices": 32768, "indptr": 32768},
                "range_readable": False,
            }
        ],
    )
    result = await schema.execute(
        "mutation Create($input: CreateSparseDatasetInput!) { createSparseDataset(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "per-node matrix",
                "store": str(store.pk),
                "axes": [
                    {"name": "node", "identifiedBy": [{"kind": "NETWORK_COLLECTION_NODES", "networkCollection": str(collection.pk)}]},
                    {"name": "gene", "identifiedBy": [{"kind": "NETWORK_COLLECTION", "networkCollection": str(collection.pk)}]},
                ],
            }
        },
    )
    assert result.errors
    assert "cannot carry" in str(result.errors[0])
    assert "createTableDataset" in str(result.errors[0]), "the refusal names the mechanism that works"


# --------------------------------------------------------------------------- #
# reachability: a product space, dropped everywhere but its own door
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_node_tables_are_product_spaces(authenticated_context: HttpContext):
    """Dropped from the walk and the plans: an object id alone cannot address a row."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    result = await _table(ctx, "node-stats", NODE_COLUMNS, identified_by=node_axes(str(collection.pk), NODE_COLUMNS))
    assert not result.errors, result.errors
    table_id = result.data["createTableDataset"]["id"]

    def reachable() -> set[str]:
        return set(attribute_plans_logic.field_reachable_tables(collection.coordinate_system, ctx.request.organization))

    assert table_id not in await sync_to_async(reachable)(), "the walk drops it; the network picker's own door offers it"

    plans = await schema.execute(
        "query P($system: ID!) { attributePlans(system: $system) { hops { table { id } } } }",
        context_value=ctx,
        variable_values={"system": str(collection.coordinate_system.pk)},
    )
    assert not plans.errors, plans.errors
    assert table_id not in {plan["hops"][0]["table"]["id"] for plan in plans.data["attributePlans"] if plan["hops"][0]["table"]}, (
        "no plan: an object-keyed hover over a per-node table would read every row of that object"
    )


# --------------------------------------------------------------------------- #
# the picker: offered with target, accepted with target STAMPED
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_options_offer_node_and_edge_columns_with_target(authenticated_context: HttpContext):
    ctx = authenticated_context
    collection = await _collection(ctx)
    node = await _table(ctx, "node-stats", NODE_COLUMNS, identified_by=node_axes(str(collection.pk), NODE_COLUMNS))
    edge = await _table(ctx, "edge-stats", EDGE_COLUMNS, identified_by=node_axes(str(collection.pk), EDGE_COLUMNS))
    assert not node.errors and not edge.errors

    options = await schema.execute(OPTIONS, context_value=ctx, variable_values={"collection": str(collection.pk)})
    assert not options.errors, options.errors
    offered = {
        (option["table"]["id"], option["column"]["name"]): option["target"]
        for option in options.data["networkColorByOptions"]
        if option["table"]
    }
    assert offered[(node.data["createTableDataset"]["id"], "dist_um")] == "NODE"
    assert offered[(edge.data["createTableDataset"]["id"], "length_um")] == "EDGE"
    # The graph attributes still lead, and they say NODE too -- their data is per node.
    graph = [option for option in options.data["networkColorByOptions"] if option["graphAttribute"]]
    assert graph and all(option["target"] == "NODE" for option in graph)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_entries_are_stamped_never_sent(authenticated_context: HttpContext):
    """The validator writes `target` from the table's shape; a caller-sent one is refused."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)
    node = await _table(ctx, "node-stats", NODE_COLUMNS, identified_by=node_axes(str(collection.pk), NODE_COLUMNS))
    edge = await _table(ctx, "edge-stats", EDGE_COLUMNS, identified_by=node_axes(str(collection.pk), EDGE_COLUMNS))
    assert not node.errors and not edge.errors
    node_id = node.data["createTableDataset"]["id"]
    edge_id = edge.data["createTableDataset"]["id"]

    result = await _layer(
        ctx,
        scene,
        collection,
        colorBys=[{"table": node_id, "column": "dist_um", "colormap": "VIRIDIS", "label": "Distance to tip"}],
        filterBys=[{"table": edge_id, "column": "length_um", "min": 0.5}],
        activeColorBy=0,
        activeFilterBys=[0],
    )
    assert not result.errors, result.errors
    layer = result.data["createNetworkLayer"]
    assert layer["colorBys"][0]["kind"] == "COLUMN"
    assert layer["colorBys"][0]["target"] == "NODE", "stamped from the table's one node axis"
    assert layer["filterBys"][0]["target"] == "EDGE", "stamped from the table's two node axes"

    # A caller-sent target on a COLUMN rule is refused, not trusted: the field is a stamp.
    sent = await _layer(
        ctx, scene, collection,
        filterBys=[{"table": edge_id, "column": "length_um", "min": 0.5, "target": "NODE"}],
    )
    assert sent.errors
    assert "stamp" in str(sent.errors[0])

    # And the same entry on the OBJECT-level table door does not exist for other layers:
    # the node table is not in any other kind's reachable set. (Object-level acceptance is
    # exercised by test_network_layers; this is the node table refused as an object entry.)
    plain = await _table(ctx, "object-stats", [
        {"name": "object_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "total", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    ], identified_by={"object_id": [{"kind": "NETWORK_COLLECTION", "networkCollection": str(collection.pk)}]})
    assert not plain.errors, plain.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_join_out_of_a_node_table_is_refused(authenticated_context: HttpContext):
    """A hop would carry the composite key into a table declared for a single id."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    scene = await _scene_for(ctx, collection)

    kinds = await _table(ctx, "kinds", [
        {"name": "kind_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "label", "dtype": "VARCHAR", "role": "LABEL"},
    ])
    assert not kinds.errors, kinds.errors
    kinds_id = kinds.data["createTableDataset"]["id"]

    columns = NODE_COLUMNS[:2] + [
        {"name": "kind_id", "dtype": "BIGINT", "role": "ID", "references": kinds_id},
        NODE_COLUMNS[2],
    ]
    node = await _table(ctx, "typed-nodes", columns, identified_by=node_axes(str(collection.pk), columns))
    assert not node.errors, node.errors
    node_id = node.data["createTableDataset"]["id"]

    result = await _layer(
        ctx, scene, collection,
        colorBys=[{
            "table": kinds_id,
            "column": "label",
            "colormap": "HUES",
            "joinPath": [{"table": node_id, "column": "kind_id"}],
        }],
    )
    assert result.errors
    assert "composite key" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_collection_is_protected_under_its_node_tables(authenticated_context: HttpContext):
    """`node_references` is PROTECT: the collection cannot be deleted out from under a column."""
    ctx = authenticated_context
    collection = await _collection(ctx)
    result = await _table(ctx, "node-stats", NODE_COLUMNS, identified_by=node_axes(str(collection.pk), NODE_COLUMNS))
    assert not result.errors, result.errors

    deletion = await schema.execute(
        "mutation Delete($input: DeleteNetworkCollectionInput!) { deleteNetworkCollection(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(collection.pk)}},
    )
    assert deletion.errors, "deleting the collection would orphan the meaning of every node id in the table"
    assert await sync_to_async(models.NetworkCollection.objects.filter(pk=collection.pk).exists)()

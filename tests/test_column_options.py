"""What a picker may offer, and the one thing that makes an options query worth having.

`attributePlans` could already be squinted at until it looked like this list, and the reason it
is not this list is the invariant asserted here: **every option returned is one the mutation
accepts, and every column left out is one it refuses**. A picker built on a set that merely
overlaps the write path's either hides legal choices or proposes refusals, and both are worse
than making the client derive it.

The second thing on trial is the join path. A column's `references` says its values identify
rows of another table; the coordinate graph deliberately stops before that hop ("tables are
always leaves"), so following it is a schema walk, and these tests are where the walk's edges --
depth, cycles, the order the list comes back in -- are pinned down.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from core.logic import column_options as column_options_logic
from mikro_server.schema import schema
from tests import seed

OPTIONS = """
query Options($collection: ID!, $filters: ColumnOptionFilter, $pagination: OffsetPaginationInput, $maxJoinDepth: Int) {
  colorByOptions(meshCollection: $collection, filters: $filters, pagination: $pagination, maxJoinDepth: $maxJoinDepth) {
    control
    column { name role unit }
    table { id name }
    joinPath { table { id } column { name } }
  }
}
"""

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id }
}
"""

CREATE_LAYER = """
mutation Create($input: CreateMeshLayerInput!) {
  createMeshLayer(input: $input) {
    id
    colorBys { table column label joinPath { table column } }
  }
}
"""

ZYX_MESH_AXES = [{"name": "z", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]

TRACK_COLUMNS = [
    {"name": "track_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "mean_velocity", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer"},
    {"name": "fate", "dtype": "VARCHAR", "role": "LABEL"},
]


def _object_columns(tracks: str | None) -> list[dict]:
    """One row per mesh: a key, a measure, a class, and optionally a hop into the tracks table."""
    columns = [
        {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer**3"},
        {"name": "cell_type", "dtype": "VARCHAR", "role": "LABEL"},
    ]
    if tracks is not None:
        columns.append({"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks})
    return columns


async def _collection(ctx: HttpContext) -> models.MeshCollection:
    store = await seed.create_fabriks_store(ctx)
    result = await schema.execute(
        "mutation Create($input: CreateMeshCollectionInput!) { createMeshCollection(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"version": "v1", "store": str(store.pk), "axes": ZYX_MESH_AXES}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.MeshCollection.objects.get)(id=result.data["createMeshCollection"]["id"])


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> str:
    store = await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{name}", bucket="parquet", key=name, organization=ctx.request.organization, populated=True)
    result = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, **extra)},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _options(ctx: HttpContext, collection: models.MeshCollection, **extra: object):
    result = await schema.execute(OPTIONS, context_value=ctx, variable_values={"collection": str(collection.pk), **extra})
    assert not result.errors, result.errors
    return result.data["colorByOptions"]


async def _keyed_stack(ctx: HttpContext, *, with_tracks: bool = True):
    """A collection, a table keyed off it, and (optionally) a tracks table one hop further."""
    collection = await _collection(ctx)
    tracks = await _table(ctx, "tracks", TRACK_COLUMNS) if with_tracks else None
    objects = await _table(
        ctx,
        "shape-stats",
        _object_columns(tracks),
        keyed_by=[{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}],
    )
    return collection, objects, tracks


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_options_are_exactly_what_the_mutation_accepts(authenticated_context: HttpContext):
    """The invariant this query exists for, asserted the only way that means anything: by writing them.

    Every option is fed straight back into `createMeshLayer(colorBys:)`. If the enumerator and
    the validator ever walk different sets, this is where it shows -- and it is worth more than
    any assertion about the list's contents, because the contents are allowed to grow.
    """
    collection, objects, tracks = await _keyed_stack(authenticated_context)
    scene = await seed.create_scene(authenticated_context, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind="IDENTITY",
            input=collection.coordinate_system,
            output=scene.world,
            params={},
            organization=authenticated_context.request.organization,
        )

    await sync_to_async(register)()

    options = await _options(authenticated_context, collection)
    assert options, "the fixture keys a table off this collection, so there is something to offer"

    for option in options:
        entry = {
            "table": option["table"]["id"],
            "column": option["column"]["name"],
            "joinPath": [{"table": step["table"]["id"], "column": step["column"]["name"]} for step in option["joinPath"]],
        }
        # The control says which *sort* of colormap the column admits, and the mutation
        # enforces exactly that -- which is the invariant this test is for.
        entry["colormap"] = "VIRIDIS" if option["control"] == "MEASURE" else "HUES"

        result = await schema.execute(
            CREATE_LAYER,
            context_value=authenticated_context,
            variable_values={"input": {"scene": str(scene.pk), "meshCollection": str(collection.pk), "colorBys": [entry]}},
        )
        assert not result.errors, f"offered {entry} and the mutation refused it: {result.errors}"
        assert result.data["createMeshLayer"]["colorBys"][0]["joinPath"] == entry["joinPath"]

    # And the other half of the invariant: a table nothing keys is not offered, and is refused.
    unrelated = await _table(authenticated_context, "unrelated", _object_columns(None))
    assert not [option for option in options if option["table"]["id"] == unrelated]

    refused = await schema.execute(
        CREATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "meshCollection": str(collection.pk), "colorBys": [{"table": unrelated, "column": "volume", "colormap": "VIRIDIS"}]}},
    )
    assert refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_control_follows_the_column_role(authenticated_context: HttpContext):
    """The split a picker renders its controls from, published rather than re-derived by a client."""
    collection, objects, tracks = await _keyed_stack(authenticated_context, with_tracks=False)

    options = await _options(authenticated_context, collection)
    controls = {option["column"]["name"]: option["control"] for option in options}

    assert controls["volume"] == "MEASURE", "a measured attribute takes a colormap and a range"
    assert controls["cell_type"] == "CATEGORICAL", "a class label takes an explicit map and a value set"
    assert controls["object"] == "MEASURE", "a COORDINATE column is measured too -- the same split Column uses for units"

    units = {option["column"]["name"]: option["column"]["unit"] for option in options}
    assert units["volume"] == "micrometer**3", "the unit rides along, so a range control can label its slider"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_reference_is_one_hop_and_is_offered_with_its_path(authenticated_context: HttpContext):
    """The case the picker could not express before: colouring by a column of the *tracks* table.

    `instance_id` references `tracks`, so `tracks.mean_velocity` is one lookup further. The hop
    is offered with the path that reaches it, and the path is what makes it selectable.
    """
    collection, objects, tracks = await _keyed_stack(authenticated_context)

    options = await _options(authenticated_context, collection, maxJoinDepth=1)
    hopped = [option for option in options if option["joinPath"]]

    assert {option["column"]["name"] for option in hopped} == {"track_id", "mean_velocity", "fate"}, "every column of the referenced table, and only those"
    velocity = next(option for option in hopped if option["column"]["name"] == "mean_velocity")
    assert velocity["table"]["id"] == tracks
    assert velocity["joinPath"] == [{"table": {"id": objects}, "column": {"name": "instance_id"}}]
    assert velocity["control"] == "MEASURE"

    # The hop column stays offerable in its own right: colouring the meshes *by* their track id
    # is a different choice from colouring them by something the track knows.
    direct = [option for option in options if not option["joinPath"]]
    assert "instance_id" in {option["column"]["name"] for option in direct}

    shallow = await _options(authenticated_context, collection, maxJoinDepth=0)
    assert all(not option["joinPath"] for option in shallow), "depth 0 is the set that existed before join paths"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_narrowings_narrow(authenticated_context: HttpContext):
    """Search, roles, controls, table and directOnly -- each keeps what it says and drops the rest."""
    collection, objects, tracks = await _keyed_stack(authenticated_context)

    searched = await _options(authenticated_context, collection, filters={"search": "veloc"})
    assert {option["column"]["name"] for option in searched} == {"mean_velocity"}

    by_table_name = await _options(authenticated_context, collection, filters={"search": "tracks"})
    assert by_table_name and all(option["table"]["id"] == tracks for option in by_table_name), "the table's name is searched too, so a whole table is findable by name"

    measures = await _options(authenticated_context, collection, filters={"controls": ["MEASURE"]})
    assert all(option["control"] == "MEASURE" for option in measures)

    labels = await _options(authenticated_context, collection, filters={"roles": ["LABEL"]})
    assert {option["column"]["name"] for option in labels} == {"cell_type", "fate"}

    # `table` means the table the value is *read from*, not one the path passes through: the
    # hopped options read from `tracks` and are dropped by the table they hop out of.
    one_table = await _options(authenticated_context, collection, filters={"table": objects})
    assert all(option["table"]["id"] == objects for option in one_table)
    assert all(not option["joinPath"] for option in one_table), "an option hopping out of this table reads from another one"

    hopped_only = await _options(authenticated_context, collection, filters={"table": tracks})
    assert hopped_only and all(option["joinPath"] for option in hopped_only), "nothing keys tracks directly, so every option reading from it is reached by a hop"

    direct = await _options(authenticated_context, collection, filters={"directOnly": True})
    assert all(not option["joinPath"] for option in direct)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_paging_is_stable_and_the_order_does_not_move(authenticated_context: HttpContext):
    """A picker whose second page reshuffles is a menu whose rows move under the cursor."""
    collection, objects, tracks = await _keyed_stack(authenticated_context)

    everything = await _options(authenticated_context, collection)
    again = await _options(authenticated_context, collection)
    assert [option["column"]["name"] for option in everything] == [option["column"]["name"] for option in again]

    first = await _options(authenticated_context, collection, pagination={"offset": 0, "limit": 2})
    second = await _options(authenticated_context, collection, pagination={"offset": 2, "limit": 2})
    assert first == everything[:2]
    assert second == everything[2:4]

    direct = [option for option in everything if not option["joinPath"]]
    assert everything[: len(direct)] == direct, "direct columns come first, so a client wanting only those reads a prefix"


FILTER_OPTIONS = """
query Options($collection: ID!, $filters: ColumnOptionFilter) {
  filterByOptions(meshCollection: $collection, filters: $filters) {
    control
    column { name role unit }
    table { id name }
    joinPath { table { id } column { name } }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_two_pickers_are_offered_the_same_candidates(authenticated_context: HttpContext):
    """`filterByOptions` is a second name for one answer, not a second answer.

    A colouring and a rule reach the same column through the same join and branch on the same
    role, so the sets must be identical -- if they ever diverge, one of them is offering
    something its write path refuses. The prose differs (MEASURE is a bound here and a colormap
    there); the candidates do not.
    """
    collection, objects, tracks = await _keyed_stack(authenticated_context)

    coloured = await _options(authenticated_context, collection)
    result = await schema.execute(FILTER_OPTIONS, context_value=authenticated_context, variable_values={"collection": str(collection.pk)})
    assert not result.errors, result.errors
    filtered = result.data["filterByOptions"]

    assert filtered == coloured, "same walk, same order, same rows"

    narrowed = await schema.execute(
        FILTER_OPTIONS,
        context_value=authenticated_context,
        variable_values={"collection": str(collection.pk), "filters": {"controls": ["CATEGORICAL"]}},
    )
    assert not narrowed.errors, narrowed.errors
    assert narrowed.data["filterByOptions"], "the shared filter input narrows this query too"
    assert all(option["control"] == "CATEGORICAL" for option in narrowed.data["filterByOptions"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_offers_its_own_options(authenticated_context: HttpContext):
    """The nested field, over the same walk: what this collection can be coloured by, in one query."""
    collection, objects, tracks = await _keyed_stack(authenticated_context)

    result = await schema.execute(
        "query Nested($id: ID!) { meshCollection(id: $id) { colorByOptions { column { name } joinPath { column { name } } } } }",
        context_value=authenticated_context,
        variable_values={"id": str(collection.pk)},
    )
    assert not result.errors, result.errors

    nested = result.data["meshCollection"]["colorByOptions"]
    flat = await _options(authenticated_context, collection)
    assert [option["column"]["name"] for option in nested] == [option["column"]["name"] for option in flat]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_costs_the_same_however_many_columns_there_are(authenticated_context: HttpContext):
    """The query count follows the join depth, never the schema's size.

    `field_edges_from` prefetches the tables but not their columns, and `columns_by_role` defeats
    a prefetch outright -- so the easy way to write this walk is one query per table and then one
    per column. The house norm is a constant count (`tests/test_placement_queries.py`), and a
    count that grows with the columns is that N+1 coming back whatever shape it returns in.

    It has come back once already: deciding whether a target is a product space is a fact about
    its columns, and asking it per edge reintroduced exactly this. The batched form
    (`graph_logic.product_space_tables`) is why the count below is a constant three rather than
    a number that follows the graph.
    """
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    collection, objects, tracks = await _keyed_stack(authenticated_context)
    organization = authenticated_context.request.organization

    def column_reads(max_join_depth: int) -> int:
        """How many times the walk goes to the database *for columns*.

        Counted rather than the whole query count, which also carries the coordinate-graph walk
        and varies with the shape of the graph rather than with the schema. What is on trial
        here is one read per level.
        """
        with CaptureQueriesContext(connection) as captured:
            options = column_options_logic.build_column_options(collection.coordinate_system, organization, max_join_depth=max_join_depth)
            assert options
        # Asked of the model rather than spelled out: this counted `core_tablecolumn` from the
        # day `TableColumn` was renamed to `Column` (migration 0007) until 2026-08-20, matched
        # nothing, and returned 0 -- so the N+1 this guards against would have gone straight
        # through it. A count that can silently become "no queries at all" is not a guard.
        table = models.Column._meta.db_table
        return len([query for query in captured.captured_queries if table in query["sql"]])

    lean = await sync_to_async(column_reads)(1)
    # Three: one read per level -- the seed tables and the hop -- plus one for the reachability
    # filter, which asks in a single `IN (...)` which of the reachable tables are product spaces
    # (`graph_logic.product_space_tables`). That third is one read for the whole set however
    # large the set is, which is why the assertion below is the one that matters: it is what
    # separates "a constant" from "a constant per level", and an N+1 would fail it.
    assert lean == 3, f"one read per level, plus one for the reachability filter -- got {lean}"

    # A second keyed table with columns of its own, and a hop of its own off the tracks table.
    await _table(authenticated_context, "more-stats", _object_columns(tracks), keyed_by=[{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}])
    fat = await sync_to_async(column_reads)(1)

    assert fat == lean, f"the walk is per level, not per table: {lean} then {fat}"

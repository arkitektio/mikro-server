"""The flat column declaration: axis-ness and identification live on `ColumnInput`.

Until 2026-08-20 a table said this in two sibling lists (`keyedBy` + `Column.references`);
then in `axes` + `columns`, where the same fact had two wire doors -- `ColumnInput.references`
and `TableAxisInput.identifiedBy [{kind: TABLE}]` both wrote `Column.references`, with the
axis silently winning on conflict and the column door open to SPACE axes the identification
door refused. Now a column is declared ONCE: a non-null `axisType` makes it an axis, and
`identifiedBy` is the one spelling of every "values here identify things there" claim, for
axes and data columns alike.

**Axis order is the axis-typed columns in column (= file) order.** A table has no byte
order: its axes are named columns, every consumer addresses them by name, and an edge that
wants a different order states its own `inputAxes`/`outputAxes`. So there is no list to
reorder the space with, and no way for the space and the file to disagree.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    columns { name role order references { id } }
    coordinateSystem { axes { name type order } }
  }
}
"""

_LOCALIZATIONS = [
    {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "photons", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


async def _parquet(ctx: HttpContext, key: str, columns: list[dict]) -> models.ParquetStore:
    """A finished store carrying exactly the columns these tests declare over."""
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization, populated=True,
        columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in seed.split_declaration(columns)[0]],
    )


async def _create(ctx: HttpContext, name: str, columns: list[dict], declared: list[dict] | None = None):
    """Create over a store matching ``columns``; ``declared`` overrides the wire columns."""
    store = await _parquet(ctx, name.replace(" ", "-"), columns)
    payload = {
        "name": name,
        "data": str(store.pk),
        "columns": seed.flat_columns(columns) if declared is None else declared,
    }
    return await schema.execute(CREATE, context_value=ctx, variable_values={"input": payload})


# --- the space is the axis-typed columns, in file order ----------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_axis_order_is_the_column_order(authenticated_context: HttpContext):
    """One declaration, one order: the axis-typed columns, as the file runs.

    The separate `axes` list existed to state an order different from the file's; for a
    table that order carried no information -- nothing strides a table by position, every
    consumer addresses its axes by name, and an edge wanting a different order states its own
    axis lists. So `Axis.order` here is `Column.order` restricted to the axes, and a caller
    who wants (x, y) writes the parquet that way.
    """
    result = await _create(authenticated_context, "molecules", _LOCALIZATIONS)
    assert not result.errors, result.errors
    table = result.data["createTableDataset"]

    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["y", "x"], "the axis-typed columns, in file order"
    assert [column["name"] for column in table["columns"]] == ["y", "x", "photons"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_without_axis_type_is_an_ordinary_column(authenticated_context: HttpContext):
    """`axisType` is the ONLY thing that makes a column an axis.

    A column without one is simply a column, inferred from the file as an ATTRIBUTE; there is
    no second list for it to be missing from, so there is nothing to disagree with.
    """
    declared = seed.flat_columns(_LOCALIZATIONS)
    # Only `y` stays an axis; `x` becomes a plain declared column.
    declared[1] = {"name": "x", "dtype": "DOUBLE"}
    result = await _create(authenticated_context, "molecules", _LOCALIZATIONS, declared=declared)

    assert not result.errors, result.errors
    table = result.data["createTableDataset"]
    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["y"]
    roles = {column["name"]: column["role"] for column in table["columns"]}
    assert roles == {"y": "COORDINATE", "x": "ATTRIBUTE", "photons": "ATTRIBUTE"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_axis_type_beside_a_role_is_refused(authenticated_context: HttpContext):
    """An axis column IS a COORDINATE; a role beside `axisType` answers twice.

    The guard the two-list shape never had for `references` and the descriptive triplet --
    now the whole question is one field, so a second answer is refused rather than silently
    preferred.
    """
    declared = seed.flat_columns(_LOCALIZATIONS)
    declared[0]["role"] = "ID"
    result = await _create(authenticated_context, "molecules", _LOCALIZATIONS, declared=declared)

    assert result.errors
    assert "declares both `axisType` and `role`" in str(result.errors[0])


# --- identification, carried on the column ------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_index_axis_may_be_identified_by_the_table_it_enumerates(authenticated_context: HttpContext):
    """Item 7's product space, on the flat declaration.

    An INDEX axis' values are already ids, so naming the table it enumerates is not a second
    map -- it is what the enumeration is *of*. It lands on `Column.references`, as it always
    did; what changed is that there is exactly one wire door to it.
    """
    cells = await _create(
        authenticated_context,
        "cells",
        [
            {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    assert not cells.errors, cells.errors
    cell_table = cells.data["createTableDataset"]["id"]

    contacts_columns = [
        {"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "overlap", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    ]
    contacts = await _create(
        authenticated_context,
        "contacts",
        contacts_columns,
        declared=seed.flat_columns(
            contacts_columns,
            identified_by={"cell_id": [{"kind": "TABLE", "table": cell_table}]},
        ),
    )
    assert not contacts.errors, contacts.errors

    references = {c["name"]: c["references"] for c in contacts.data["createTableDataset"]["columns"]}
    assert references["cell_id"] == {"id": cell_table}, "it lands on the column, as it always did"
    assert references["nucleus_id"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_data_column_may_reference_a_table(authenticated_context: HttpContext):
    """The retired `ColumnInput.references`, spelled the one remaining way.

    An `instance_id` data column referencing a table of tracks is a foreign key, not a map:
    it authors no edge, makes the column no axis, and is exactly the edge of the join graph
    `colorBys` walks. The flat shape keeps it a TABLE identification on the column.
    """
    tracks = await _create(
        authenticated_context,
        "tracks",
        [
            {"name": "track_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
            {"name": "speed", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
        ],
    )
    assert not tracks.errors, tracks.errors
    track_table = tracks.data["createTableDataset"]["id"]

    columns = [
        {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "track", "dtype": "BIGINT", "role": "ID"},
        {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    ]
    result = await _create(
        authenticated_context,
        "objects",
        columns,
        declared=seed.flat_columns(columns, identified_by={"track": [{"kind": "TABLE", "table": track_table}]}),
    )
    assert not result.errors, result.errors

    table = result.data["createTableDataset"]
    references = {c["name"]: c["references"] for c in table["columns"]}
    assert references["track"] == {"id": track_table}, "the FK, on a plain data column"
    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["object"], "referencing did not make it an axis"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_keying_source_on_a_data_column_is_refused(authenticated_context: HttpContext):
    """A FIELD edge produces an axis, so it cannot land on a column that is not one.

    Under the two-list shape this was unstateable (identifications lived on axes); flat, it
    is stateable and refused with the fix named.
    """
    mask = await seed.create_array_dataset(authenticated_context, "mask")
    columns = [
        {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
        {"name": "other_id", "dtype": "BIGINT", "role": "ID"},
    ]
    result = await _create(
        authenticated_context,
        "objects",
        columns,
        declared=seed.flat_columns(columns, identified_by={"other_id": [{"kind": "DATASET", "dataset": str(mask.pk)}]}),
    )

    assert result.errors
    message = str(result.errors[0])
    assert "authors a FIELD edge" in message
    assert "`axisType: INDEX`" in message, "the refusal names the fix"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_identification_on_a_space_axis_is_refused(authenticated_context: HttpContext):
    """A position in nanometres and a row id are different things.

    Under the two-list shape this rule guarded only the identification door while
    `ColumnInput.references` could smuggle a reference onto a SPACE axis unchecked. One door
    now, so the rule holds by construction and this is its only test.
    """
    cells = await _create(
        authenticated_context,
        "cells",
        [{"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}],
    )
    assert not cells.errors, cells.errors

    result = await _create(
        authenticated_context,
        "molecules",
        _LOCALIZATIONS,
        declared=seed.flat_columns(
            _LOCALIZATIONS,
            identified_by={"x": [{"kind": "TABLE", "table": cells.data["createTableDataset"]["id"]}]},
        ),
    )

    assert result.errors
    message = str(result.errors[0])
    assert "SPACE axis" in message
    assert "positions rather than ids" in message
    assert not await sync_to_async(models.TableDataset.objects.filter(name="molecules").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_identified_by_two_tables_is_refused(authenticated_context: HttpContext):
    """Fan-in is only meaningful for the kinds that author an edge.

    Two masks keying one axis are two edges, each standing on its own. Two *tables* would be
    two different answers to what a value in the column is -- and one column carries one
    `references`, so only one of them could even be recorded.
    """
    first = await _create(
        authenticated_context, "cells", [{"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}]
    )
    second = await _create(
        authenticated_context, "nuclei", [{"name": "nucleus_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}]
    )
    assert not first.errors and not second.errors

    columns = [{"name": "thing_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}]
    result = await _create(
        authenticated_context,
        "contacts",
        columns,
        declared=seed.flat_columns(
            columns,
            identified_by={
                "thing_id": [
                    {"kind": "TABLE", "table": first.data["createTableDataset"]["id"]},
                    {"kind": "TABLE", "table": second.data["createTableDataset"]["id"]},
                ]
            },
        ),
    )

    assert result.errors
    message = str(result.errors[0])
    # "enumeration", not "table", since NETWORK_COLLECTION_NODES joined the no-edge kinds:
    # the refusal covers any pair of answers, a table and a collection's nodes included.
    assert "identified by more than one enumeration" in message
    assert "two masks may key one axis" in message, "say which fan-in is meaningful"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_with_no_identification_is_ordinary(authenticated_context: HttpContext):
    """`identifiedBy` defaults to empty, and that is the common case.

    A localization table's `x` axis is identified by nothing: its values are positions, and a
    position is not an id of anything. The sparse path refuses an empty list because every
    axis of a matrix is positions-and-nothing-else; a table's is not, which is why that check
    belongs to the caller that means it rather than to the shared splitter.
    """
    result = await _create(authenticated_context, "molecules", _LOCALIZATIONS)

    assert not result.errors, result.errors
    axes = result.data["createTableDataset"]["coordinateSystem"]["axes"]
    assert [axis["name"] for axis in axes] == ["y", "x"]

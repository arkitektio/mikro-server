"""The declaration and the file are two statements about the same bytes, and both are checked.

`createTableDataset` takes every column of the Parquet, with its name and its DuckDB type, and
checks the list against `ParquetStore.columns` -- what `fill_info` read off the file when the
upload finished. Same names, same order, same types, or the create is refused.

That check is what `validateSchema` promised and never made. Its description said it rejected
"any declared column whose name/dtype does not match the file"; the implementation compared
`col.name not in actual` and threw the type half away, and no test ever set the flag. So a table
could go up declaring DOUBLE where the file said FLOAT and nothing on either side would say so.
There is no flag now -- the file is read on every create, and a declaration that has drifted
from the data is worth less than none, because everything downstream reads it as true.

A coordinate column is declared ONCE, in `columns`, with an `axisType` -- it is a column of
the file like any other that is also a position in a space, and since the declaration must
match the file name-for-name in order, the axis order is the file's order restricted to the
axis-typed columns. There is no second list for the two statements to disagree across.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from datalayer.models import ZarrStore
from mikro_server.schema import schema

CREATE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) {
    id
    columns { name dtype role unit longName order }
    coordinateSystem { axes { name type order } }
  }
}
"""

#: What the *file* says. In an order a person would not have chosen, and with a type per column
#: that a caller would plausibly get wrong: a float32 is FLOAT, and pandas calls it float32.
FILE = [("volume", "DOUBLE"), ("object_id", "BIGINT"), ("intensity", "FLOAT"), ("label", "VARCHAR")]

#: The same thing, declared. Every column, in the file's order, with the file's types.
DECLARED = [{"name": name, "dtype": dtype} for name, dtype in FILE]


async def _store(ctx: HttpContext, key: str, columns=FILE) -> models.ParquetStore:
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization,
        populated=True, columns=[{"name": n, "type": t, "nullable": True} for n, t in columns],
    )


async def _create(ctx: HttpContext, key: str, *, file=FILE, **payload):
    store = await _store(ctx, key, file)
    payload.setdefault("columns", DECLARED)
    return await schema.execute(
        CREATE, context_value=ctx, variable_values={"input": {"name": key, "data": str(store.pk), **payload}}
    )


# --- the declaration is checked against the file -----------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_declaration_that_matches_the_file_is_stored_as_given(authenticated_context: HttpContext):
    result = await _create(
        authenticated_context, "measurements",
        columns=[
            {"name": "volume", "dtype": "DOUBLE", "unit": "micrometer**3"},
            {"name": "object_id", "dtype": "BIGINT", "axisType": "INDEX"},
            {"name": "intensity", "dtype": "FLOAT", "longName": "mean signal"},
            {"name": "label", "dtype": "VARCHAR", "role": "LABEL"},
        ],
    )
    assert not result.errors, result.errors
    columns = result.data["createTableDataset"]["columns"]

    assert [(c["name"], c["dtype"], c["role"], c["order"]) for c in columns] == [
        ("volume", "DOUBLE", "ATTRIBUTE", 0),
        ("object_id", "BIGINT", "COORDINATE", 1),
        ("intensity", "FLOAT", "ATTRIBUTE", 2),
        ("label", "VARCHAR", "LABEL", 3),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_the_file_does_not_have_is_refused(authenticated_context: HttpContext):
    result = await _create(
        authenticated_context, "measurements",
        columns=DECLARED + [{"name": "not_in_the_file", "dtype": "DOUBLE"}],
    )

    assert result.errors
    message = str(result.errors[0])
    assert "does not describe its Parquet" in message
    assert "the declaration has ['not_in_the_file'] and the file does not" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_of_the_file_left_undeclared_is_refused(authenticated_context: HttpContext):
    """Every column is declared. A file column with nothing said about it is a gap in the
    statement, not a shorthand -- the declaration is about *these* bytes."""
    result = await _create(authenticated_context, "measurements", columns=DECLARED[:-1])

    assert result.errors
    message = str(result.errors[0])
    assert "the file has ['label'] and the declaration does not" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_declared_order_must_be_the_files(authenticated_context: HttpContext):
    """Because `Column.order` is the file's order, and a reader takes it as such."""
    result = await _create(authenticated_context, "measurements", columns=list(reversed(DECLARED)))

    assert result.errors
    message = str(result.errors[0])
    assert "the same columns in a different order" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dtype_the_file_does_not_have_is_refused(authenticated_context: HttpContext):
    """The half `validateSchema` promised and dropped.

    `intensity` is a float32, which DuckDB reads back as FLOAT. A caller writing the
    declaration by hand reaches for DOUBLE -- and until now nothing ever told them otherwise.
    """
    wrong = [dict(column) for column in DECLARED]
    wrong[2]["dtype"] = "DOUBLE"
    result = await _create(authenticated_context, "measurements", columns=wrong)

    assert result.errors
    message = str(result.errors[0])
    assert "'intensity' is declared DOUBLE and the file records FLOAT" in message
    assert "DuckDB" in message, "say which vocabulary the name is from"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_pandas_dtype_name_is_refused_as_such(authenticated_context: HttpContext):
    """The trap four `testing/` scripts used to warn about at length, now actually caught."""
    wrong = [dict(column) for column in DECLARED]
    wrong[0]["dtype"] = "float64"
    result = await _create(authenticated_context, "measurements", columns=wrong)

    assert result.errors
    assert "'volume' is declared float64 and the file records DOUBLE" in str(result.errors[0])


# --- axis-ness is part of the one column declaration -------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_must_be_a_column_of_the_file(authenticated_context: HttpContext):
    """An axis that is no column of the file is unstateable except as a phantom column --
    and the file check catches that one, so no axis-specific check is needed."""
    result = await _create(
        authenticated_context, "measurements",
        columns=DECLARED + [{"name": "not_a_column", "dtype": "BIGINT", "axisType": "INDEX"}],
    )

    assert result.errors
    assert "the declaration has ['not_a_column'] and the file does not" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_may_not_claim_the_coordinate_role(authenticated_context: HttpContext):
    """COORDINATE follows from `axisType`; as a bare role it names no axis type to build."""
    columns = [dict(column) for column in DECLARED]
    columns[1]["role"] = "COORDINATE"
    result = await _create(authenticated_context, "measurements", columns=columns)

    assert result.errors
    assert "say so with `axisType`" in str(result.errors[0])


# --- how wide a table may be -------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_matrix_wide_file_is_refused_and_told_where_it_belongs(authenticated_context: HttpContext):
    """The refusal is about shape rather than size, so it says so at length.

    A caller who hits this has a real object -- an expression matrix, an intensity matrix --
    and needs to know that there is somewhere for it to go and roughly what that looks like,
    not merely that a number was exceeded.
    """
    wide = [(f"gene_{index}", "FLOAT") for index in range(3001)]
    result = await _create(
        authenticated_context, "expression",
        file=wide,
        columns=[{"name": name, "dtype": dtype} for name, dtype in wide],
    )

    assert result.errors
    message = str(result.errors[0])
    assert "3,001 columns" in message and "3,000" in message
    assert "shape, not size" in message
    assert "createSparseDataset" in message
    assert "one axis with one picker entry" in message
    assert "requestSparseUpload" in message, "name the upload path, not only the mutation"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_just_inside_the_cap_is_accepted(authenticated_context: HttpContext):
    """So the test above is failing on the width and not on something else about wide files."""
    wide = [(f"gene_{index}", "FLOAT") for index in range(3000)]
    result = await _create(
        authenticated_context, "expression",
        file=wide,
        columns=[{"name": name, "dtype": dtype} for name, dtype in wide],
    )

    assert not result.errors, result.errors


# --- the store has to know its own columns for any of this to mean anything ---


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_store_that_knows_no_columns_is_refused(authenticated_context: HttpContext):
    """There is nothing to check the declaration against, and checking it is the point.

    Refused rather than described on demand: `columns_for_store`'s fallback reaches for the
    object store, and this path runs on every create -- a network hang where a sentence belongs.
    """
    store = await sync_to_async(models.ParquetStore.objects.create)(
        path="s3://parquet/unfinished", bucket="parquet", key="unfinished",
        organization=authenticated_context.request.organization, populated=True, columns=None,
    )
    result = await schema.execute(
        CREATE, context_value=authenticated_context,
        variable_values={"input": {"name": "measurements", "data": str(store.pk), "columns": []}},
    )

    assert result.errors
    assert "no recorded schema" in str(result.errors[0])

"""A table's row count and column ranges come off its parquet footer; histograms come from a client.

The footer is read for real -- a parquet DuckDB writes to a local path -- because the
aggregation over row groups (a row group without stats makes the answer unknown, not partial)
is the part worth testing. What a client computed is pinned to the table's anchors as a
spoke, like an array's value histogram, and read back on the column.
"""

from pathlib import Path

import duckdb
import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from core.logic import table_stats
from mikro_server.schema import schema
from tests.test_fileable_folder import _create_array_dataset, _create_table


def _write_parquet(path: Path) -> str:
    """1000 rows in four row groups: an id, a measure, a text column with nulls, and a timestamp."""
    location = str(path / "cells.parquet")
    connection = duckdb.connect()
    connection.execute(
        f"""
        COPY (
            SELECT i AS object, i * 1.5 AS area,
                   CASE WHEN i % 3 = 0 THEN NULL ELSE 'c' || i END AS label,
                   TIMESTAMP '2020-01-01' + INTERVAL (i) DAY AS seen
            FROM range(1000) t(i)
        ) TO '{location}' (FORMAT parquet, ROW_GROUP_SIZE 250)
        """
    )
    connection.close()
    return location


def test_the_footer_gives_rows_ranges_and_nulls_without_a_scan(tmp_path: Path) -> None:
    footer = table_stats.read_footer(_write_parquet(tmp_path))

    assert footer.row_count == 1000
    assert footer.columns["object"] == table_stats.ColumnFooter(
        minimum=0.0, maximum=999.0, null_count=0
    )
    assert footer.columns["area"] == table_stats.ColumnFooter(
        minimum=0.0, maximum=1498.5, null_count=0
    )
    assert footer.columns["label"].null_count == 334
    assert (footer.columns["label"].minimum, footer.columns["seen"].maximum) == (
        None,
        None,
    ), "only numbers have a range"


async def _query(ctx: HttpContext, query: str, **variables: object) -> dict:
    result = await schema.execute(query, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tables_footer_facts_are_read_on_the_table(
    authenticated_context: HttpContext, tmp_path: Path
) -> None:
    ctx = authenticated_context
    created = await _create_table(ctx, "cells")
    query = "query Q($id: ID!) { tableDataset(id: $id) { rowCount columns { name stats { min max nullCount } } } }"

    unread = (await _query(ctx, query, id=created["id"]))["tableDataset"]
    assert unread["rowCount"] is None, (
        "a store whose parquet cannot be read leaves the table without a count, and the create succeeds"
    )

    location = _write_parquet(tmp_path)
    table = await models.TableDataset.objects.aget(pk=created["id"])
    assert await sync_to_async(table_stats.record_footer)(table, location)

    read = (await _query(ctx, query, id=created["id"]))["tableDataset"]
    assert read["rowCount"] == 1000
    assert read["columns"] == [
        {"name": "object", "stats": {"min": 0.0, "max": 999.0, "nullCount": 0}}
    ]


ANCHOR = """
mutation A($input: CreateCoordinateAnchorInput!) {
  createCoordinateAnchor(input: $input) { id columnSummaries { column { name } histogram bins distinctCount } }
}
"""

SUMMARY = "query Q($id: ID!) { tableDataset(id: $id) { columns { name summary { histogram bins distinctValues distinctCount } } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_client_pins_a_column_summary_to_the_table(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    table = await _create_table(ctx, "summarised")
    summary = {
        "column": "object",
        "histogram": [3, 5, 2],
        "bins": [0, 10, 20, 30],
        "distinctValues": [1, 2, 3],
        "distinctCount": 3,
    }

    pinned = await _query(
        ctx,
        ANCHOR,
        input={
            "table": table["id"],
            "anchor": {"axisAnchors": [], "columnSummaries": [summary]},
        },
    )
    assert pinned["createCoordinateAnchor"]["columnSummaries"] == [
        {
            "column": {"name": "object"},
            "histogram": [3.0, 5.0, 2.0],
            "bins": [0.0, 10.0, 20.0, 30.0],
            "distinctCount": 3,
        }
    ]

    read = (await _query(ctx, SUMMARY, id=table["id"]))["tableDataset"]["columns"][0][
        "summary"
    ]
    assert read == {
        "histogram": [3.0, 5.0, 2.0],
        "bins": [0.0, 10.0, 20.0, 30.0],
        "distinctValues": [1, 2, 3],
        "distinctCount": 3,
    }

    again = {
        **summary,
        "histogram": [10],
        "bins": [0, 30],
        "distinctValues": None,
        "distinctCount": None,
    }
    await _query(
        ctx,
        ANCHOR,
        input={
            "table": table["id"],
            "anchor": {"axisAnchors": [], "columnSummaries": [again]},
        },
    )
    read = (await _query(ctx, SUMMARY, id=table["id"]))["tableDataset"]["columns"][0][
        "summary"
    ]
    assert read["histogram"] == [10.0], (
        "a summary stated again replaces the earlier one"
    )
    assert (
        await models.ColumnSummary.objects.filter(column__table_id=table["id"]).acount()
        == 1
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_column_summary_is_refused_where_it_cannot_belong(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    table = await _create_table(ctx, "strict")
    dataset = await _create_array_dataset(ctx, "image")

    async def refused(input: dict) -> str:
        result = await schema.execute(
            ANCHOR, context_value=ctx, variable_values={"input": input}
        )
        assert result.errors, result.data
        return str(result.errors[0])

    assert "not a column of this table" in await refused(
        {
            "table": table["id"],
            "anchor": {"axisAnchors": [], "columnSummaries": [{"column": "area"}]},
        }
    )
    assert "table's anchor" in await refused(
        {
            "dataset": dataset["id"],
            "anchor": {"axisAnchors": [], "columnSummaries": [{"column": "object"}]},
        }
    )
    assert "bin edges" in await refused(
        {
            "table": table["id"],
            "anchor": {
                "axisAnchors": [],
                "columnSummaries": [
                    {"column": "object", "histogram": [1, 2], "bins": [0]}
                ],
            },
        }
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_counts_past_32_bits_are_answered_and_accepted(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    created = await _create_table(ctx, "huge")
    huge = 3 * 2**31
    await models.TableDataset.objects.filter(pk=created["id"]).aupdate(row_count=huge)
    await models.Column.objects.filter(table_id=created["id"]).aupdate(null_count=huge)

    read = (
        await _query(
            ctx,
            "query Q($id: ID!) { tableDataset(id: $id) { rowCount columns { stats { nullCount } } } }",
            id=created["id"],
        )
    )["tableDataset"]
    assert read == {"rowCount": huge, "columns": [{"stats": {"nullCount": huge}}]}

    summary = {
        "column": "object",
        "histogram": [1],
        "bins": [0, 1],
        "distinctCount": huge,
    }
    pinned = await _query(
        ctx,
        ANCHOR,
        input={
            "table": created["id"],
            "anchor": {"axisAnchors": [], "columnSummaries": [summary]},
        },
    )
    assert (
        pinned["createCoordinateAnchor"]["columnSummaries"][0]["distinctCount"] == huge
    )

"""What a table's parquet footer says about its rows, read without reading a row.

A parquet file states, per row group and column chunk, how many rows it holds, the smallest
and largest value of each column and how many of them are null. Summed over the row groups
that is the table's row count and each column's range and null count: a footer read, the same
footprint as the DESCRIBE and the codec check its finished upload already ran, and **never a
scan**. Distinct values and histograms need the rows; a client computes them over its own
access grant and pins them to the table's anchors (`ColumnSummary`), where every later reader
finds them.

Recorded once per table: when `createTableDataset` makes it, and by `record_table_stats` for the
tables made before. A parquet is immutable, so a recorded fact never goes stale.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config as BotoConfig
from django.db import transaction

from core import models

if TYPE_CHECKING:
    from arkitekt_datalayer.datalayer import Datalayer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnFooter:
    """What the footer says about one column, over every row group. None where a row group says nothing."""

    minimum: float | None
    maximum: float | None
    null_count: int | None


@dataclass(frozen=True)
class TableFooter:
    """What the footer says about a whole file."""

    row_count: int
    columns: dict[str, ColumnFooter]


#: The range is numeric only: a column whose stats do not read as numbers (text, a timestamp)
#: has none. A row group without stats leaves the column's answer unknown rather than partial --
#: the smallest of the row groups that have one is not the smallest of the table.
_COLUMN_FOOTER_SQL = """
SELECT
    path_in_schema,
    CASE WHEN count(TRY_CAST(stats_min_value AS DOUBLE)) = count(*) THEN min(TRY_CAST(stats_min_value AS DOUBLE)) END,
    CASE WHEN count(TRY_CAST(stats_max_value AS DOUBLE)) = count(*) THEN max(TRY_CAST(stats_max_value AS DOUBLE)) END,
    CASE WHEN count(stats_null_count) = count(*) THEN sum(stats_null_count) END
FROM parquet_metadata($location)
GROUP BY path_in_schema;
"""


def read_footer(location: str) -> TableFooter:
    """Read a parquet's row count and per-column range and null count off its footer.

    Args:
        location: An ``s3://`` URL the datalayer's DuckDB can read, or a local path.
    """
    from arkitekt_datalayer.duck import get_current_duck

    # Bound to a name: the relation needs its connection alive (see `Datalayer.get_parquet_schema`).
    duck = get_current_duck()
    rows = duck.sql(
        "SELECT sum(num_rows) FROM parquet_file_metadata($location);",
        params={"location": location},
    ).fetchall()
    row_count = int(rows[0][0] or 0)
    columns = {
        str(name): ColumnFooter(
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
            null_count=int(nulls) if nulls is not None else None,
        )
        for name, minimum, maximum, nulls in duck.sql(
            _COLUMN_FOOTER_SQL, params={"location": location}
        ).fetchall()
    }
    return TableFooter(row_count=row_count, columns=columns)


def write_footer(table: "models.TableDataset", footer: TableFooter) -> None:
    """Store a footer's facts on the table and its columns.

    `update()`, not `save()`: the facts are the file's, not an edit of the table, so they write
    no history row and leave the stored descriptors alone.
    """
    with transaction.atomic():
        models.TableDataset.objects.filter(pk=table.pk).update(
            row_count=footer.row_count
        )
        for column in table.columns.all():
            facts = footer.columns.get(column.name)
            if facts is None:
                continue
            models.Column.objects.filter(pk=column.pk).update(
                value_min=facts.minimum,
                value_max=facts.maximum,
                null_count=facts.null_count,
            )
    table.row_count = footer.row_count


def _object_is_there(layer: "Datalayer", key: str) -> bool:
    """Whether the parquet's object can be reached, asked in one request that is not retried.

    Asked before DuckDB is: DuckDB (and the datalayer's own client) retry an unreachable endpoint
    or a missing object with backoff, which turns a store whose bytes are gone -- or an object
    store that is down -- into seconds of waiting inside a mutation that does not need the answer.
    """
    config = layer.config
    client = boto3.client(
        "s3",
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        aws_session_token=config.session_token or None,
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 1, "mode": "standard"},
            connect_timeout=2,
            read_timeout=5,
        ),
    )
    try:
        client.head_object(
            Bucket=layer.get_bucket_config("parquet").bucket,
            Key=layer.build_object_key("parquet", key),
        )
    except Exception as error:  # noqa: BLE001 - botocore raises ClientError for a 404 and its own errors for an unreachable endpoint
        logger.info(
            "Not reading the parquet footer of %s: its object cannot be reached (%s)",
            key,
            error,
        )
        return False
    return True


def record_footer(table: "models.TableDataset", location: str | None = None) -> bool:
    """Read and store a table's footer facts; False, logged in one line, when its parquet cannot be read.

    Never raises: a table whose footer cannot be read is a table without a row count, which
    every reader already handles, and `record_table_stats` tries again.
    """
    if location is None:
        from arkitekt_datalayer.datalayer import Datalayer

        layer = Datalayer()
        if not _object_is_there(layer, table.store.key):
            return False
        location = layer.parquet_source(table.store)
    try:
        footer = read_footer(location)
    except Exception as error:  # noqa: BLE001 - DuckDB raises its own IO and parser errors, all of them "not readable now"
        logger.info(
            "Could not read the parquet footer of table %s (%s): %s",
            table.pk,
            location,
            error,
        )
        return False
    write_footer(table, footer)
    return True

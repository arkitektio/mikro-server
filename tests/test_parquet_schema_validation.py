"""How a parquet store learns what columns its file holds.

A parquet store used to come away from its own upload no wiser: `fill_info` set `path` and
`populated` and read nothing. The cost was paid a layer up -- a caller declared every column's
name and DuckDB type by hand, and `createTableDataset` compared only the *names*, so the
declared type could be neither right nor wrong. Now the store DESCRIBEs itself at
`finishParquetUpload`, which is the one moment the bytes are known to be reachable.

The DESCRIBE lives on `Datalayer`, beside `get_zarr_metadata` and `get_sparse_metadata`, rather
than in `core.logic.tables` where it started. It had to move: `datalayer` may not import `core`
(`test_architecture.py`), and a store that has to ask core how to describe itself would be the
only exception -- for nothing, since the DuckDB facade it needs never depended on core either.

The tests stay deliberately split, for the reason they always were. The facade runs against a
**real** DuckDB connection, because a stub asserting `duck.sql` was called would have passed on
the original bug (`columns_for_store` was written against a method the class never grew). The
store-to-SQL wiring is stubbed, because DuckDB reads S3 over its own HTTP client, which `moto`
-- a botocore patch -- does not intercept.
"""

import pytest

from core.logic import tables as tables_logic
from datalayer import base_models
from datalayer.datalayer import Datalayer
from datalayer.duck import DuckLayer


def test_the_duck_layer_offers_the_query_method_its_callers_use():
    """A real query through the facade, not an assertion that a mock was called.

    `get_parquet_schema` calls `duck.sql(...)`. That the class *has* `connection` is not the
    contract its callers were written to, and the gap between the two was an exception on every
    schema validation.
    """
    layer = DuckLayer()

    assert layer.sql("SELECT 42 AS answer;").fetchall() == [(42,)]


def test_the_facade_runs_on_the_s3_configured_connection():
    """The point of the facade: the secret setup stays an implementation detail.

    A caller reaching through `.connection` itself would work too, but then every call site
    owns the knowledge that a connection needs configuring before it can see S3.
    """
    layer = DuckLayer()

    assert layer.sql("SELECT 1;").fetchall() == [(1,)]
    assert layer.connection is layer.connection, "one configured connection per layer, not one per query"


class _FakeRelation:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDuck:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def sql(self, query: str):
        self._captured.append(query)
        return _FakeRelation(self._rows)


def _stub_duck(monkeypatch, rows, captured):
    """Point `Datalayer.get_parquet_schema` at a fake DuckDB, wherever it imports it from."""
    import datalayer.duck as duck_module

    monkeypatch.setattr(duck_module, "get_current_duck", lambda: _FakeDuck(rows, captured))


def test_a_store_is_described_rather_than_read(monkeypatch):
    """The schema is a DESCRIBE over the parquet, never a scan of its values.

    A table dataset can be very large and only its column names and types are wanted, so
    reading rows to learn the schema would make registration cost the size of the data. This is
    the same line `get_zarr_metadata` and `get_sparse_metadata` hold.
    """
    captured: list[str] = []
    _stub_duck(monkeypatch, [("object_id", "BIGINT", "YES", None, None, None)], captured)

    class FakeStore:
        key = "abc123"

    layer = Datalayer()
    monkeypatch.setattr(Datalayer, "parquet_source", lambda self, store: "s3://tables/abc123")

    assert layer.get_parquet_schema(FakeStore()) == [base_models.ParquetColumn(name="object_id", type="BIGINT", nullable=True)]

    (query,) = captured
    assert "DESCRIBE SELECT * FROM read_parquet('s3://tables/abc123')" in query
    assert "LIMIT" not in query.upper(), "a DESCRIBE needs no row limit -- it reads no rows"


def test_the_nullable_flag_is_read_off_the_describe(monkeypatch):
    """DESCRIBE reports nullability as the strings YES/NO, not as a boolean."""
    _stub_duck(monkeypatch, [("a", "BIGINT", "YES", None, None, None), ("b", "DOUBLE", "NO", None, None, None)], [])
    monkeypatch.setattr(Datalayer, "parquet_source", lambda self, store: "s3://tables/abc123")

    columns = Datalayer().get_parquet_schema(object())

    assert [column.nullable for column in columns] == [True, False]


def test_the_file_order_of_the_columns_is_kept(monkeypatch):
    """File order is the order a `Column` row's `order` will be written in, so it must survive."""
    rows = [(name, "BIGINT", "YES", None, None, None) for name in ("z", "a", "m")]
    _stub_duck(monkeypatch, rows, [])
    monkeypatch.setattr(Datalayer, "parquet_source", lambda self, store: "s3://tables/abc123")

    assert [column.name for column in Datalayer().get_parquet_schema(object())] == ["z", "a", "m"]


@pytest.mark.django_db
def test_fill_info_records_what_the_file_declared(monkeypatch, authenticated_context):
    """The whole point of the move: after a finish, the schema is on the row.

    Nothing on the create path goes back to S3 for it -- which is why `create_table_dataset`
    now calls `fill_info` only when the finish never ran.
    """
    from datalayer import models

    monkeypatch.setattr(
        Datalayer,
        "get_parquet_schema",
        lambda self, store: [base_models.ParquetColumn(name="object_id", type="BIGINT", nullable=False)],
    )
    monkeypatch.setattr(Datalayer, "build_store_path", lambda self, bucket, key: f"s3://{bucket}/{key}")

    store = models.ParquetStore.objects.create(organization=authenticated_context.request.organization, key="abc123", bucket="parquet")
    store.fill_info()
    store.refresh_from_db()

    assert store.populated is True
    assert store.columns == [{"name": "object_id", "type": "BIGINT", "nullable": False}]


@pytest.mark.django_db
def test_columns_for_store_reads_the_row_rather_than_the_file(monkeypatch, authenticated_context):
    """A described store is never re-described; the answer is already on it."""
    from datalayer import models

    def _refuse(self, store):
        raise AssertionError("a store that recorded its schema must not be described again")

    monkeypatch.setattr(Datalayer, "get_parquet_schema", _refuse)

    store = models.ParquetStore.objects.create(
        organization=authenticated_context.request.organization,
        key="abc123",
        bucket="parquet",
        populated=True,
        columns=[{"name": "object_id", "type": "BIGINT", "nullable": False}],
    )

    assert tables_logic.columns_for_store(store) == [base_models.ParquetColumn(name="object_id", type="BIGINT", nullable=False)]


@pytest.mark.django_db
def test_a_store_written_before_the_field_existed_falls_back_to_describing(monkeypatch, authenticated_context):
    """`columns` is null on every store finished before this field, and those still work."""
    from datalayer import models

    monkeypatch.setattr(
        Datalayer,
        "get_parquet_schema",
        lambda self, store: [base_models.ParquetColumn(name="legacy", type="VARCHAR", nullable=True)],
    )

    store = models.ParquetStore.objects.create(organization=authenticated_context.request.organization, key="old", bucket="parquet", populated=True)

    assert [column.name for column in tables_logic.columns_for_store(store)] == ["legacy"]


@pytest.mark.django_db
def test_the_parquet_source_names_the_configured_bucket(settings):
    """The URL DuckDB is handed is built from the datalayer's own bucket configuration."""

    class FakeStore:
        key = "abc123"

    assert tables_logic.parquet_source_for_store(FakeStore()).startswith("s3://")
    assert tables_logic.parquet_source_for_store(FakeStore()).endswith("/abc123")


def test_the_connection_outlives_the_relation_it_returns(monkeypatch):
    """`sql()` returns a lazy relation, so the layer that owns the connection must stay alive.

    Written after the real failure: `get_current_duck().sql(...).fetchall()` on one line left
    the layer's only reference in a temporary, so CPython collected it -- and its duckdb
    connection with it -- as soon as the relation was built. Every one of 24 live stores then
    failed with "Connection has already been closed", while every test here passed, because a
    fake relation holds no connection to lose.

    So this fake loses one on purpose: `fetchall` refuses once its owner has been collected,
    which is exactly the sequence the inlined form produces.
    """

    class ClosingRelation:
        def __init__(self, owner, rows):
            self._owner = owner
            self._rows = rows

        def fetchall(self):
            if self._owner["closed"]:
                raise RuntimeError("Connection has already been closed")
            return self._rows

    class ClosingDuck:
        def __init__(self, state):
            self._state = state

        def __del__(self):
            self._state["closed"] = True

        def sql(self, query: str):
            # The relation deliberately does NOT reference the layer, exactly as duckdb's does
            # not keep its connection alive from Python's side.
            return ClosingRelation(self._state, [("object_id", "BIGINT", "YES", None, None, None)])

    import datalayer.duck as duck_module

    monkeypatch.setattr(duck_module, "get_current_duck", lambda: ClosingDuck({"closed": False}))
    monkeypatch.setattr(Datalayer, "parquet_source", lambda self, store: "s3://tables/abc123")

    assert Datalayer().get_parquet_schema(object()) == [base_models.ParquetColumn(name="object_id", type="BIGINT", nullable=True)]

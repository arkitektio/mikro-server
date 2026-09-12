"""The SQL builder: pure over a lookup step, in every shape a step arrives in, and run for real.

No Django here, deliberately. The module under test imports only the standard library so it can
be copied into the client unchanged (``tests/test_architecture.py`` pins that), and these tests
feed it dicts and namespaces rather than model rows for the same reason: a builder that needed
a ``Column`` row to produce a string would be one the client could not carry.
"""

from types import SimpleNamespace

import duckdb
import pytest

from core.logic import plan_sql


def _key(held: str, column: str) -> dict:
    return {"axis": held, "column": {"name": column}}


def test_the_builder_quotes_identifiers_and_never_interpolates_values() -> None:
    """The injection regression test: a hostile column name is a quoted identifier, nothing more."""
    lookup = {"keyColumns": [_key("i", "i")], "attributes": [{"name": 'a"; DROP TABLE rows; --'}]}

    sql = plan_sql.build_lookup_sql(lookup)

    assert sql == 'SELECT "a""; DROP TABLE rows; --" FROM read_parquet(?) WHERE "i" = ?', "the embedded quote is doubled, so the name cannot close its own identifier"
    assert sql.count("?") == 2, "one placeholder for the parquet path, one per key -- values never appear in the string"


def test_the_builder_selects_keys_when_a_table_has_only_coordinates() -> None:
    """A table whose every column is a coordinate still answers: the row exists."""
    lookup = {"keyColumns": [_key("t", "t"), _key("i", "i")], "attributes": []}

    assert plan_sql.build_lookup_sql(lookup) == 'SELECT "t", "i" FROM read_parquet(?) WHERE "t" = ? AND "i" = ?'


def test_the_builder_reads_a_graphql_shaped_dict_and_a_dataclass_alike() -> None:
    """camelCase dict, snake_case dict, attribute objects: one statement, whichever a client holds."""
    as_camel = {"keyColumns": [_key("t", "t"), _key("i", "i")], "attributes": [{"name": "area"}]}
    as_snake = {"key_columns": [_key("t", "t"), _key("i", "i")], "attributes": [{"name": "area"}]}
    as_object = SimpleNamespace(
        key_columns=[SimpleNamespace(axis="t", column=SimpleNamespace(name="t")), SimpleNamespace(axis="i", column=SimpleNamespace(name="i"))],
        attributes=[SimpleNamespace(name="area")],
    )

    expected = 'SELECT "area" FROM read_parquet(?) WHERE "t" = ? AND "i" = ?'
    assert plan_sql.build_lookup_sql(as_camel) == expected
    assert plan_sql.build_lookup_sql(as_snake) == expected
    assert plan_sql.build_lookup_sql(as_object) == expected


def test_a_many_lookup_binds_a_set_and_returns_its_keys() -> None:
    """After a sparse step the worker holds positions, plural: bind a list, and get the key back per row."""
    lookup = {"keyColumns": [_key("feature", "feature_id")], "attributes": [{"name": "symbol"}]}

    sql = plan_sql.build_lookup_sql(lookup, cardinality="MANY")

    assert sql == 'SELECT "feature_id", "symbol" FROM read_parquet(?) WHERE "feature_id" IN (SELECT unnest(?))'


def test_a_lookup_without_keys_is_refused() -> None:
    """No WHERE is a full read, which is never what a plan means."""
    with pytest.raises(ValueError, match="at least one key column"):
        plan_sql.build_lookup_sql({"keyColumns": [], "attributes": [{"name": "area"}]})


def test_the_statement_runs_against_a_real_parquet(tmp_path) -> None:
    """Both shapes execute on DuckDB with the documented bind order: the path first, then the keys."""
    path = str(tmp_path / "objects.parquet")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM (VALUES (0, 7, 1.5::DOUBLE), (0, 8, 2.5::DOUBLE), (1, 7, 3.5::DOUBLE)) AS t(t, i, area)) TO '{path}' (FORMAT PARQUET)")

    lookup = {"keyColumns": [_key("t", "t"), _key("i", "i")], "attributes": [{"name": "area"}]}

    one = con.execute(plan_sql.build_lookup_sql(lookup), [path, 1, 7]).fetchall()
    assert one == [(3.5,)]

    many = con.execute(plan_sql.build_lookup_sql(lookup, cardinality="MANY"), [path, [0, 1], [7]]).fetchall()
    assert sorted(many) == [(0, 7, 1.5), (1, 7, 3.5)], "the keys come back with each row, so a row says which value it answers"

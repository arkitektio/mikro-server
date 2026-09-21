"""A Parquet the viewer cannot decode is refused at the finish, not discovered from a blank view.

The server used to validate no codec at all -- a DESCRIBE reads the schema, never the
compression -- and the Python client carried the readable sets instead. That was the wrong
side: which frontend a deployment serves is the server's to know. The sets now live in
`datalayer.codecs`, the footer is read through the same S3-configured DuckDB the DESCRIBE
uses, and the check runs in `fill_info`, the one moment the bytes are known to be reachable.

Split the way `test_parquet_schema_validation.py` is split, for the same reason: the footer
read runs against a **real** DuckDB over a file written with a real codec, and the store-level
wiring is stubbed because DuckDB reads S3 over its own HTTP client, which `moto` does not
intercept.
"""

from pathlib import Path

import pytest
from pytest_django.fixtures import SettingsWrapper

from datalayer import codecs
from datalayer.datalayer import Datalayer
from datalayer.duck import DuckLayer
from datalayer.models import ParquetStore


def test_a_table_codec_outside_duckdbs_reach_is_refused_and_the_message_says_what_to_write() -> None:
    """LZO is the one codec DuckDB does not decode; the message names the reader and the fix."""
    with pytest.raises(ValueError) as excinfo:
        codecs.refuse_unreadable_codecs({"LZO"}, codecs.TABLE_CODECS, where="The parquet at s3://t/x", reader=codecs.TABLE_READER)
    message = str(excinfo.value)
    assert "LZO" in message and "DuckDB-WASM" in message and "ZSTD" in message


def test_the_part_set_is_the_narrow_one() -> None:
    """GZIP is fine for a table and fatal for a part: the two readers differ, so the sets do."""
    codecs.refuse_unreadable_codecs({"GZIP"}, codecs.TABLE_CODECS, where="a table", reader=codecs.TABLE_READER)
    with pytest.raises(ValueError, match="GZIP"):
        codecs.refuse_unreadable_codecs({"GZIP"}, codecs.PART_CODECS, where="a part", reader=codecs.PART_READER)
    for codec in ("UNCOMPRESSED", "SNAPPY", "ZSTD", "zstd"):
        codecs.refuse_unreadable_codecs({codec}, codecs.PART_CODECS, where="a part", reader=codecs.PART_READER)


def test_the_codecs_are_read_off_a_real_footer(tmp_path: Path, settings: SettingsWrapper) -> None:
    """Written by DuckDB with GZIP, read back by DuckDB as GZIP -- no pyarrow on either side."""
    settings.DATALAYER = {"access_key": "k", "secret_key": "s", "parquet": {"bucket": "tables"}}
    target = tmp_path / "gz.parquet"
    DuckLayer().connection.execute(f"COPY (SELECT 1 AS a, 'x' AS b) TO '{target}' (FORMAT PARQUET, COMPRESSION GZIP);")

    assert Datalayer().parquet_codecs_of(str(target)) == {"GZIP"}


class _FakeRelation:
    """The rows a stubbed query returns."""

    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        """Return the canned rows."""
        return self._rows


class _FakeDuck:
    """A DuckDB facade that records what it was asked and answers with canned rows."""

    def __init__(self, rows: list[tuple[str]], captured: list[str]) -> None:
        self._rows = rows
        self._captured = captured

    def sql(self, query: str) -> _FakeRelation:
        """Record the query and return the canned relation."""
        self._captured.append(query)
        return _FakeRelation(self._rows)


@pytest.fixture()
def layer(settings: SettingsWrapper) -> Datalayer:
    """A datalayer whose bucket config names the buckets the probes address."""
    settings.DATALAYER = {"access_key": "k", "secret_key": "s", "parquet": {"bucket": "tables"}, "fabriks": {"bucket": "meshes"}}
    return Datalayer()


def _stub_duck(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str]]) -> list[str]:
    """Point the codec probe at a fake DuckDB and hand back the queries it sends."""
    import datalayer.duck as duck_module

    captured: list[str] = []
    monkeypatch.setattr(duck_module, "get_current_duck", lambda: _FakeDuck(rows, captured))
    return captured


def test_a_table_is_probed_at_its_own_location(layer: Datalayer, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe asks the same s3:// URL the DESCRIBE reads, and only the footer."""
    captured = _stub_duck(monkeypatch, [("ZSTD",), ("SNAPPY",)])
    store = ParquetStore(key="abc.parquet", bucket="parquet")

    layer.refuse_unreadable_table(store)

    assert captured == ["SELECT DISTINCT compression FROM parquet_metadata('s3://tables/abc.parquet');"]


def test_an_unreadable_table_is_refused(layer: Datalayer, monkeypatch: pytest.MonkeyPatch) -> None:
    """A table the table viewer cannot decode never becomes populated."""
    _stub_duck(monkeypatch, [("LZO",)])
    with pytest.raises(ValueError, match="LZO"):
        layer.refuse_unreadable_table(ParquetStore(key="abc.parquet", bucket="parquet"))


def test_a_collection_is_probed_through_one_part_under_its_prefix(layer: Datalayer, monkeypatch: pytest.MonkeyPatch) -> None:
    """One footer read of the cell catalog, addressed under the store's prefix."""
    captured = _stub_duck(monkeypatch, [("ZSTD",)])

    layer.refuse_unreadable_collection_parts("s3://meshes/abc123", "catalog/cells.parquet", kind="mesh")

    assert captured == ["SELECT DISTINCT compression FROM parquet_metadata('s3://meshes/abc123/catalog/cells.parquet');"]


def test_a_collection_whose_parts_hyparquet_cannot_read_is_refused(layer: Datalayer, monkeypatch: pytest.MonkeyPatch) -> None:
    """A GZIP part would upload, verify and draw nothing; it is refused at the finish instead."""
    _stub_duck(monkeypatch, [("GZIP",)])
    with pytest.raises(ValueError, match="mesh collection at s3://meshes/abc123") as excinfo:
        layer.refuse_unreadable_collection_parts("s3://meshes/abc123", "catalog/cells.parquet", kind="mesh")
    assert "hyparquet" in str(excinfo.value)


def test_the_cell_catalog_is_the_part_a_finish_probes() -> None:
    """The manifest may name it or not; either way there is exactly one answer."""
    from datalayer import fabriks as fabriks_format
    from datalayer import konnektion as konnektion_format

    assert fabriks_format.cells_path_of({}) == "catalog/cells.parquet"
    assert fabriks_format.cells_path_of({"cells": {"path": "c/cells.parquet", "bytes": 10}}) == "c/cells.parquet"
    assert fabriks_format.cells_path_of({"cells": "c/cells.parquet"}) == "c/cells.parquet"
    assert konnektion_format.cells_path_of({}) == "catalog/cells.parquet"

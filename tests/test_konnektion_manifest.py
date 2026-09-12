"""Reading a konnektion store's manifest, and refusing a prefix that has not got one.

The graph twin of ``test_fabriks_manifest.py``, and the same argument: a prefix has no atomic
"upload finished" flag, so a konnektion writer lands ``konnektion.json`` **last** and the server
refuses a prefix without it.

What is worth testing *here* rather than there is what a network manifest declares that a mesh
one does not. Two things, and both are refusals nothing downstream could make:

* ``encoding.edges`` gives the index arity. A segment list read as a triangle list raises
  nothing anywhere -- the length divides, the indices are in range, and the picture is a
  plausible, wrong graph -- so the key is required and its vocabulary is closed.
* ``encoding.pruning`` / ``simplification`` say whether any level coarsened. For traced data
  the honest answer is usually ``NONE``, and a format that could not say so would leave a
  reader unable to tell "not coarsened" from "not stated".

These tests stub S3 rather than standing one up: what is being tested is the reading and the
refusing, and the bytes are the least interesting part of that.
"""

import io
import json

import pytest

from datalayer import konnektion as konnektion_format
from datalayer.datalayer import Datalayer
from datalayer.models import KonnektionStore

#: A manifest in the shape konnektion actually writes. Two things about it are deliberate and
#: are what a mesh fixture cannot show:
#:
#: ``levels: 1`` with ``pruning``/``simplification`` both ``NONE`` -- the common case for a
#: traced arbor, where the collection carries the data exactly as it was given. A server that
#: treated a single-level collection as suspicious would reject most real input.
#:
#: ``edges: UINT32_PAIRS`` -- the arity, stated, because nothing in the bytes carries it.
MANIFEST = {
    "specVersion": "1",
    "grid": {"cellSize": [128, 128, 64], "levels": 1, "sortKey": "MORTON"},
    "encoding": {
        "positions": "UINT16_QUANTIZED_PER_CELL",
        "edges": "UINT32_PAIRS",
        "nodeIds": "UINT64",
        "radii": "FLOAT32",
        "ghosts": "TRAILING_PER_OWNER_CELL",
        "codec": "NONE",
        "compression": "NONE",
        "pruning": "NONE",
        "simplification": "NONE",
    },
    "axes": ["x", "y", "z"],
    "attributes": [
        {"name": "strahler", "encoding": "FLOAT32", "semantics": "STRAHLER"},
        {"name": "tortuosity", "encoding": "FLOAT32", "semantics": None},
    ],
    "counts": {"objects": 2, "cells": 16, "levels": 1},
    "files": {
        "cells": {"path": "catalog/cells.parquet", "bytes": 9124, "rowGroups": 1},
        "objects": {"path": "catalog/objects.parquet", "bytes": 2048},
        "levels": {"0": [{"path": "level0/part-00000.parquet", "bytes": 84432, "rowGroups": 1}]},
    },
}

#: A ladder, so the coarsening declarations are exercised in the state where they mean something.
COARSENED = json.loads(json.dumps(MANIFEST))
COARSENED["grid"]["levels"] = 3
COARSENED["encoding"]["pruning"] = "STRAHLER"
COARSENED["encoding"]["simplification"] = "DOUGLAS_PEUCKER"


class _FakeS3:
    """Returns one body for any key, or raises -- whichever the test asked for."""

    def __init__(self, body: bytes | None) -> None:
        self.body = body
        self.requested: list[tuple[str, str]] = []

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 -- boto3's casing
        self.requested.append((Bucket, Key))
        if self.body is None:
            raise RuntimeError("NoSuchKey")
        return {"Body": io.BytesIO(self.body)}


@pytest.fixture()
def layer(settings) -> Datalayer:
    settings.DATALAYER = {"access_key": "k", "secret_key": "s", "konnektion": {"bucket": "networks"}}
    return Datalayer()


def _store() -> KonnektionStore:
    """An unsaved store row -- the manifest read touches S3, never the database."""
    return KonnektionStore(path="s3://networks/abc123", key="abc123", bucket="konnektion")


def _with_body(layer: Datalayer, body: bytes | None) -> _FakeS3:
    fake = _FakeS3(body)
    layer._s3 = fake  # type: ignore[assignment]
    return fake


def test_the_manifest_is_read_from_the_prefix_and_its_facts_come_back(layer: Datalayer):
    """One GET of one small object, at registration only."""
    fake = _with_body(layer, json.dumps(MANIFEST).encode())

    metadata = layer.get_konnektion_metadata(_store())

    assert fake.requested == [("networks", "abc123/konnektion.json")], "the manifest sits at the root of the prefix"
    assert metadata.spec_version == "1"
    assert metadata.grid["cellSize"] == [128, 128, 64]
    assert metadata.encoding["edges"] == "UINT32_PAIRS", "the arity, carried through unchanged"
    assert metadata.encoding["radii"] == "FLOAT32"
    assert metadata.axes == ["x", "y", "z"]


def test_declared_attributes_ride_the_whole_metadata_path(layer: Datalayer):
    """The attributes reach `KonnektionMetadata`, which is what `fill_info` stores.

    Not redundant with the parse tests below: the first live upload after attributes landed
    failed at exactly this seam -- `parse_manifest` knew the key and the metadata model did
    not, so `finishKonnektionUpload` raised `'KonnektionMetadata' object has no attribute
    'attributes'` after the tree was already written.
    """
    _with_body(layer, json.dumps(MANIFEST).encode())

    metadata = layer.get_konnektion_metadata(_store())

    assert metadata.attributes == [
        {"name": "strahler", "encoding": "FLOAT32", "semantics": "STRAHLER"},
        {"name": "tortuosity", "encoding": "FLOAT32", "semantics": None},
    ]

    # A manifest that predates the key reads as declaring none, never as an error.
    bare = {key: value for key, value in MANIFEST.items() if key != "attributes"}
    _with_body(layer, json.dumps(bare).encode())
    assert layer.get_konnektion_metadata(_store()).attributes == []


def test_a_single_level_collection_is_ordinary(layer: Datalayer):
    """`levels: 1` with nothing coarsened is the common case for traced data, not a defect."""
    _with_body(layer, json.dumps(MANIFEST).encode())

    metadata = layer.get_konnektion_metadata(_store())

    assert metadata.grid["levels"] == 1
    assert metadata.encoding["pruning"] == "NONE"
    assert metadata.encoding["simplification"] == "NONE"


def test_a_coarsened_collection_says_which_operations_ran(layer: Datalayer):
    """Two independent operations, so a level can do one and not the other."""
    _with_body(layer, json.dumps(COARSENED).encode())

    metadata = layer.get_konnektion_metadata(_store())

    assert metadata.encoding["pruning"] == "STRAHLER"
    assert metadata.encoding["simplification"] == "DOUGLAS_PEUCKER"


def test_a_prefix_with_no_manifest_is_refused_as_an_unfinished_upload(layer: Datalayer):
    """The completion marker doing its job."""
    _with_body(layer, None)

    with pytest.raises(FileNotFoundError) as excinfo:
        layer.get_konnektion_metadata(_store())

    assert "uploads the manifest last" in str(excinfo.value), "the message must name the likely cause, not just the missing key"


def test_a_half_written_manifest_is_refused(layer: Datalayer):
    """A truncated JSON body is the other shape of an interrupted write."""
    _with_body(layer, json.dumps(MANIFEST).encode()[:60])

    with pytest.raises(ValueError, match="not valid JSON"):
        layer.get_konnektion_metadata(_store())


def test_an_unsupported_spec_version_is_refused():
    """The version selects how every byte is read, so an unknown one is not guessed at."""
    raw = json.loads(json.dumps(MANIFEST))
    raw["specVersion"] = "2"

    with pytest.raises(konnektion_format.ManifestError, match="specVersion"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_an_encoding_missing_the_edge_arity_is_refused():
    """The key whose absence is least survivable, and which nothing downstream could catch."""
    raw = json.loads(json.dumps(MANIFEST))
    del raw["encoding"]["edges"]

    with pytest.raises(konnektion_format.ManifestError, match="edges"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_a_triangle_arity_is_refused():
    """A network collection is a segment list. Triangles belong in a fabriks store."""
    raw = json.loads(json.dumps(MANIFEST))
    raw["encoding"]["edges"] = "UINT32_TRIPLES"

    with pytest.raises(konnektion_format.ManifestError, match="edges"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_a_mesh_manifest_is_refused():
    """Handing this server a `fabriks.json` fails on the keys the two formats do not share."""
    mesh = {
        "specVersion": "1",
        "grid": {"cellSize": [128, 128, 64], "levels": 3, "sortKey": "MORTON"},
        "encoding": {
            "positions": "UINT16_QUANTIZED_PER_CELL",
            "indices": "UINT32",
            "codec": "MESHOPT",
            "compression": "NONE",
            "boundary": "LOCKED",
            "decimation": "QUARTER",
        },
    }

    with pytest.raises(konnektion_format.ManifestError, match="omits"):
        konnektion_format.parse_manifest(json.dumps(mesh).encode())


def test_a_coarsening_word_nobody_defined_is_refused():
    raw = json.loads(json.dumps(MANIFEST))
    raw["encoding"]["pruning"] = "AGGRESSIVE"

    with pytest.raises(konnektion_format.ManifestError, match="pruning"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_a_grid_with_no_levels_is_refused():
    raw = json.loads(json.dumps(MANIFEST))
    raw["grid"]["levels"] = 0

    with pytest.raises(konnektion_format.ManifestError, match="levels"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_axes_are_optional_but_checked_when_present():
    """Nothing in the format decodes through them, so absent is legitimate; malformed is not."""
    raw = json.loads(json.dumps(MANIFEST))
    del raw["axes"]
    assert konnektion_format.parse_manifest(json.dumps(raw).encode()).axes is None

    raw["axes"] = ["x", "y"]
    with pytest.raises(konnektion_format.ManifestError, match="three-dimensional"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())

    raw["axes"] = ["x", "x", "z"]
    with pytest.raises(konnektion_format.ManifestError, match="each axis once"):
        konnektion_format.parse_manifest(json.dumps(raw).encode())


def test_the_manifest_locates_the_catalogs_and_the_levels():
    """A file list is a convenience for a reader that would otherwise list the prefix."""
    manifest = konnektion_format.parse_manifest(json.dumps(MANIFEST).encode())

    assert manifest.cells_path == "catalog/cells.parquet"
    assert manifest.objects_path == "catalog/objects.parquet"
    assert manifest.level_paths(0) == ["level0/part-00000.parquet"]
    assert manifest.level_paths(2) is None, "a level the manifest does not name sends a reader to the listing"


def test_a_manifest_that_names_no_files_falls_back_to_the_format_s_own_paths():
    raw = json.loads(json.dumps(MANIFEST))
    del raw["files"]
    manifest = konnektion_format.parse_manifest(json.dumps(raw).encode())

    assert manifest.cells_path == konnektion_format.CELL_CATALOG_PATH
    assert manifest.level_paths(0) is None

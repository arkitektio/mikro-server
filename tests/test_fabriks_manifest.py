"""Reading a fabriks store's manifest, and refusing a prefix that has not got one.

A prefix has no atomic "upload finished" flag. That is the one thing an object key gives you
for free and a directory does not: `PutObject` either happened or it did not, whereas a tree
is a sequence of writes that can stop anywhere. So a fabriks writer lands `fabriks.json` **last**
and the server refuses a prefix without it — which turns "this collection is half written" from
something a renderer discovers into something registration rejects.

These tests stub S3 rather than standing one up: what is being tested is the reading and the
refusing, and the bytes are the least interesting part of that. The live path is covered by the
MinIO probe.
"""

import io
import json

import pytest

from datalayer import fabriks as fabriks_format
from datalayer.datalayer import Datalayer
from datalayer.models import FabriksStore

#: A manifest in the shape fabriks actually writes: spec 1, and `files` entries carrying the
#: byte length a reader needs to range-read a part it cannot stat.
#:
#: The level directories are `level0`, not the Hive-style `level=0` this once was. Every name in
#: a fabriks tree is spelled to be *signable* -- SigV4 percent-encodes a path against RFC 3986's
#: unreserved set before signing, and `=` is a sub-delimiter that one signer sends as `%3D` and
#: another leaves bare, which is two strings to sign for one object. The server never builds these
#: paths (it reads what the manifest declares, and hands out a prefix-wide grant so the client
#: signs), so this fixture is where the layout is written down and where a Hive-shaped "fix"
#: would have to come back through.
MANIFEST = {
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
    "axes": ["z", "y", "x"],
    "counts": {"objects": 41822, "cellsPerLevel": [21044, 5310, 1388]},
    "files": {
        "cells": {"path": "catalog/cells.parquet", "bytes": 91244, "rowGroups": 1},
        "objects": {"path": "catalog/objects.parquet", "bytes": 20481},
        "levels": {
            "0": [{"path": "level0/part-00000.parquet", "bytes": 8443210, "rowGroups": 4}],
            "1": [{"path": "level1/part-00000.parquet", "bytes": 2110992}],
            "2": [{"path": "level2/part-00000.parquet", "bytes": 530771}],
        },
    },
}


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
    settings.DATALAYER = {"access_key": "k", "secret_key": "s", "fabriks": {"bucket": "meshes"}}
    return Datalayer()


def _store() -> FabriksStore:
    """An unsaved store row -- the manifest read touches S3, never the database."""
    return FabriksStore(path="s3://meshes/abc123", key="abc123", bucket="fabriks")


def _with_body(layer: Datalayer, body: bytes | None) -> _FakeS3:
    fake = _FakeS3(body)
    layer._s3 = fake  # type: ignore[assignment]
    return fake


def test_the_manifest_is_read_from_the_prefix_and_its_facts_come_back(layer: Datalayer):
    """One GET of one small object, at registration only -- the zarr.json move for meshes."""
    fake = _with_body(layer, json.dumps(MANIFEST).encode())

    metadata = layer.get_fabriks_metadata(_store())

    assert fake.requested == [("meshes", "abc123/fabriks.json")], "the manifest sits at the root of the prefix"
    assert metadata.spec_version == "1"
    assert metadata.grid["cellSize"] == [128, 128, 64], "anisotropic, in x/y/z -- carried through unchanged"
    assert metadata.encoding["codec"] == "MESHOPT"
    assert metadata.encoding["compression"] == "NONE", "a writer's real choice, never defaulted on its behalf"
    assert metadata.axes == ["z", "y", "x"]


def test_a_prefix_with_no_manifest_is_refused_as_an_unfinished_upload(layer: Datalayer):
    """The completion marker doing its job.

    This is the whole reason the manifest is written last. Without this refusal a killed writer
    leaves a prefix that registers cleanly and fails later, on a reader, with no way to tell an
    interrupted upload from a corrupt one.
    """
    _with_body(layer, None)

    with pytest.raises(FileNotFoundError) as excinfo:
        layer.get_fabriks_metadata(_store())

    assert "uploads the manifest last" in str(excinfo.value), "the message must name the likely cause, not just the missing key"


def test_a_half_written_manifest_is_refused(layer: Datalayer):
    """A truncated JSON body is the other shape of an interrupted write."""
    _with_body(layer, json.dumps(MANIFEST).encode()[:60])

    with pytest.raises(ValueError, match="not valid JSON"):
        layer.get_fabriks_metadata(_store())


def test_the_file_entries_are_read_in_both_shapes(layer: Datalayer):
    """fabriks writes objects carrying a byte length; a hand-written manifest may write a string.

    The server needs only the path -- it addresses files, it never seeks inside them -- but a
    reader does need the length, because a store is asked for get/put/list and nothing else, so
    there is no `head` to call and a Parquet footer lives at the end. Refusing the richer form
    would refuse every manifest fabriks produces.
    """
    _with_body(layer, json.dumps(MANIFEST).encode())
    manifest = fabriks_format.parse_manifest(json.dumps(MANIFEST).encode())

    assert manifest.cells_path == "catalog/cells.parquet"
    assert manifest.level_paths(0) == ["level0/part-00000.parquet"]

    bare = {**MANIFEST, "files": {"cells": "catalog/cells.parquet", "levels": {"0": ["level0/part-00000.parquet"]}}}
    plain = fabriks_format.parse_manifest(json.dumps(bare).encode())
    assert plain.cells_path == "catalog/cells.parquet"
    assert plain.level_paths(0) == ["level0/part-00000.parquet"]
    assert plain.objects_path == "catalog/objects.parquet", "an unnamed catalog falls back to where the format puts it"


def test_a_manifest_that_names_no_parts_says_so_rather_than_nothing():
    """`None` and `[]` are different answers, and only one of them sends a reader to the listing.

    The manifest's file list is a claim, not authority -- a convenience for a reader that would
    otherwise list the prefix. So "this manifest does not say" has to be distinguishable from
    "this level is empty", or a reader silently draws nothing.
    """
    without = fabriks_format.parse_manifest(json.dumps({**MANIFEST, "files": {}}).encode())
    assert without.level_paths(0) is None
    assert without.cells_path == "catalog/cells.parquet", "the format's own layout is the fallback"


def test_the_vocabulary_matches_the_format_rather_than_the_servers_older_rules():
    """Two values the earlier server rules would have refused, and the format defines both.

    `decimation` gained HALF/EIGHTH/CUSTOM because a writer that reduced by half and declared
    QUARTER would be making a claim nothing could check -- naming what was done beats one name
    that is sometimes a lie. And `boundary: OPEN` is a rendering trade-off, not a malformed
    file; refusing it here would reject collections fabriks legitimately produces.
    """
    for decimation in ("QUARTER", "HALF", "EIGHTH", "CUSTOM"):
        manifest = fabriks_format.parse_manifest(json.dumps({**MANIFEST, "encoding": {**MANIFEST["encoding"], "decimation": decimation}}).encode())
        assert manifest.encoding["decimation"] == decimation

    opened = fabriks_format.parse_manifest(json.dumps({**MANIFEST, "encoding": {**MANIFEST["encoding"], "boundary": "OPEN"}}).encode())
    assert opened.encoding["boundary"] == "OPEN"

    with pytest.raises(fabriks_format.ManifestError, match="does not define"):
        fabriks_format.parse_manifest(json.dumps({**MANIFEST, "encoding": {**MANIFEST["encoding"], "codec": "DRACO"}}).encode())


def test_axes_are_optional_and_checked_only_when_stated():
    """Nothing in the format decodes through `axes`, so absent is legitimate -- wrong is not."""
    without = fabriks_format.parse_manifest(json.dumps({key: value for key, value in MANIFEST.items() if key != "axes"}).encode())
    assert without.axes is None, "a collection that never claimed an axis order is readable in full"

    with pytest.raises(fabriks_format.ManifestError, match="names 3 axes"):
        fabriks_format.parse_manifest(json.dumps({**MANIFEST, "axes": ["z", "y"]}).encode())

    with pytest.raises(fabriks_format.ManifestError, match="each axis once"):
        fabriks_format.parse_manifest(json.dumps({**MANIFEST, "axes": ["z", "z", "x"]}).encode())


def test_an_unreadable_format_version_is_refused(layer: Datalayer):
    """The version selects how every byte in the prefix is read.

    Registering a store whose format this server does not know would mean recording a
    collection nothing can decode -- so it is refused rather than accepted and ignored, which
    is the failure the mesh contract has been fighting since it had more than one version.

    A future fabriks 2 is the case this protects: it will mean something specific, and reading
    its bytes as though they were 1 is exactly the silent wrongness the refusal exists for.
    """
    _with_body(layer, json.dumps({**MANIFEST, "specVersion": "2"}).encode())

    with pytest.raises(ValueError, match="cannot read"):
        layer.get_fabriks_metadata(_store())


def test_a_manifest_missing_grid_or_encoding_is_refused(layer: Datalayer):
    """Nothing else in the prefix states them, so a manifest without them is undecodable."""
    for dropped in ("grid", "encoding"):
        _with_body(layer, json.dumps({key: value for key, value in MANIFEST.items() if key != dropped}).encode())

        with pytest.raises(ValueError, match="must carry a `grid` and an `encoding`"):
            layer.get_fabriks_metadata(_store())

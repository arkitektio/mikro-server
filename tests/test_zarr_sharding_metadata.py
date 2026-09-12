"""The shard/chunk split a sharded zarr writes, and the layouts the finish refuses.

Zarr v3 sharding hides the readable unit: ``chunk_grid.configuration.chunk_shape`` is the
*shard* (storage-object) shape, and the inner chunk shape — the thing a reader can actually
decode — lives inside the ``sharding_indexed`` codec's configuration. Recording the grid
shape as ``chunks`` would therefore silently misreport every sharded store, which is why
``ZarrMetadata`` splits the two (``chunks`` = inner, ``shards`` = outer, null when unsharded).

The refusals mirror the frontend reader's contract exactly: it unwraps ``sharding_indexed``
only when it is the sole top-level codec, requires shard/inner ranks to match and the shard
to be an exact per-axis multiple, and knows only two index locations. A store violating any
of these would upload fine and then be unreadable, so the finish is where they fail.
"""

import copy
import json

import pytest
from authentikate.models import Organization
from datalayer import base_models
from datalayer.models import ZarrStore

# See test_upload_accounting for why the fixture is shared rather than copied.
from tests.test_store_purging import buckets  # noqa: F401


#: A sharded zarr v3 manifest as zarr-python writes it: the chunk grid carries the shard
#: shape, the sole top-level codec is `sharding_indexed`, and the inner chunk shape sits in
#: its configuration alongside the declared (crc32c-checksummed) index codecs.
SHARDED_MANIFEST = {
    "zarr_format": 3,
    "node_type": "array",
    "shape": [1, 1, 16, 4096, 4096],
    "data_type": "uint16",
    "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [1, 1, 16, 2048, 2048]}},
    "chunk_key_encoding": {"name": "default"},
    "fill_value": 0,
    "codecs": [
        {
            "name": "sharding_indexed",
            "configuration": {
                "chunk_shape": [1, 1, 4, 512, 512],
                "codecs": [
                    {"name": "bytes", "configuration": {"endian": "little"}},
                    {"name": "zstd", "configuration": {"level": 0, "checksum": False}},
                ],
                "index_codecs": [
                    {"name": "bytes", "configuration": {"endian": "little"}},
                    {"name": "crc32c"},
                ],
                "index_location": "end",
            },
        }
    ],
}

UNSHARDED_MANIFEST = {
    "zarr_format": 3,
    "node_type": "array",
    "shape": [4, 4],
    "data_type": "uint8",
    "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [2, 2]}},
    "chunk_key_encoding": {"name": "default"},
    "fill_value": 0,
    "codecs": [{"name": "bytes", "configuration": {"endian": "little"}}],
}


@pytest.fixture()
def organization(db) -> Organization:
    """The organization every grant in this module is issued in."""
    org, _ = Organization.objects.get_or_create(slug="static_org")
    return org


def granted_store_with_manifest(layer, organization: Organization, manifest: dict) -> ZarrStore:
    """Mint an upload grant and put the given manifest at the store's prefix."""
    grant = layer.generate_zarr_upload_grant(organization.id, base_models.RequestZarrUploadInput())
    store = ZarrStore.objects.get(id=grant.store)
    bucket = layer.get_bucket_config("zarr").bucket
    layer._s3.put_object(Bucket=bucket, Key=f"{store.key}/zarr.json", Body=json.dumps(manifest).encode())
    return store


def metadata_for(manifest: dict) -> base_models.ZarrMetadata:
    """Build a ZarrMetadata straight from a manifest dict, bypassing S3."""
    return base_models.ZarrMetadata(
        zarr_format=manifest["zarr_format"],
        node_type=manifest["node_type"],
        shape=manifest["shape"],
        data_type=manifest.get("data_type"),
        chunk_grid=manifest.get("chunk_grid"),
        chunk_key_encoding=manifest.get("chunk_key_encoding"),
        fill_value=manifest.get("fill_value"),
        codecs=manifest.get("codecs") or [],
    )


def sharded_variant(**config_overrides) -> dict:
    """A deep copy of SHARDED_MANIFEST with its sharding configuration patched."""
    manifest = copy.deepcopy(SHARDED_MANIFEST)
    manifest["codecs"][0]["configuration"].update(config_overrides)
    return manifest


def test_a_finished_sharded_upload_splits_chunks_from_shards(buckets, organization):  # noqa: F811
    """`chunks` must be the readable unit and `shards` the storage unit, or every
    consumer of the row plans reads against 128 MiB objects it cannot decode."""
    store = granted_store_with_manifest(buckets, organization, SHARDED_MANIFEST)

    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    store.refresh_from_db()
    assert store.populated is True
    assert store.chunks == [1, 1, 4, 512, 512]
    assert store.shards == [1, 1, 16, 2048, 2048]
    # The codec pipeline is recorded verbatim: readers that want the inner compressors
    # or the index layout get them from here, not from re-derived fields.
    assert store.codecs == SHARDED_MANIFEST["codecs"]


def test_an_unsharded_upload_keeps_todays_shape(buckets, organization):  # noqa: F811
    """Regression: the split must not disturb the unsharded path."""
    store = granted_store_with_manifest(buckets, organization, UNSHARDED_MANIFEST)

    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    store.refresh_from_db()
    assert store.populated is True
    assert store.chunks == [2, 2]
    assert store.shards is None


@pytest.mark.parametrize(
    "manifest, complaint",
    [
        # An extra top-level codec after sharding: the reader unwraps codecs[0] and
        # refuses a pipeline with anything beside it.
        (
            {**copy.deepcopy(SHARDED_MANIFEST), "codecs": copy.deepcopy(SHARDED_MANIFEST["codecs"]) + [{"name": "crc32c"}]},
            "sole top-level codec",
        ),
        # Sharding at position 1 (behind a transpose) is permitted by the spec but not
        # by the reader — and it would also flip the chunks/shards split undetected.
        (
            {**copy.deepcopy(SHARDED_MANIFEST), "codecs": [{"name": "transpose", "configuration": {"order": [0, 1, 2, 3, 4]}}] + copy.deepcopy(SHARDED_MANIFEST["codecs"])},
            "sole top-level codec",
        ),
        # A shard that is not an exact multiple of the inner chunk has no well-defined index grid.
        (sharded_variant(chunk_shape=[1, 1, 4, 512, 500]), "not an exact multiple"),
        # Rank mismatch between the inner chunk and the shard.
        (sharded_variant(chunk_shape=[4, 512, 512]), "rank"),
        # Nested sharding.
        (
            sharded_variant(codecs=[{"name": "sharding_indexed", "configuration": {"chunk_shape": [1, 1, 2, 256, 256], "codecs": []}}]),
            "Nested",
        ),
        # An index location the reader does not know.
        (sharded_variant(index_location="middle"), "index_location"),
    ],
    ids=["extra-top-level-codec", "sharding-not-first", "non-multiple-shard", "rank-mismatch", "nested-sharding", "bad-index-location"],
)
def test_an_unreadable_sharding_layout_fails_the_finish(buckets, organization, manifest, complaint):  # noqa: F811
    """The finish is the last moment the platform can refuse a store the frontend
    cannot read; after it, the store row looks exactly like a working one."""
    store = granted_store_with_manifest(buckets, organization, manifest)

    with pytest.raises(ValueError, match=complaint):
        buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    store.refresh_from_db()
    assert store.populated is False


def test_metadata_properties_split_without_s3():
    """The chunks/shards split is a pure function of the manifest."""
    sharded = metadata_for(SHARDED_MANIFEST)
    assert sharded.chunks == [1, 1, 4, 512, 512]
    assert sharded.shards == [1, 1, 16, 2048, 2048]
    sharded.validate_sharding()

    unsharded = metadata_for(UNSHARDED_MANIFEST)
    assert unsharded.chunks == [2, 2]
    assert unsharded.shards is None
    unsharded.validate_sharding()

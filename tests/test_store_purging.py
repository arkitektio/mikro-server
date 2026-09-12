"""Deleting data eventually deletes its bytes, and never deletes bytes something still wants.

These assert against real bucket contents through `moto`, not against mock call counts, because
the bug being fixed is precisely that a call was made and deleted nothing: a zarr key is a
*prefix*, and `DeleteObject` on a prefix returns 204 having removed no chunk at all.

The two safety properties matter more than the feature:

- a store still referenced by anything is never purged (stores can be shared), and
- a store that was never flagged is never purged, because an unreferenced store is the normal
  state of an upload in flight rather than a sign of garbage.
"""

import datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from moto import mock_aws

import datalayer.datalayer as datalayer_module
from core import models
from core.logic import storage
from datalayer.models import DatalayerStore
from kante.context import HttpContext
from mikro_server.schema import schema

from tests import seed
from tests.seed import create_array_dataset, create_folder, create_file


@pytest.fixture()
def buckets(monkeypatch):
    """A real (moto-backed) S3 with the configured buckets created.

    `endpoint_url` is dropped for the duration: moto patches botocore's default AWS endpoints
    and does **not** intercept a client pointed at `http://minio:9000`, which is what the real
    config carries -- a client built against it tries to open a socket and fails. Removing
    host/port makes `endpoint_url` None, which is the shape moto understands.

    `GLOBAL_DL` is reset on the way in and out because `get_current_datalayer` memoizes one
    Datalayer for the process, and a client built outside the mock would leak into these tests
    (and one built inside would leak out).
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    config = {key: value for key, value in settings.DATALAYER.items() if key not in ("host", "port", "protocol")}
    monkeypatch.setattr(settings, "DATALAYER", config)

    with mock_aws():
        monkeypatch.setattr(datalayer_module, "GLOBAL_DL", None)
        layer = datalayer_module.Datalayer()
        for bucket_key in ("bigfile", "zarr", "parquet", "media", "fabriks"):
            layer._s3.create_bucket(Bucket=layer.get_bucket_config(bucket_key).bucket)
        monkeypatch.setattr(datalayer_module, "GLOBAL_DL", layer)
        yield layer
    datalayer_module.GLOBAL_DL = None


def keys_in(layer, bucket_key: str) -> set[str]:
    """Every object key currently in one of the datalayer's buckets."""
    bucket = layer.get_bucket_config(bucket_key).bucket
    listing = layer._s3.list_objects_v2(Bucket=bucket)
    return {item["Key"] for item in listing.get("Contents", [])}


def put(layer, bucket_key: str, key: str) -> None:
    """Put one object into a datalayer bucket."""
    layer._s3.put_object(Bucket=layer.get_bucket_config(bucket_key).bucket, Key=key, Body=b"bytes")


def purge(**options) -> str:
    """Run the sweeper and return its output."""
    out = StringIO()
    call_command("purge_orphaned_stores", stdout=out, stderr=out, **options)
    return out.getvalue()


def age(store: DatalayerStore, days: int) -> None:
    """Backdate a store's orphaning so the grace period has elapsed."""
    DatalayerStore.objects.filter(pk=store.pk).update(orphaned_at=timezone.now() - datetime.timedelta(days=days))


# --------------------------------------------------------------------------------------
# The headline: a delete flags, the sweeper collects.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_file_flags_its_store_and_the_sweeper_purges_it(db, authenticated_context: HttpContext, buckets):
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/scan", bucket="bigfile", key="scan", populated=True, organization=ctx.request.organization)
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "scan.czi", folder, store=store)
    await sync(put)(buckets, "bigfile", "scan")

    result = await schema.execute(
        "mutation D($input: DeleteFileInput!) { deleteFile(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(file.id)}},
    )
    assert not result.errors, result.errors

    # The request does no S3 work: the bytes are still there, the store is merely flagged.
    assert await sync(keys_in)(buckets, "bigfile") == {"scan"}
    store = await DatalayerStore.objects.aget(pk=store.pk)
    assert store.orphaned_at is not None

    await sync(age)(store, 30)
    output = await sync(purge)()

    assert "purged" in output
    assert await sync(keys_in)(buckets, "bigfile") == set()
    assert not await DatalayerStore.objects.filter(pk=store.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_dataset_purges_every_pyramid_level(db, authenticated_context: HttpContext, buckets):
    """The stores hang off DataArray rows, not off the dataset -- a one-hop walk misses them.

    And each is a zarr, so each needs the prefix delete: `delete_object` against `level-0`
    would succeed and leave every chunk in place.
    """
    ctx = authenticated_context
    dataset = await create_array_dataset(ctx, "Cells", shapes=[[8, 64, 64], [8, 32, 32], [8, 16, 16]])

    # The seed helper builds the coordinate graph but attaches no stores, so give each level
    # one. (An earlier version of this test read `store__key` straight off the levels and got
    # `[None, None, None]` -- three of them, so a length assertion passed while checking
    # nothing. Attach them explicitly and assert the keys are real.)
    arrays = [array async for array in models.DataArray.objects.filter(dataset=dataset).order_by("level")]
    assert len(arrays) == 3, "the seed helper is expected to build three levels"

    store_keys = []
    for array in arrays:
        store = await models.ZarrStore.objects.acreate(
            path=f"s3://zarr/level-{array.level}", bucket="zarr", key=f"level-{array.level}", populated=True, organization=ctx.request.organization
        )
        array.store = store
        await array.asave(update_fields=["store"])
        store_keys.append(store.key)

    assert all(store_keys), "every level must really have a store, or this test asserts nothing"
    for key in store_keys:
        for suffix in ("zarr.json", "c/0/0", "c/0/1"):
            await sync(put)(buckets, "zarr", f"{key}/{suffix}")
    # A sibling prefix that merely shares a leading substring must survive.
    await sync(put)(buckets, "zarr", f"{store_keys[0]}-untouched/zarr.json")

    result = await schema.execute(
        "mutation D($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(dataset.id)}},
    )
    assert not result.errors, result.errors

    flagged = [store async for store in DatalayerStore.objects.filter(orphaned_at__isnull=False)]
    assert len(flagged) == 3, "every level's store must be flagged, not just the dataset's own"

    for store in flagged:
        await sync(age)(store, 30)
    await sync(purge)()

    remaining = await sync(keys_in)(buckets, "zarr")
    assert all("untouched" in key for key in remaining), f"only the sibling prefix should remain, got {remaining}"
    assert remaining, "the sibling prefix must not have been swept up"


# --------------------------------------------------------------------------------------
# The two safety properties.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shared_store_is_never_purged(db, authenticated_context: HttpContext, buckets):
    """Two Files on one store: deleting one must not destroy the other's bytes.

    Nothing stops this today -- no `store` FK has a unique constraint and store ids are
    client-supplied with no already-attached check -- so the sweeper's re-check is the only
    thing between a shared store and data loss.
    """
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/shared", bucket="bigfile", key="shared", populated=True, organization=ctx.request.organization)
    folder = await create_folder(ctx, "DS")
    doomed = await create_file(ctx, "first.czi", folder, store=store)
    await create_file(ctx, "second.czi", folder, store=store)
    await sync(put)(buckets, "bigfile", "shared")

    result = await schema.execute(
        "mutation D($input: DeleteFileInput!) { deleteFile(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(doomed.id)}},
    )
    assert not result.errors, result.errors

    await sync(age)(store, 30)
    output = await sync(purge)()

    assert "keep" in output and "still referenced" in output
    assert await sync(keys_in)(buckets, "bigfile") == {"shared"}
    refreshed = await DatalayerStore.objects.aget(pk=store.pk)
    assert refreshed.orphaned_at is None, "a re-checked store must be un-flagged, not left as a standing candidate"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_upload_in_flight_is_never_touched(db, authenticated_context: HttpContext, buckets):
    """An unreferenced store is not garbage -- it is the normal state of a live upload.

    `requestFileUpload` creates the row before the client has uploaded anything and long
    before a data row attaches. Sweeping unreferenced stores would delete uploads in progress,
    which is why only *flagged* rows are ever collectable.
    """
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/inflight", bucket="bigfile", key="inflight", populated=False, organization=ctx.request.organization)
    await sync(put)(buckets, "bigfile", "inflight")

    await sync(purge)(older_than=0)

    assert await sync(keys_in)(buckets, "bigfile") == {"inflight"}
    assert await DatalayerStore.objects.filter(pk=store.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_grace_period_holds(db, authenticated_context: HttpContext, buckets):
    """A store flagged just now survives a default run -- that is the recovery window."""
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/fresh", bucket="bigfile", key="fresh", populated=True, organization=ctx.request.organization)
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "fresh.czi", folder, store=store)
    await sync(put)(buckets, "bigfile", "fresh")

    result = await schema.execute(
        "mutation D($input: DeleteFileInput!) { deleteFile(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(file.id)}},
    )
    assert not result.errors, result.errors

    await sync(purge)()
    assert await sync(keys_in)(buckets, "bigfile") == {"fresh"}, "the default grace period must protect a fresh delete"

    await sync(purge)(older_than=0)
    assert await sync(keys_in)(buckets, "bigfile") == set(), "--older-than 0 must collect it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dry_run_deletes_nothing(db, authenticated_context: HttpContext, buckets):
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/dry", bucket="bigfile", key="dry", populated=True, organization=ctx.request.organization)
    await sync(put)(buckets, "bigfile", "dry")
    await sync(age)(store, 30)

    output = await sync(purge)(dry_run=True)

    assert "would purge" in output
    assert await sync(keys_in)(buckets, "bigfile") == {"dry"}
    assert await DatalayerStore.objects.filter(pk=store.pk).aexists()


# --------------------------------------------------------------------------------------
# Collection, in isolation from the sweeper.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_failed_delete_flags_nothing(db, authenticated_context: HttpContext, buckets):
    """Collection happens before the delete and flagging after it, in one transaction."""
    ctx = authenticated_context
    store = await models.BigFileStore.objects.acreate(path="s3://bigfile/kept", bucket="bigfile", key="kept", populated=True, organization=ctx.request.organization)
    folder = await create_folder(ctx, "DS")
    file = await create_file(ctx, "kept.czi", folder, store=store)

    with patch.object(models.File, "delete", side_effect=RuntimeError("boom")):
        result = await schema.execute(
            "mutation D($input: DeleteFileInput!) { deleteFile(input: $input) }",
            context_value=ctx,
            variable_values={"input": {"id": str(file.id)}},
        )
    assert result.errors

    refreshed = await DatalayerStore.objects.aget(pk=store.pk)
    assert refreshed.orphaned_at is None, "a delete that raised must leave no store flagged"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_referrers_of_sees_a_store_a_collection_points_at(db, authenticated_context: HttpContext, buckets):
    """A store something still points at is never purged, however it is pointed at.

    Note **no store many-to-many exists anywhere any more**, so that branch of `_store_fields`
    -- the relation shape a purely cascade-based collector misses, because `Collector` walks
    through-rows rather than their targets -- is structural insurance rather than something a
    test covers. It is kept so the next such relation does not have to rediscover the problem.
    """
    ctx = authenticated_context
    store = await sync(seed._seed_fabriks_store_sync)(ctx, axes=None, populated=True)

    assert await sync(storage.referrers_of)(store) == [], "a fresh store is referenced by nothing"

    await models.MeshCollection.objects.acreate(
        version="v1",
        spec_version="fabriks/1",
        store=store,
        creator=ctx.request.user,
        organization=ctx.request.organization,
    )

    assert await sync(storage.referrers_of)(store), "a referenced store is still in use -- these bytes must not be collected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_mesh_collection_flags_its_fabriks_store(db, authenticated_context: HttpContext, buckets):
    """One collection, one store, one prefix to collect.

    The sweep needs no special case for it: `_store_fields` finds the foreign key by walking
    `_meta`, and `purge_orphaned_stores` reads `is_prefix` off the downcast row to choose a
    list-then-batch delete over a `DeleteObject` that would succeed having removed nothing.
    """
    ctx = authenticated_context
    store = await sync(seed._seed_fabriks_store_sync)(ctx, axes=None, populated=True)

    collection = await models.MeshCollection.objects.acreate(
        version="v1",
        spec_version="fabriks/1",
        store=store,
        creator=ctx.request.user,
        organization=ctx.request.organization,
    )

    result = await schema.execute(
        "mutation D($input: DeleteMeshCollectionInput!) { deleteMeshCollection(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(collection.pk)}},
    )
    assert not result.errors, result.errors

    refreshed = await DatalayerStore.objects.aget(pk=store.pk)
    assert refreshed.orphaned_at is not None, "the collection was the only thing pointing at that prefix"
    assert refreshed.get_real_instance().is_prefix, "and it is collected as a prefix, not as one object"


def sync(fn):
    """Run a blocking ORM/boto call from an async test."""
    from asgiref.sync import sync_to_async

    return sync_to_async(fn)

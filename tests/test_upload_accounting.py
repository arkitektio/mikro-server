"""What an upload advertised, what it delivered, and how it survives its own credentials expiring.

Two facts about credential grants drive everything here, and neither is obvious from the grant
payload:

- **A session policy bounds what a credential may write, never how much.** ``s3:PutObject``
  takes no size condition, so the ``maxBytes`` a grant advertises cannot be enforced at write
  time by any mechanism S3 offers. It was advertised and checked nowhere at all; now it is
  advertised, measured against, and *still* not enforced -- which is the honest state, and the
  reason these tests assert an overrun is **recorded** rather than refused.
- **Credentials expire while a write is still running.** Clients hold the session token as a
  static credential (obstore's ``S3Store`` has no refresh hook), so a multi-GB array that
  outlives its hour dies partway through. Hence a reissue path, and hence its one refusal: a
  populated store is finished, and writing to it again is an overwrite, not a resumption.

These assert against real bucket contents through `moto` for the same reason
`test_store_purging` does -- the measurement is a listing, and a listing that silently returns
nothing looks exactly like an empty store.
"""

import json
import logging

import pytest
from django.conf import settings

import datalayer.datalayer as datalayer_module
from authentikate.models import Organization
from datalayer import base_models
from datalayer.models import ZarrStore

# The moto-backed datalayer with every configured bucket created. Shared rather than copied:
# the fixture encodes two non-obvious things (moto ignores a client pointed at a real endpoint,
# and `GLOBAL_DL` memoizes one Datalayer per process) that would rot independently if duplicated.
from tests.test_store_purging import buckets  # noqa: F401


#: A minimal valid zarr v3 array manifest. `fill_info` fetches and parses this, so a store
#: cannot be finished without one -- the accounting below runs *after* that parse.
ZARR_MANIFEST = {
    "zarr_format": 3,
    "node_type": "array",
    "shape": [4, 4],
    "data_type": "uint8",
    "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [2, 2]}},
    "chunk_key_encoding": {"name": "default"},
    "fill_value": 0,
    "codecs": [],
}


@pytest.fixture()
def organization(db) -> Organization:
    """The organization every grant in this module is issued in."""
    org, _ = Organization.objects.get_or_create(slug="static_org")
    return org


def write_zarr(layer, store: ZarrStore, chunk_bytes: int = 0) -> int:
    """Write a finishable zarr into the store's prefix, and return the bytes written."""
    bucket = layer.get_bucket_config("zarr").bucket
    manifest = json.dumps(ZARR_MANIFEST).encode()
    layer._s3.put_object(Bucket=bucket, Key=f"{store.key}/zarr.json", Body=manifest)
    written = len(manifest)
    if chunk_bytes:
        layer._s3.put_object(Bucket=bucket, Key=f"{store.key}/c/0/0", Body=b"x" * chunk_bytes)
        written += chunk_bytes
    return written


def request_store(layer, organization: Organization) -> ZarrStore:
    """Ask for a zarr upload grant and return the store it minted."""
    grant = layer.generate_zarr_upload_grant(organization.id, base_models.RequestZarrUploadInput())
    return ZarrStore.objects.get(id=grant.store)


def test_a_grant_records_the_budget_it_advertised(buckets, organization):  # noqa: F811
    """The advertised number has to be persisted, or there is nothing to compare against later.

    It goes on the row at grant time rather than being recomputed at finish because the config
    default can change between the two, and the question worth answering is what *this* upload
    was told.
    """
    store = request_store(buckets, organization)
    assert store.max_bytes == buckets.get_bucket_config("zarr").default_max_bytes


def test_a_finished_upload_records_what_it_actually_delivered(buckets, organization):  # noqa: F811
    """The measurement is a sum over the prefix, not the one object at it.

    A zarr's key names a directory; `head_object` on it answers for nothing, which is the same
    confusion that made `DeleteObject` on a prefix delete nothing.
    """
    store = request_store(buckets, organization)
    written = write_zarr(buckets, store, chunk_bytes=512)

    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    store.refresh_from_db()
    assert store.populated is True
    assert store.size_bytes == written


def test_an_upload_over_its_advertised_budget_is_recorded_not_rejected(buckets, organization, caplog):  # noqa: F811
    """The cap is advertised, measured, and deliberately not enforced.

    Refusing here would reject uploads that were never told a real budget: `maxBytes` is
    output-only -- there is no input field to declare a size in -- so every zarr grant carries
    the configured default regardless of what the client is about to write. Enforcing that
    number would fail essentially every real upload, so the finish succeeds and says so.
    """
    store = request_store(buckets, organization)
    store.max_bytes = 100
    store.save(update_fields=["max_bytes"])
    written = write_zarr(buckets, store, chunk_bytes=4096)

    # `settings_test` calls `logging.disable(logging.CRITICAL)` process-wide, which outranks
    # `caplog.at_level`. Lifted for this assertion only: "the overrun is logged" is half of
    # what makes an unenforced cap acceptable, so it is worth asserting rather than assuming.
    logging.disable(logging.NOTSET)
    try:
        with caplog.at_level(logging.WARNING, logger="datalayer.datalayer"):
            buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))
    finally:
        logging.disable(logging.CRITICAL)

    store.refresh_from_db()
    assert store.populated is True, "an overrun is not a failed upload"
    assert store.size_bytes == written > store.max_bytes
    assert any("against an advertised budget" in record.message for record in caplog.records), "and it is not silent either"


def test_a_finish_survives_a_measurement_it_cannot_take(buckets, organization, monkeypatch):  # noqa: F811
    """Accounting must never cost an upload.

    The bytes are written and the metadata is parsed by the time this runs, so a listing that
    fails is a bookkeeping problem. Losing the store over it would trade something that matters
    for something that does not.
    """
    store = request_store(buckets, organization)
    write_zarr(buckets, store)

    def refuse(*args, **kwargs):
        raise RuntimeError("listing is unavailable")

    monkeypatch.setattr(datalayer_module.Datalayer, "measure_prefix_bytes", refuse)
    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    store.refresh_from_db()
    assert store.populated is True
    assert store.size_bytes is None


def test_a_refresh_reissues_credentials_against_the_same_prefix(buckets, organization):  # noqa: F811
    """A resumed write must land where the interrupted one was going.

    The whole point is that the second session addresses the *same* store: a reissue that minted
    a new key would leave the first upload's bytes orphaned and the client writing a fresh array
    it never asked for.
    """
    first = buckets.generate_zarr_upload_grant(organization.id, base_models.RequestZarrUploadInput())
    second = buckets.refresh_zarr_upload_grant(organization.id, first.store)

    assert (second.store, second.key, second.path, second.bucket) == (first.store, first.key, first.path, first.bucket)
    assert ZarrStore.objects.count() == 1, "a refresh reissues credentials, it does not mint a store"
    assert second.expires_in == first.expires_in


def test_a_refresh_refuses_an_upload_that_is_already_finished(buckets, organization):  # noqa: F811
    """A populated store's bytes are referenced, so write credentials for them are an overwrite.

    Distinct from a store finished with ``valid=False``, which is *not* populated and stays
    refreshable -- that is the retry case this exists to serve.
    """
    store = request_store(buckets, organization)
    write_zarr(buckets, store)
    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(store.pk)))

    with pytest.raises(ValueError, match="already populated"):
        buckets.refresh_zarr_upload_grant(organization.id, str(store.pk))

    invalidated = request_store(buckets, organization)
    buckets.finish_zarr_upload(organization.id, base_models.FinishZarrUploadInput(store_id=str(invalidated.pk), valid=False))
    assert buckets.refresh_zarr_upload_grant(organization.id, str(invalidated.pk)).store == str(invalidated.pk)


def test_a_refresh_cannot_reach_another_organizations_store(buckets, organization):  # noqa: F811
    """Scoped by organization like every other store lookup, and worth pinning: this one hands
    out *write* credentials, so a missed scope is not a leak but a takeover."""
    store = request_store(buckets, organization)
    other, _ = Organization.objects.get_or_create(slug="other_org")

    with pytest.raises(ZarrStore.DoesNotExist):
        buckets.refresh_zarr_upload_grant(other.id, str(store.pk))


def test_a_measured_store_reports_both_numbers_to_clients(buckets, organization):  # noqa: F811
    """Advertised and delivered are both on the GraphQL type, because the gap is the finding.

    `maxBytes` alone reads as a guarantee. Beside `sizeBytes` it reads as what it is.
    """
    from datalayer import types

    assert {"max_bytes", "size_bytes"} <= set(types.ZarrStore.__annotations__)
    assert settings.DATALAYER is not None

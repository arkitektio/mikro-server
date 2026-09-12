"""What a grant is scoped to, and why a prefix store needs a different one.

Nothing covered the policy builder before this. That mattered more than it looks: the two
halves of "this store is a directory" were stated in different places -- deletion read
`is_prefix` off the model, the grant builder tested `bucket_key == "zarr"` -- so they could
disagree, and the disagreement was **silent in both directions**. A prefix store granted an
object-scoped policy cannot list or write its own children; an object store granted a prefix
policy hands out more than it should. Neither raises, and in a deployment with no `role_arn`
configured no policy is attached at all, so nothing would surface until the day one is.

These tests read the policy document directly rather than asserting on a live S3, because the
document *is* the contract -- the same reason `test_store_purging` asserts against real bucket
contents where the bug it guards is a call that succeeded and deleted nothing.
"""

import json

import pytest

from datalayer.datalayer import MIN_SESSION_DURATION_SECONDS, Datalayer
from datalayer.models import (
    BigFileStore,
    DatalayerStore,
    FabriksStore,
    KonnektionStore,
    MediaStore,
    ParquetStore,
    SparseStore,
    ZarrStore,
)


class _RecordingSts:
    """Stands in for the STS client: records each request and mints a fake session.

    Substituted for the client rather than for :meth:`Datalayer._assume_role`, so the request
    these tests assert on is the one boto3 would actually have been handed.
    """

    def __init__(self, refuse: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.refuse = refuse

    def assume_role(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return {"Credentials": {"AccessKeyId": "temp-key", "SecretAccessKey": "temp-secret", "SessionToken": "temp-token"}}

    @property
    def policy(self) -> dict:
        """The inline policy of the last session requested."""
        return json.loads(self.calls[-1]["Policy"])  # type: ignore[arg-type]


def _configure(settings, **overrides: object) -> Datalayer:
    """Build a datalayer with every logical bucket configured, pointing nowhere in particular.

    Replaces ``settings.DATALAYER`` wholesale rather than merging into it, and must keep doing
    so: `settings_test` turns `allow_unscoped_fallback` on for the rest of the suite, and merged
    in here it would make the refusal tests below pass while asserting nothing.
    """
    settings.DATALAYER = {
        "access_key": "key",
        "secret_key": "secret",
        "host": "minio",
        "port": 9000,
        "protocol": "http",
        # Without a role there is nothing to assume and no grant can be issued at all, so the
        # fixture sets one exactly as a deployment must. MinIO ignores the value.
        "role_arn": "arn:aws:iam::000000000000:role/datalayer",
        "bigfile": {"bucket": "files"},
        "media": {"bucket": "files"},
        "zarr": {"bucket": "arrays"},
        "parquet": {"bucket": "tables"},
        **overrides,
    }
    return Datalayer()


@pytest.fixture()
def sts() -> _RecordingSts:
    """The STS client every grant in these tests talks to."""
    return _RecordingSts()


@pytest.fixture()
def layer(settings, sts: _RecordingSts) -> Datalayer:
    """A datalayer with every logical bucket configured and a stubbed STS."""
    instance = _configure(settings)
    instance._sts = sts
    return instance


def _statements(policy: dict) -> list[dict]:
    return policy["Statement"]  # type: ignore[return-value]


def test_the_prefix_buckets_are_derived_from_the_store_classes():
    """The set is a consequence of the models, not a literal anyone has to remember.

    `FabriksStore` is the evidence rather than the example: it joined this set by declaring
    `bucket_key` and `is_prefix = True` on the model, **without an edit to the grant builder**.
    Under the previous `bucket_key == "zarr"` test it would have been deleted correctly and
    granted credentials that could neither list nor write its own children -- and nothing would
    have raised, because a deployment with no role to assume attaches no policy at all, so the
    breakage only surfaces on the day one is configured.

    `KonnektionStore` is the second piece of the same evidence, and it arrived the same way: two
    class attributes on a model, no grant-builder change, and this assertion is the only line
    that had to move.
    """
    assert Datalayer.prefix_bucket_keys() == {"zarr", "fabriks", "konnektion"}

    subclasses = DatalayerStore.__subclasses__()
    assert {subclass.bucket_key for subclass in subclasses} == {"bigfile", "media", "zarr", "parquet", "fabriks", "konnektion"}, "every store type declares which bucket it belongs to"
    assert [subclass.is_prefix for subclass in (BigFileStore, MediaStore, ParquetStore)] == [False, False, False]
    assert FabriksStore.bucket_key == "fabriks" and FabriksStore.is_prefix
    assert KonnektionStore.bucket_key == "konnektion" and KonnektionStore.is_prefix

    # A bucket may hold more than one kind of store, and one does: a sparse matrix is a zarr
    # tree, so `SparseStore` lives in the zarr bucket rather than paying for a bucket of its
    # own (config, MinIO, a Caddy route in both site blocks, an `arkitekt-server` entry).
    #
    # What that makes load-bearing is not "one class per bucket" -- it never was -- but that
    # **classes sharing a bucket agree on `is_prefix`**. `prefix_bucket_keys()` answers per
    # *bucket*, so a bucket holding one prefix store and one object store would get a single
    # answer that is wrong for one of them: either object-scoped deletion on a tree, which
    # succeeds having removed nothing and leaks every byte, or a listable grant over
    # single objects. Asserted here because nothing else would notice.
    by_bucket: dict[str, set[bool]] = {}
    for subclass in subclasses:
        by_bucket.setdefault(subclass.bucket_key, set()).add(subclass.is_prefix)
    disagreeing = {bucket: kinds for bucket, kinds in by_bucket.items() if len(kinds) > 1}
    assert not disagreeing, f"stores sharing a bucket must agree on is_prefix, but {disagreeing} do not"

    in_zarr = {subclass for subclass in subclasses if subclass.bucket_key == "zarr"}
    assert in_zarr == {ZarrStore, SparseStore}, "the zarr bucket holds arrays and sparse groups, both prefixes"


def test_an_object_store_is_granted_exactly_its_own_key(layer: Datalayer):
    """One object, one resource, and no bucket listing -- a parquet is a single file."""
    policy = layer._build_policy("tables", "parquet", "abc123", "read")
    statements = _statements(policy)

    assert len(statements) == 1, "no ListBucket statement for a single object"
    assert statements[0]["Resource"] == ["arn:aws:s3:::tables/abc123"]
    assert statements[0]["Action"] == ["s3:GetObject"]


def test_a_prefix_store_is_granted_its_whole_tree_and_may_list_it(layer: Datalayer):
    """A zarr is a directory: the grant must cover the children and permit listing them.

    Without the `/*` resource a reader can fetch `zarr.json` and not one chunk; without the
    conditional `ListBucket` a client cannot discover what is there, which is also what any
    glob-based reader needs.
    """
    policy = layer._build_policy("arrays", "zarr", "abc123", "read")
    statements = _statements(policy)

    assert statements[0]["Resource"] == ["arn:aws:s3:::arrays/abc123", "arn:aws:s3:::arrays/abc123/*"]

    listing = statements[1]
    assert listing["Action"] == ["s3:ListBucket"]
    assert listing["Resource"] == ["arn:aws:s3:::arrays"]
    assert listing["Condition"]["StringLike"]["s3:prefix"] == ["abc123", "abc123/*"]


def test_a_prefix_upload_may_read_back_and_delete_inside_its_own_tree(layer: Datalayer):
    """A tree is written incrementally, so the writer needs more than PutObject.

    It reads back and rewrites objects as it goes, and a failed multipart leaves garbage only
    DeleteObject can clear. An object upload gets neither, deliberately: there is nothing to
    read back and nothing to clean up but the one key.
    """
    prefix_upload = _statements(layer._build_policy("arrays", "zarr", "abc123", "upload"))[0]
    assert prefix_upload["Action"] == ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]

    object_upload = _statements(layer._build_policy("tables", "parquet", "abc123", "upload"))[0]
    assert object_upload["Action"] == ["s3:PutObject", "s3:AbortMultipartUpload"]


def test_a_fabriks_store_is_granted_its_whole_prefix_and_may_write_inside_it(layer: Datalayer, settings):
    """The fifth store type gets prefix treatment because of what it declares, not who it is."""
    settings.DATALAYER = {**settings.DATALAYER, "fabriks": {"bucket": "meshes"}}
    layer = Datalayer()

    read = _statements(layer._build_policy("meshes", "fabriks", "abc123", "read"))
    assert read[0]["Resource"] == ["arn:aws:s3:::meshes/abc123", "arn:aws:s3:::meshes/abc123/*"]
    assert read[1]["Action"] == ["s3:ListBucket"], "a reader globs level partitions, so it must be able to list"

    upload = _statements(layer._build_policy("meshes", "fabriks", "abc123", "upload"))[0]
    assert "s3:DeleteObject" in upload["Action"], "a tree written incrementally needs to clean up after a failed part"


def test_every_store_type_has_an_organization_wide_read_grant(layer: Datalayer, settings, sts: _RecordingSts):
    """`requestGeneral*Access` exists per store type, and fabriks is one of them.

    There is no single `requestGeneralAccess`: the grants are per logical bucket, because each
    names the bucket it is for and a client asks for the one it is about to read. So "does
    general access cover fabriks" is answered by there *being* a fabriks one -- and by the
    resolver being registered in the schema, which is a separate mistake to make.
    """
    settings.DATALAYER = {**settings.DATALAYER, "fabriks": {"bucket": "meshes"}}
    layer = Datalayer()
    layer._sts = sts

    grant = layer.generate_general_fabriks_access_grant(organization_id="1", user_id="1")

    assert grant.bucket == "meshes", "the grant names the bucket it is for"
    assert grant.access_key and grant.secret_key, "and carries usable credentials"
    assert grant.status == "granted"


def test_a_general_grant_refuses_a_bucket_the_deployment_has_not_configured(layer: Datalayer, settings):
    """`fabriks` is optional in the config, so asking for it unconfigured must say so.

    The alternative -- handing out credentials naming a bucket that does not exist -- fails
    later and further away, at whichever client tries to read through them.
    """
    settings.DATALAYER = {key: value for key, value in settings.DATALAYER.items() if key != "fabriks"}
    layer = Datalayer()

    with pytest.raises(ValueError, match="not configured"):
        layer.generate_general_fabriks_access_grant(organization_id="1", user_id="1")


def test_a_bucket_subpath_is_carried_into_the_grant(layer: Datalayer, settings):
    """Two logical buckets can share a physical one, so the subpath must reach the resource.

    `config.yaml` already points media and bigfile at the same bucket. Nothing sets `subpath`
    today, which is exactly why it is worth a test before something does.
    """
    settings.DATALAYER = {**settings.DATALAYER, "zarr": {"bucket": "arrays", "subpath": "meshes"}}
    layer = Datalayer()

    statements = _statements(layer._build_policy("arrays", "zarr", "abc123", "read"))
    assert statements[0]["Resource"] == ["arn:aws:s3:::arrays/meshes/abc123", "arn:aws:s3:::arrays/meshes/abc123/*"]
    assert statements[1]["Condition"]["StringLike"]["s3:prefix"] == ["meshes/abc123", "meshes/abc123/*"]


def test_a_prefix_grant_asks_for_no_action_its_condition_cannot_cover(layer: Datalayer):
    """The listing statement carries an `s3:prefix` condition, so every action under it must accept one.

    MinIO validates the inline policy when the role is assumed and rejects the *whole document*
    if one action cannot be conditioned that way:

        InvalidParameterValue: unsupported condition keys '[s3:prefix]' used for action 's3:GetBucketLocation'

    That refusal used to be swallowed, so the cost of the extra action was not a broken listing
    but a silent downgrade to the service account's permanent key -- for zarr and fabriks, the
    two store types that carry the actual pixel data.
    """
    for bucket, bucket_key in (("arrays", "zarr"), ("tables", "parquet")):
        for statement in _statements(layer._build_policy(bucket, bucket_key, "abc123", "read")):
            assert "s3:GetBucketLocation" not in statement["Action"]


def test_a_grant_that_cannot_be_scoped_fails_instead_of_handing_over_the_service_key(settings, sts: _RecordingSts):
    """The point of the whole exercise: a refused session must not become an unlimited one.

    Every route to credentials -- no `role_arn`, an STS that refuses, a duration STS won't
    accept -- used to end at the same `except Exception` returning `config.access_key`: the
    service account's cluster-wide `readwrite`, with no expiry and no log line. So a deployment
    could believe it was issuing scoped grants while issuing none at all.
    """
    refused = _configure(settings)
    refused._sts = _RecordingSts(refuse=RuntimeError("Unsupported action"))
    with pytest.raises(RuntimeError, match="permanent credentials"):
        refused._issue_temporary_credentials("zarr", "abc123", "read", 3600)

    roleless = _configure(settings, role_arn=None)
    roleless._sts = sts
    with pytest.raises(RuntimeError, match="permanent credentials"):
        roleless._issue_temporary_credentials("zarr", "abc123", "read", 3600)
    assert sts.calls == [], "no role means no request to make, not a request that happens to fail"


def test_the_unscoped_fallback_is_available_but_has_to_be_asked_for(settings):
    """Development still needs to run without a working STS -- explicitly, and only then."""
    layer = _configure(settings, allow_unscoped_fallback=True)
    layer._sts = _RecordingSts(refuse=RuntimeError("Unsupported action"))

    assert layer._issue_temporary_credentials("zarr", "abc123", "read", 3600) == ("key", "secret", "")


def test_a_short_lived_grant_is_floored_at_the_sts_minimum(layer: Datalayer, sts: _RecordingSts):
    """A duration STS would reject is another way into the fallback, so clamp rather than pass it on.

    `expires_in` is a ceiling a caller may lower, and lowering it past 900s used to buy a
    *permanent* key -- botocore refuses the call, and the refusal landed in the same place.
    """
    layer._issue_temporary_credentials("zarr", "abc123", "read", 60)
    assert sts.calls[-1]["DurationSeconds"] == MIN_SESSION_DURATION_SECONDS


def test_a_general_grant_is_read_only_and_stops_at_one_bucket(layer: Datalayer, sts: _RecordingSts):
    """It cannot name one store, but it must still be narrower than the service account.

    These grants carried no `Policy` at all, which against MinIO means the session inherits the
    caller's policy untouched -- `readwrite`, every bucket. Read-only on the one bucket the
    grant already names is the floor; per-organization scoping needs the keys to carry the
    organization, which they do not yet.
    """
    layer._issue_temporary_user_access_credentials("zarr", organization_id="1", user_id="1", expires_in=3600)

    statements = _statements(sts.policy)
    assert statements[0]["Action"] == ["s3:GetObject"]
    assert statements[0]["Resource"] == ["arn:aws:s3:::arrays/*"], "one bucket, not every bucket"
    assert statements[1]["Action"] == ["s3:ListBucket"], "a zarr reader has to be able to list chunks"

    layer._issue_temporary_user_access_credentials("media", organization_id="1", user_id="1", expires_in=3600)
    assert len(_statements(sts.policy)) == 1, "an object bucket needs no listing"

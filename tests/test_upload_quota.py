"""Who may upload, and how much: ``datalayer.upload_roles`` and ``datalayer.quotas``.

Runs through the real schema against the dokker stack (postgres + RustFS). Usage is a sum of
measured ``size_bytes``, so the "already holds N bytes" state is written straight onto store
rows -- the same number ``finish`` would have measured -- rather than uploading gigabytes.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

import datalayer.datalayer as datalayer_module
from datalayer import quota
from datalayer.models import MediaStore, ZarrStore
from mikro_server.schema import schema

GIB = 1024**3

REQUEST_ZARR = "mutation { requestZarrUpload(input: {}) { store maxBytes } }"
REQUEST_MEDIA = """
mutation($size: ByteCount) {
  requestMediaUpload(input: {originalFileName: "a.tif", fileSize: $size}) { store maxBytes }
}
"""
CREATE_FOLDER = 'mutation { createFolder(input: {name: "editor folder"}) { id } }'


@pytest.fixture()
def quotas(settings, monkeypatch):
    """Set ``datalayer.quotas`` for one test; the memoized datalayer is rebuilt around it."""
    monkeypatch.setattr(datalayer_module, "GLOBAL_DL", None)

    def configure(**block):
        settings.DATALAYER = {**settings.DATALAYER, "quotas": block}
        datalayer_module.GLOBAL_DL = None

    return configure


def _errors(result) -> str:
    return " ".join(error.message for error in result.errors or [])


@sync_to_async
def _hold(ctx: HttpContext, size: int, **kwargs) -> MediaStore:
    """Give the context's user a finished store of ``size`` measured bytes."""
    key = f"held-{MediaStore.objects.count()}"
    return MediaStore.objects.create(
        organization=ctx.request.organization,
        creator=kwargs.pop("creator", ctx.request.user),
        key=key,
        bucket="media",
        populated=True,
        size_bytes=size,
        **kwargs,
    )


# --- roles -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_editor_can_upload_and_create(db, editor_context: HttpContext, quotas):
    quotas()
    result = await schema.execute(REQUEST_ZARR, context_value=editor_context)
    assert not result.errors, result.errors

    store = await ZarrStore.objects.aget(id=result.data["requestZarrUpload"]["store"])
    assert store.creator_id == editor_context.request.user.id

    result = await schema.execute(CREATE_FOLDER, context_value=editor_context)
    assert not result.errors, result.errors


@pytest.fixture()
def viewer_context(db, backend_stack) -> HttpContext:
    """A user holding only "viewer" (static token "viewertest"): in the org, but outside ``upload_roles``."""
    from authentikate.models import Client, Membership, Organization, User
    from kante.context import UniversalRequest
    from strawberry.http.temporal_response import TemporalResponse

    user, _ = User.objects.get_or_create(sub="4", iss="static_issuer", defaults={"username": "static_issuer_4"})
    client, _ = Client.objects.get_or_create(client_id="oinsoins")
    org, _ = Organization.objects.get_or_create(slug="static_org")
    membership, _ = Membership.objects.get_or_create(user=user, organization=org, defaults={"roles": ["viewer"]})

    request = UniversalRequest(_extensions={"token": "viewertest"}, _client=client, _user=user, _organization=org)  # type: ignore
    request.set_membership(membership)  # type: ignore
    return HttpContext(request=request, response=TemporalResponse(), headers={"Authorization": "Bearer viewertest"}, type="http")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bot_can_upload(db, bot_context: HttpContext, quotas):
    quotas()
    result = await schema.execute(REQUEST_ZARR, context_value=bot_context)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_role_outside_upload_roles_cannot_upload(db, viewer_context: HttpContext, quotas):
    """The default ``upload_roles`` is admin + editor + bot: "viewer" is none of them."""
    quotas()
    result = await schema.execute(REQUEST_ZARR, context_value=viewer_context)
    assert result.errors and "roles" in _errors(result)


# --- per-upload limit --------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_per_upload_quota_is_advertised_and_enforced(db, authenticated_context: HttpContext, quotas):
    quotas(default={"max_upload_bytes": "500GiB"})

    result = await schema.execute(REQUEST_ZARR, context_value=authenticated_context)
    assert not result.errors, result.errors
    assert result.data["requestZarrUpload"]["maxBytes"] == 500 * GIB

    result = await schema.execute(REQUEST_MEDIA, variable_values={"size": 600 * GIB}, context_value=authenticated_context)
    assert result.errors and "per-upload quota" in _errors(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_without_quotas_the_bucket_default_is_advertised(db, authenticated_context: HttpContext, quotas):
    quotas()
    result = await schema.execute(REQUEST_ZARR, context_value=authenticated_context)
    assert not result.errors, result.errors
    assert result.data["requestZarrUpload"]["maxBytes"] == datalayer_module.get_current_datalayer().get_bucket_config("zarr").default_max_bytes


# --- totals ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_user_quota_counts_only_that_user(db, authenticated_context: HttpContext, editor_context: HttpContext, quotas):
    quotas(organizations={"static_org": {"max_user_bytes": 10 * GIB}})
    await _hold(authenticated_context, 10 * GIB)

    result = await schema.execute(REQUEST_ZARR, context_value=authenticated_context)
    assert result.errors and "Your storage quota" in _errors(result)

    result = await schema.execute(REQUEST_ZARR, context_value=editor_context)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_declared_size_counts_toward_the_user_quota(db, authenticated_context: HttpContext, quotas):
    quotas(default={"max_user_bytes": 10 * GIB})
    await _hold(authenticated_context, 6 * GIB)

    result = await schema.execute(REQUEST_MEDIA, variable_values={"size": 5 * GIB}, context_value=authenticated_context)
    assert result.errors and "Your storage quota" in _errors(result)

    result = await schema.execute(REQUEST_MEDIA, variable_values={"size": 3 * GIB}, context_value=authenticated_context)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_user_override_beats_the_org_limit(db, authenticated_context: HttpContext, quotas):
    quotas(organizations={"static_org": {"max_user_bytes": 1 * GIB, "users": {"1": {"max_user_bytes": "20GiB"}}}})
    await _hold(authenticated_context, 10 * GIB)

    result = await schema.execute(REQUEST_ZARR, context_value=authenticated_context)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_org_quota_counts_everyone_and_stays_in_its_org(
    db, authenticated_context: HttpContext, editor_context: HttpContext, other_org_context: HttpContext, quotas
):
    quotas(default={"max_org_bytes": 10 * GIB})
    await _hold(authenticated_context, 6 * GIB)
    await _hold(editor_context, 4 * GIB)

    result = await schema.execute(REQUEST_ZARR, context_value=editor_context)
    assert result.errors and "Organization storage quota" in _errors(result)

    result = await schema.execute(REQUEST_ZARR, context_value=other_org_context)
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_orphaned_store_frees_its_bytes(db, authenticated_context: HttpContext, quotas):
    from django.utils import timezone

    quotas(default={"max_user_bytes": 10 * GIB})
    await _hold(authenticated_context, 10 * GIB, orphaned_at=timezone.now())

    result = await schema.execute(REQUEST_ZARR, context_value=authenticated_context)
    assert not result.errors, result.errors


# --- resolution (no database) ------------------------------------------------


def test_resolution_is_most_specific_first():
    config = quota.QuotaConfig(
        default={"max_upload_bytes": "1GiB", "max_user_bytes": "1GiB", "max_org_bytes": "1GiB"},
        organizations={"lab": {"max_user_bytes": "2GiB", "users": {"alice": {"max_upload_bytes": "3GiB"}}}},
    )

    alice = quota.resolve_quota(config, "lab", "alice")
    assert (alice.max_upload_bytes, alice.max_user_bytes, alice.max_org_bytes) == (3 * GIB, 2 * GIB, GIB)

    bob = quota.resolve_quota(config, "lab", "bob")
    assert (bob.max_upload_bytes, bob.max_user_bytes) == (GIB, 2 * GIB)

    elsewhere = quota.resolve_quota(config, "other", "alice")
    assert (elsewhere.max_upload_bytes, elsewhere.max_user_bytes) == (GIB, GIB)

    assert quota.resolve_quota(quota.QuotaConfig(), "lab", None) == quota.Quota(None, None, None)

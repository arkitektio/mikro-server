"""Registering a mesh collection: a collection *is* a fabriks store.

Mostly tests about what the API **refuses to be told**. A fabriks store states its own grid,
encoding, level count and format version in `fabriks.json`, which the server read when the
upload was finished, so there is no way to pass any of those through this mutation. A second
statement of the same fact is free to disagree with the bytes, and nothing downstream could say
which of the two was right.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateMeshCollectionInput!) {
  createMeshCollection(input: $input) {
    id
    version
    specVersion
    grid
    encoding
    store { id key specVersion grid encoding axes }
    coordinateSystem { id axes { name type } }
  }
}
"""

AXES = [{"name": "z", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]


async def _create(ctx: HttpContext, payload: dict):
    return await schema.execute(CREATE, context_value=ctx, variable_values={"input": payload})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_is_its_store_and_declares_nothing_it_can_read(authenticated_context: HttpContext):
    """The whole point of a self-describing artifact, end to end.

    The call names a store and its axes. It does not name a grid, an encoding, a version, a
    catalog or a level -- all of which the manifest already stated and the server already read.
    They come back off the API anyway, because a renderer configures its decoder from what it
    queries, so what is stored is what the writer wrote rather than what nobody typed.
    """
    store = await seed.create_fabriks_store(authenticated_context)

    result = await _create(authenticated_context, {"version": "v20260814-fabriks", "axes": AXES, "store": str(store.pk)})
    assert not result.errors, result.errors

    collection = result.data["createMeshCollection"]
    assert collection["specVersion"] == "fabriks/1", "namespaced, so the format's version is never confused for the collection's own"
    assert collection["grid"] == seed.FABRIKS_GRID
    assert collection["encoding"] == seed.FABRIKS_ENCODING
    assert collection["store"]["id"] == str(store.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_geometry_cannot_be_described_through_the_api_at_all(authenticated_context: HttpContext):
    """Not merely ignored -- absent from the schema.

    A second statement of the same fact is free to disagree with the bytes, and nothing
    downstream could say which of the two was right. The strongest form of "do not declare
    this" is having nowhere to declare it, so the fields are gone rather than validated.
    """
    sdl = schema.as_str()
    input_def = sdl[sdl.find("input CreateMeshCollectionInput ") : sdl.find("\n}", sdl.find("input CreateMeshCollectionInput "))]

    for gone in ("catalog", "objectCatalog", "geometry", "shards", "grid", "encoding", "specVersion", "validateStores"):
        assert f"\n  {gone}" not in input_def, f"`{gone}` is stated by the store's manifest, so the API must not offer a second place to state it"

    assert "\n  store" in input_def
    assert "MeshGeometryShard" not in sdl


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unfinished_store_is_refused(authenticated_context: HttpContext):
    """Nothing is known about a prefix whose manifest was never read.

    `finishFabriksUpload` is the step that reads `fabriks.json` and refuses a tree that has none,
    so a store that never reached it is a half-written upload as far as anything here can tell.
    Registering it would record a collection whose grid and encoding are simply unknown.
    """
    store = await seed.create_fabriks_store(authenticated_context, populated=False)

    result = await _create(authenticated_context, {"version": "v1", "axes": AXES, "store": str(store.pk)})
    assert result.errors
    assert "has not been finished" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_declared_axes_are_checked_against_the_manifest(authenticated_context: HttpContext):
    """The transposition trap.

    An axis order the server cannot check is a claim nothing tests: the rank matches, the
    derivation edge is accepted, the layer places, and everything draws sideways. The manifest
    gives the server a second statement to compare against, so the mistake becomes a refusal.

    A manifest that states no axes is not a disagreement -- nothing in the format decodes
    through them, so a writer that never claimed an order is simply not answering the question.
    """
    disagreeing = await seed.create_fabriks_store(authenticated_context, axes=["x", "y", "z"])
    result = await _create(authenticated_context, {"version": "v1", "axes": AXES, "store": str(disagreeing.pk)})
    assert result.errors
    assert "draw transposed" in str(result.errors[0])

    agreeing = await seed.create_fabriks_store(authenticated_context, axes=["z", "y", "x"])
    assert not (await _create(authenticated_context, {"version": "v2", "axes": AXES, "store": str(agreeing.pk)})).errors

    silent = await seed.create_fabriks_store(authenticated_context, axes=None)
    assert not (await _create(authenticated_context, {"version": "v3", "axes": AXES, "store": str(silent.pk)})).errors, "a manifest that claims no axis order does not contradict one"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_store_from_another_organization_is_not_registerable(authenticated_context: HttpContext, other_org_context: HttpContext):
    """Org scoping, on the one id this mutation now takes.

    Worth its own test because the surface shrank to a single store reference: if that one
    lookup were unscoped, a collection could point at another organization's bytes and the
    access grant would follow.
    """
    foreign = await seed.create_fabriks_store(other_org_context)

    result = await _create(authenticated_context, {"version": "v1", "axes": AXES, "store": str(foreign.pk)})
    assert result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_collection_leaves_its_store_collectable(authenticated_context: HttpContext):
    """One collection, one prefix -- and the sweep needs no special case for it."""
    store = await seed.create_fabriks_store(authenticated_context)
    created = await _create(authenticated_context, {"version": "v1", "axes": AXES, "store": str(store.pk)})
    assert not created.errors, created.errors

    result = await schema.execute(
        "mutation D($input: DeleteMeshCollectionInput!) { deleteMeshCollection(input: $input) }",
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshCollection"]["id"]}},
    )
    assert not result.errors, result.errors

    refreshed = await sync_to_async(models.FabriksStore.objects.get)(pk=store.pk)
    assert refreshed.orphaned_at is not None, "nothing points at that prefix any more"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_organization_wide_read_access_covers_the_fabriks_bucket(authenticated_context: HttpContext):
    """`requestGeneralFabriksAccess` is reachable and returns usable credentials.

    There is no single `requestGeneralAccess` -- the grants are per logical bucket, each naming
    the one it is for -- so "general access covers fabriks" means this mutation existing *and*
    being wired into the schema. The second half is a separate mistake to make: a resolver that
    is written and never registered is simply absent from the API, with nothing to notice.
    """
    result = await schema.execute(
        "mutation G($input: RequestGeneralFabriksAccessInput!) { requestGeneralFabriksAccess(input: $input) { accessKey secretKey bucket status } }",
        context_value=authenticated_context,
        variable_values={"input": {}},
    )
    assert not result.errors, result.errors

    grant = result.data["requestGeneralFabriksAccess"]
    assert grant["bucket"], "the grant names the bucket it is for"
    assert grant["accessKey"] and grant["secretKey"]
    assert grant["status"] == "granted"

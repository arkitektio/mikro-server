"""``prune_organizations`` on mikro's own graph: everything an organization owns goes, nothing else does.

The command knows no mikro model (it is arkitekt-service's); these tests hold it to mikro's
PROTECT/RESTRICT web -- datasets in their systems, a dataset derived from a lens, a refined
shape, a correction superseding a metric, a scene's registration -- seeded through the writers.
"""

from io import StringIO

import arkitekt_datalayer.datalayer as datalayer_module

import pytest
from arkitekt_service.feed.models import FeedEntry
from arkitekt_service.server import prune
from asgiref.sync import sync_to_async
from authentikate.models import Organization
from django.core.management import CommandError, call_command
from kante.context import HttpContext

from core import enums, models
from tests import seed
from tests.test_annotation_lineage import CORNERS, _draw_from, _drawn_on
from tests.test_cross_container_derivation import _derived
from tests.test_lens_geometry import _draw
from tests.test_metrics import CREATE, quantity, run
from tests.test_store_purging import buckets, keys_in, put  # noqa: F401  the fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

OURS = "static_issuer#static_org"


def _prune(*args: str) -> str:
    out = StringIO()
    call_command("prune_organizations", *args, stdout=out, stderr=out)
    return out.getvalue()


def _owned(identifier: str) -> dict[str, int]:
    return prune.in_scope([Organization.objects.get(identifier=identifier)])[identifier]


async def _everything(ctx: HttpContext, layer: datalayer_module.Datalayer, key: str) -> None:
    """An organization's lab: derived data, a refined shape, corrected metrics, a scene, a file and its bytes."""
    raw = await seed.create_array_dataset(ctx, f"Raw-{key}", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(ctx, raw, [{"axis": "y", "start": 0, "stop": 32}])
    await _derived(ctx, f"Mask-{key}", lens=lens)

    first = (await run(ctx, CREATE, {"input": quantity(raw)}))["createMetric"]
    await run(ctx, CREATE, {"input": quantity(raw, magnitude=30.9, supersedes=first["id"])})

    scene = await seed.create_scene(ctx, f"Scene-{key}")
    await seed.register_into_scene(ctx, scene, raw)

    store = await models.BigFileStore.objects.acreate(path=f"s3://bigfile/{key}", bucket="bigfile", key=key, populated=True, organization=ctx.request.organization)
    await seed.create_file(ctx, f"{key}.czi", await seed.create_folder(ctx, key), store=store)
    await sync_to_async(put)(layer, "bigfile", key)


async def test_an_organization_is_pruned_through_every_link_and_its_bytes_go(
    authenticated_context: HttpContext,
    other_org_context: HttpContext,
    buckets: datalayer_module.Datalayer,  # noqa: F811
) -> None:
    """Derived data, refined shapes, corrected metrics and a registered scene all go; kraph hears of the metrics."""
    ctx = authenticated_context
    await _everything(ctx, buckets, "ours")
    _, collection = await _drawn_on(ctx)
    rough = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    refined = await _draw_from(ctx, collection["id"], "RECTANGLE", [[1.0, 2.0], [4.0, 6.0]], [{"annotation": rough["id"], "relation": "REFINED"}])
    assert not refined.errors, refined.errors

    await _everything(other_org_context, buckets, "theirs")
    theirs = await sync_to_async(_owned)(other_org_context.request.organization.identifier)

    dry = await sync_to_async(_prune)("--only", OURS)
    assert "pass --yes" in dry and (await models.ArrayDataset.objects.filter(organization=ctx.request.organization).acount()) == 3

    out = await sync_to_async(_prune)("--only", OURS, "--yes")

    assert "Pruned 1 organization(s)" in out, out
    assert await sync_to_async(_owned)(OURS) == {}, "nothing it owned is left"
    assert not await models.Annotation.objects.filter(collection__organization=ctx.request.organization).aexists()
    assert await sync_to_async(_owned)(other_org_context.request.organization.identifier) == theirs, "another organization keeps all it has"
    assert await Organization.objects.filter(identifier=OURS).aexists(), "the organization stays"

    assert await sync_to_async(keys_in)(buckets, "bigfile") == {"theirs"}, "the bytes went with their store"

    deleted = [e async for e in FeedEntry.objects.filter(feed="@mikro/metric", kind="DELETED")]
    assert len(deleted) == 2 and {e.organization for e in deleted} == {OURS}, "kraph hears of every metric that went"

    assert "Nothing to prune" in await sync_to_async(_prune)("--only", OURS, "--yes")


async def test_a_dataset_another_organization_registered_is_left_and_named(authenticated_context: HttpContext, other_org_context: HttpContext) -> None:
    """A guest placed our dataset in their scene: their edge holds our system, and it is not ours to delete."""
    ctx = authenticated_context
    raw = await seed.create_array_dataset(ctx, "Shared", axes=seed.YX_AXES, shapes=[[16, 16]])
    other = await seed.create_array_dataset(ctx, "Unshared", axes=seed.YX_AXES, shapes=[[16, 16]])
    scene = await seed.create_scene(other_org_context, "Theirs")
    edge = await seed.register_into_scene(other_org_context, scene, raw)

    with pytest.raises(CommandError, match="row"):
        await sync_to_async(_prune)("--only", OURS, "--yes")

    assert await models.Transformation.objects.filter(pk=edge.pk).aexists(), "their edge stands"
    assert await models.CoordinateSystem.objects.filter(pk=raw.coordinate_system_id).aexists()
    assert not await models.ArrayDataset.objects.filter(pk=other.pk).aexists(), "what nobody else holds goes"


async def test_a_layer_another_organization_draws_through_our_lens_is_not_taken_along(authenticated_context: HttpContext, other_org_context: HttpContext) -> None:
    """A layer has no organization of its own: it is its scene's, so their layer over our lens holds the lens."""
    ctx = authenticated_context
    raw = await seed.create_array_dataset(ctx, "Shared", axes=seed.YX_AXES, shapes=[[16, 16]])
    lens = await seed.create_lens(ctx, raw, [{"axis": "y", "start": 0, "stop": 8}])
    scene = await seed.create_scene(other_org_context, "Theirs")
    layer = await models.Layer.objects.acreate(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)

    err = StringIO()
    with pytest.raises(CommandError, match="row"):
        await sync_to_async(call_command)("prune_organizations", "--only", OURS, "--yes", stdout=StringIO(), stderr=err)

    assert f"core.Layer {layer.pk}, of another organization (through scene)" in err.getvalue(), err.getvalue()

    assert await models.Layer.objects.filter(pk=layer.pk, lens_id=lens.pk).aexists(), "their layer still draws through our lens"

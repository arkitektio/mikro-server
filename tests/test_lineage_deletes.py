"""Deletes follow lineage: nothing goes while something made from it stands, unless `cascade` takes it along.

`core.logic.deletion` reads what is downstream off the same walk `lineage` does, so these
tests seed lineage the way the other lineage suites do -- through the real mutations -- and
delete through the real delete mutations and `preflightDelete<Model>` queries.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.models import RestrictedError
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed
from tests.test_annotation_lineage import CORNERS, PATH, SMOOTHER, _draw_from, _drawn_on
from tests.test_condensed_placement import _fresh_request
from tests.test_cross_container_derivation import _derived
from tests.test_derived_datasets import _derive
from tests.test_lens_geometry import _draw, _lens_from

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

DELETE_DATASET = "mutation D($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }"
DELETE_ANNOTATION = "mutation D($input: DeleteAnnotationInput!) { deleteAnnotation(input: $input) }"
DELETE_COLLECTION = "mutation D($input: DeleteAnnotationCollectionInput!) { deleteAnnotationCollection(input: $input) }"
DELETE_FILE = "mutation D($input: DeleteFileInput!) { deleteFile(input: $input) }"
DELETE_LENS = "mutation D($input: DeleteLensInput!) { deleteLens(input: $input) }"

PREFLIGHT_FIELDS = "target casualties { key kind id name depth parent } blockers { key kind reason } cascadeCounts { label count } truncated needsCascade deletable"
PREFLIGHT_DATASET = f"query P($id: ID!) {{ preflightDeleteArrayDataset(id: $id) {{ {PREFLIGHT_FIELDS} }} }}"
PREFLIGHT_ANNOTATION = f"query P($id: ID!) {{ preflightDeleteAnnotation(id: $id) {{ {PREFLIGHT_FIELDS} }} }}"
PREFLIGHT_COLLECTION = f"query P($id: ID!) {{ preflightDeleteAnnotationCollection(id: $id) {{ {PREFLIGHT_FIELDS} }} }}"


async def _run(ctx: HttpContext, query: str, variables: dict):  # noqa: ANN202 - strawberry's ExecutionResult
    return await schema.execute(query, context_value=_fresh_request(ctx), variable_values=variables)


async def _chain(ctx: HttpContext) -> tuple[models.ArrayDataset, str, str]:
    """Raw --derive--> Mask --derive--> Cleaned."""
    raw = await seed.create_array_dataset(ctx, "Raw", axes=seed.YX_AXES, shapes=[[64, 64]])
    mask = await _derived(ctx, "Mask", lens=await seed.create_lens(ctx, raw))
    mask_row = await sync_to_async(models.ArrayDataset.objects.get)(pk=mask)
    cleaned = await _derived(ctx, "Cleaned", lens=await seed.create_lens(ctx, mask_row))
    return raw, mask, cleaned


async def test_a_dataset_something_was_derived_from_is_not_deleted_alone(authenticated_context: HttpContext) -> None:
    """Refused without cascade, listed by preflight farthest-last, and taken along with it."""
    ctx = authenticated_context
    raw, mask, cleaned = await _chain(ctx)

    refused = await _run(ctx, DELETE_DATASET, {"input": {"id": str(raw.pk)}})
    assert refused.errors and "2 thing(s) were made from it" in refused.errors[0].message, refused.errors
    assert await models.ArrayDataset.objects.acount() == 3, "a refusal deletes nothing"

    preflight = (await _run(ctx, PREFLIGHT_DATASET, {"id": str(raw.pk)})).data["preflightDeleteArrayDataset"]
    assert preflight["needsCascade"] and preflight["deletable"] and not preflight["truncated"]
    assert [(c["key"], c["depth"]) for c in preflight["casualties"]] == [(f"dataset:{mask}", 1), (f"dataset:{cleaned}", 2)]
    assert preflight["casualties"][1]["parent"] == f"dataset:{mask}"

    cascaded = await _run(ctx, DELETE_DATASET, {"input": {"id": str(raw.pk), "cascade": True}})
    assert not cascaded.errors, cascaded.errors
    assert await models.ArrayDataset.objects.acount() == 0

    # A leaf has nothing below it: the plain delete is what it always was.
    leaf = await seed.create_array_dataset(ctx, "Leaf", axes=seed.YX_AXES, shapes=[[8, 8]])
    assert (await _run(ctx, PREFLIGHT_DATASET, {"id": str(leaf.pk)})).data["preflightDeleteArrayDataset"]["needsCascade"] is False
    assert not (await _run(ctx, DELETE_DATASET, {"input": {"id": str(leaf.pk)}})).errors


async def test_a_shape_takes_its_refinements_and_the_data_computed_within_it(authenticated_context: HttpContext) -> None:
    """A refined shape and the crop cut along it are downstream of the shape; the image it was cut from is not."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rough = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    refined = await _draw_from(ctx, collection["id"], "RECTANGLE", [[1.0, 2.0], [4.0, 6.0]], [{"annotation": rough["id"], "relation": "REFINED"}])
    assert not refined.errors, refined.errors
    refined_id = refined.data["createAnnotation"]["id"]
    cut = await _lens_from(ctx, dataset, annotation=refined_id)
    crop = await _derive(ctx, "Crop", axes=seed.YX_AXES, shape=[4, 5], entries=[{"kind": "LENS", "lens": cut["id"], "transform": {"kind": "IDENTITY"}}, {"kind": "ANNOTATION", "annotation": refined_id}])
    assert not crop.errors, crop.errors
    crop_id = crop.data["createArrayDataset"]["id"]

    refused = await _run(ctx, DELETE_ANNOTATION, {"input": {"id": rough["id"]}})
    assert refused.errors and "were made from it" in refused.errors[0].message

    answer = await _run(ctx, PREFLIGHT_ANNOTATION, {"id": rough["id"]})
    assert not answer.errors, answer.errors
    preflight = answer.data["preflightDeleteAnnotation"]
    assert [c["key"] for c in preflight["casualties"]] == [f"annotation:{refined_id}", f"dataset:{crop_id}"]

    # The ORM backstop: a source is RESTRICT on its child, whoever deletes.
    with pytest.raises(RestrictedError):
        await sync_to_async(models.Annotation.objects.filter(pk=rough["id"]).delete)()

    cascaded = await _run(ctx, DELETE_ANNOTATION, {"input": {"id": rough["id"], "cascade": True}})
    assert not cascaded.errors, cascaded.errors
    assert not await models.Annotation.objects.filter(pk__in=[rough["id"], refined_id]).aexists()
    assert not await models.ArrayDataset.objects.filter(pk=crop_id).aexists()
    assert await models.ArrayDataset.objects.filter(pk=dataset.pk).aexists(), "what the crop was cut from is upstream, not downstream"


async def test_a_collection_takes_what_was_made_from_its_shapes_elsewhere(authenticated_context: HttpContext) -> None:
    """Its own shapes go with it; a shape converted into another collection is a casualty."""
    ctx = authenticated_context
    _, collection = await _drawn_on(ctx, "Drawn")
    _, other = await _drawn_on(ctx, "Elsewhere")
    rough = await _draw(ctx, collection["id"], "PATH", PATH)
    in_place = await _draw_from(ctx, collection["id"], "PATH", SMOOTHER, [{"annotation": rough["id"], "relation": "REFINED"}])
    converted = await _draw_from(ctx, other["id"], "POLYGON", CORNERS, [{"annotation": rough["id"], "relation": "CONVERTED"}])
    assert not in_place.errors and not converted.errors
    converted_id = converted.data["createAnnotation"]["id"]

    answer = await _run(ctx, PREFLIGHT_COLLECTION, {"id": collection["id"]})
    assert not answer.errors, answer.errors
    preflight = answer.data["preflightDeleteAnnotationCollection"]
    assert [c["key"] for c in preflight["casualties"]] == [f"annotation:{converted_id}"], "the in-place refinement is the collection's own shape and goes with it"
    assert {"label": "annotation", "count": 2} in preflight["cascadeCounts"], preflight["cascadeCounts"]

    refused = await _run(ctx, DELETE_COLLECTION, {"input": {"id": collection["id"]}})
    assert refused.errors and "were made from it" in refused.errors[0].message
    cascaded = await _run(ctx, DELETE_COLLECTION, {"input": {"id": collection["id"], "cascade": True}})
    assert not cascaded.errors, cascaded.errors
    assert not await models.Annotation.objects.filter(pk=converted_id).aexists()
    assert await models.AnnotationCollection.objects.filter(pk=other["id"]).aexists(), "the other collection stays; only the shape made from ours goes"


async def test_a_file_takes_the_series_converted_out_of_it(authenticated_context: HttpContext) -> None:
    """Ingest is lineage too: the series converted out of a file are made from it."""
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Raw")
    raw = await seed.create_file(ctx, "raw.czi", folder)
    series = await seed.create_array_dataset(ctx, "Series", axes=seed.YX_AXES, shapes=[[8, 8]])
    await sync_to_async(models.FileLink.objects.create)(file=raw, dataset=series, direction=enums.FileLinkDirectionChoices.SOURCE.value, organization=ctx.request.organization)

    refused = await _run(ctx, DELETE_FILE, {"input": {"id": str(raw.pk)}})
    assert refused.errors and "were made from it" in refused.errors[0].message
    assert not (await _run(ctx, DELETE_FILE, {"input": {"id": str(raw.pk), "cascade": True}})).errors
    assert not await models.ArrayDataset.objects.filter(pk=series.pk).aexists()


async def test_a_cascade_holds_every_casualty_to_its_own_owner(authenticated_context: HttpContext, bot_context: HttpContext) -> None:
    """The bot may delete its own dataset, not the admin's dataset derived from it."""
    theirs = await seed.create_array_dataset(bot_context, "Bot's", axes=seed.YX_AXES, shapes=[[64, 64]])
    derived = await _derived(authenticated_context, "Admin's", lens=await seed.create_lens(authenticated_context, theirs))

    preflight = (await _run(bot_context, PREFLIGHT_DATASET, {"id": str(theirs.pk)})).data["preflightDeleteArrayDataset"]
    assert preflight["deletable"] is False
    assert [blocker["key"] for blocker in preflight["blockers"]] == [f"dataset:{derived}"]

    refused = await _run(bot_context, DELETE_DATASET, {"input": {"id": str(theirs.pk), "cascade": True}})
    assert refused.errors and "Only the creator" in refused.errors[0].message
    assert await models.ArrayDataset.objects.acount() == 2, "nothing goes when one casualty may not"


async def test_a_lens_something_was_derived_from_is_not_deleted(authenticated_context: HttpContext) -> None:
    """A crop whose own space something was derived from stays; deleting the dataset can take both."""
    ctx = authenticated_context
    raw = await seed.create_array_dataset(ctx, "Raw", axes=seed.YX_AXES, shapes=[[64, 64]])
    crop = await seed.create_lens(ctx, raw, slices=[{"axis": "y", "start": 0, "stop": 32}])
    await _derived(ctx, "FromCrop", lens=crop)

    refused = await _run(ctx, DELETE_LENS, {"input": {"id": str(crop.pk)}})
    assert refused.errors and "were derived from its selection" in refused.errors[0].message, refused.errors

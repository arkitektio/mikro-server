"""`merge_duplicate_lenses`: the lenses written before one selection was one row are converged.

Rows are written raw here, the way the old code wrote them: a sliced array lens per `createLens`
call, spelled as the caller spelled it. The database now refuses two rows with one spelling, so
what is left to fold is two spellings of one selection -- and what is left to backfill is the
whole lens of a container that never had one.
"""

from io import StringIO

import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from tests import seed


def _legacy_sliced_lens(ctx: HttpContext, dataset: models.ArrayDataset, slices: list[dict]) -> models.ArrayLens:
    """A sliced lens exactly as `create_lens` wrote it before normalization: its system, its axes, its edge from the spelled starts."""
    creation = seed._creation(ctx)
    system = models.CoordinateSystem.objects.create(name=f"{dataset.name}/lens", creator=creation.user, organization=creation.organization)
    lens = models.ArrayLens.objects.create(dataset=dataset, coordinate_system=system, organization=dataset.organization, slices=slices)
    graph_logic.create_pixel_axes(system, dataset.axes)
    graph_logic.create_lens_edge(lens_system=system, parent_system=dataset.coordinate_system, dataset_axis_names=dataset.axis_names, slices=lens.slices_list, ctx=creation)
    return lens


async def _run(dry_run: bool = False) -> str:
    out = StringIO()
    args = ["--dry-run"] if dry_run else []
    await sync_to_async(call_command)("merge_duplicate_lenses", *args, stdout=out, stderr=out)
    return out.getvalue()


async def _lens_ids(dataset: models.ArrayDataset) -> list[int]:
    return [pk async for pk in models.ArrayLens.objects.filter(dataset=dataset).order_by("pk").values_list("pk", flat=True)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_job_converges_what_the_old_code_wrote_and_then_does_nothing(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Image", shapes=[[3, 64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    (whole_id,) = await _lens_ids(dataset)
    scene = await seed.create_scene(ctx, "A")

    # One crop, spelled three ways, as three `createLens` calls once wrote it. The second
    # spelling's edge was written from its negative start. The third's system has a
    # registration of its own, so it cannot be folded.
    first = await sync_to_async(_legacy_sliced_lens)(ctx, dataset, [{"axis": "y", "start": -56, "stop": 40, "step": None}])
    second = await sync_to_async(_legacy_sliced_lens)(ctx, dataset, [{"axis": "y", "start": 8, "stop": 40, "step": None}])
    third = await sync_to_async(_legacy_sliced_lens)(ctx, dataset, [{"axis": "y", "start": 8, "stop": -24, "step": None}])
    layer_second = await models.Layer.objects.acreate(scene=scene, kind=enums.LayerKindChoices.IMAGE.value, lens=second)
    layer_third = await models.Layer.objects.acreate(scene=scene, kind=enums.LayerKindChoices.IMAGE.value, lens=third)
    elsewhere = await models.CoordinateSystem.objects.acreate(name="elsewhere", organization=ctx.request.organization)
    await models.Transformation.objects.acreate(kind=enums.TransformKindChoices.TRANSLATION.value, input_id=third.coordinate_system_id, output=elsewhere, params={"translation": [0.0, 0.0, 0.0]}, organization=ctx.request.organization)
    first_edge = await models.Transformation.objects.aget(input_id=first.coordinate_system_id, output=intrinsic, parent__isnull=True)
    assert first_edge.params["translation"] == [0.0, -56.0, 0.0], "the old edge recorded the spelling, not the crop"

    # A dataset that never got a whole lens.
    bare = await seed.create_array_dataset(ctx, "Bare")
    await models.Lens.objects.filter(arraylens__dataset=bare).adelete()

    # A dry run names everything and writes nothing.
    preview = await _run(dry_run=True)
    assert "would backfill" in preview and "would repair" in preview and f"would normalize lens {first.pk}" in preview and f"would merge lens {second.pk} into lens {first.pk}" in preview
    assert await _lens_ids(dataset) == [whole_id, first.pk, second.pk, third.pk]
    assert await _lens_ids(bare) == []

    report = await _run()

    # The three are one selection: `second` folds onto `first` (its system is private), `third`
    # stays because its system is registered elsewhere, and `first` -- the keeper -- is then
    # rewritten in the one spelling and its edge repaired from it.
    assert await _lens_ids(dataset) == [whole_id, first.pk, third.pk]
    assert (await models.Layer.objects.aget(pk=layer_second.pk)).lens_id == first.pk
    assert (await models.Layer.objects.aget(pk=layer_third.pk)).lens_id == third.pk

    kept = await models.ArrayLens.objects.aget(pk=first.pk)
    assert kept.slices == [{"axis": "y", "start": 8, "stop": 40, "step": 1}], "normalized in place"
    assert not await models.CoordinateSystem.objects.filter(pk=second.coordinate_system_id).aexists(), "the folded crop's system went with it"
    assert await models.CoordinateSystem.objects.filter(pk=third.coordinate_system_id).aexists(), "the crop something else is registered from stays"
    assert f"lens {third.pk} duplicates lens {first.pk} but is left in place" in report
    assert "repaired edge" in report and f"of lens {first.pk}" in report
    first_edge = await models.Transformation.objects.aget(input_id=first.coordinate_system_id, output=intrinsic, parent__isnull=True)
    assert first_edge.params["translation"] == [0.0, 8.0, 0.0], "the edge now says where the crop is"

    backfilled = await _lens_ids(bare)
    assert len(backfilled) == 1
    assert (await models.ArrayLens.objects.aget(pk=backfilled[0])).slices == []

    # Run again: nothing is owed.
    again = await _run()
    assert "normalized 0 lens(es), repaired 0 edge(s), backfilled 0 whole lens(es), merged 0 unsliced and 0 sliced duplicate(s)" in again
    assert await _lens_ids(dataset) == [whole_id, first.pk, third.pk]
    assert "1 duplicate sliced lens(es) left in place" in again, "what it cannot fold it keeps reporting"

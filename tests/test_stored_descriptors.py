"""The descriptors of mikro's files, spaces, edges, anchors and links: stored on the row, and asked of it by a port's ``requires``.

Every structure that declares descriptors keeps them on its row (``stored_descriptors``), so a
picker filters on them in SQL. What a save of the row cannot see -- the axes written after a
space, the arrays after a dataset -- is restored by whoever writes it; the setup job gives a row
what it still owes.
"""

import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed
from tests.test_derived_datasets import _derive


async def _ids(ctx: HttpContext, field: str, filter_type: str, requires: list[dict]) -> set[str]:
    result = await schema.execute(f"query Q($filters: {filter_type}) {{ {field}(filters: $filters) {{ id }} }}", context_value=ctx, variable_values={"filters": {"requires": requires}})
    assert not result.errors, result.errors
    return {row["id"] for row in result.data[field]}


def _stored(model: type, pk: object) -> dict:
    return model.objects.values_list("stored_descriptors", flat=True).get(pk=pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_is_found_by_its_extension_when_its_media_type_says_nothing(authenticated_context: HttpContext):
    """A microscope's file arrives as application/octet-stream: its extension is what picks the importer."""
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "acquisitions")
    stack = await seed.create_file(ctx, "Cells_01.OME.TIF", folder, content_type="application/octet-stream", size=4_000_000_000)
    notes = await seed.create_file(ctx, "notes.txt", folder, content_type="text/plain", size=120)
    unnamed = await seed.create_file(ctx, "README", folder)

    assert await sync_to_async(_stored)(models.File, stack.pk) == {"@mikro/content_type": "application/octet-stream", "@mikro/extension": "ome.tif", "@mikro/size_bytes": 4_000_000_000}
    assert (await sync_to_async(_stored)(models.File, unnamed.pk))["@mikro/extension"] == ""

    importable = [{"key": "@mikro/extension", "operator": "IN", "value": ["czi", "nd2", "ome.tif"]}]
    assert await _ids(ctx, "files", "FileFilter!", importable) == {str(stack.pk)}
    previewable = [{"key": "@mikro/size_bytes", "operator": "LTE", "value": 10_000_000}]
    assert await _ids(ctx, "files", "FileFilter!", previewable) == {str(notes.pk), str(unnamed.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_counts_the_axes_written_after_it_and_an_edge_says_what_kind_of_map_it_is(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "plane", axes=seed.YX_AXES, shapes=[[64, 64]])
    physical = await seed.create_physical_space(ctx, dataset, axes=[seed.physical_axis("y", enums.AxisType.SPACE, "um"), seed.physical_axis("x", enums.AxisType.SPACE, "um")], scale=[0.5, 0.5])

    stored = await sync_to_async(_stored)(models.CoordinateSystem, physical.pk)
    assert (stored["@mikro/n_space_axes"], stored["@mikro/n_time_axes"], stored["@mikro/n_channel_axes"]) == (2, 0, 0)
    planes = await _ids(ctx, "coordinateSystems", "CoordinateSystemFilter!", [{"key": "@mikro/n_space_axes", "operator": "EQUALS", "value": 2}])
    assert {str(physical.pk), str(dataset.coordinate_system_id)} <= planes
    assert not await _ids(ctx, "coordinateSystems", "CoordinateSystemFilter!", [{"key": "@mikro/n_space_axes", "operator": "EQUALS", "value": 3}])

    edge = await models.Transformation.objects.aget(input_id=dataset.coordinate_system_id, output=physical)
    assert edge.stored_descriptors == {
        "@mikro/transform_kind": "SCALE",
        "@mikro/validity": "INFERRED",
        "@mikro/value_relation": "",
        "@mikro/n_input_axes": 2,
        "@mikro/n_output_axes": 2,
    }
    # A refining action asks for what is only inferred; once validated, the edge is no longer offered.
    to_refine = [{"key": "@mikro/validity", "operator": "IN", "value": ["INFERRED", "UNKNOWN"]}, {"key": "@mikro/transform_kind", "operator": "EQUALS", "value": "SCALE"}]
    assert str(edge.pk) in await _ids(ctx, "transformations", "TransformationFilter!", to_refine)
    edge.validity = enums.PlacementValidityChoices.VALIDATED.value
    await edge.asave(update_fields=["validity"])
    assert str(edge.pk) not in await _ids(ctx, "transformations", "TransformationFilter!", to_refine)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_and_a_file_link_say_which_kind_of_container_they_belong_to(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "stack")
    file = await seed.create_file(ctx, "stack.czi", await seed.create_folder(ctx, "raw"))
    anchor = await models.CoordinateAnchor.objects.acreate(dataset=dataset, coordinates={"c": 0})
    link = await models.FileLink.objects.acreate(file=file, dataset=dataset, direction=enums.FileLinkDirectionChoices.SOURCE.value, organization=ctx.request.organization, creator=ctx.request.user)

    assert await sync_to_async(_stored)(models.CoordinateAnchor, anchor.pk) == {"@mikro/container_kind": "ARRAY"}
    assert await sync_to_async(_stored)(models.FileLink, link.pk) == {"@mikro/link_direction": "SOURCE", "@mikro/container_kind": "ARRAY"}
    assert await _ids(ctx, "coordinateAnchors", "CoordinateAnchorFilter!", [{"key": "@mikro/container_kind", "operator": "EQUALS", "value": "ARRAY"}]) == {str(anchor.pk)}
    assert not await _ids(ctx, "coordinateAnchors", "CoordinateAnchorFilter!", [{"key": "@mikro/container_kind", "operator": "EQUALS", "value": "TABLE"}])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_is_found_by_the_element_type_of_its_store(authenticated_context: HttpContext):
    """An action typed on integer labels asks for them: the dtype is its level-0 store's."""
    ctx = authenticated_context
    source = await seed.create_array_dataset(ctx, "raw", axes=seed.YX_AXES, shapes=[[64, 64]])
    derived = await _derive(ctx, "mask", axes=seed.YX_AXES, shape=[64, 64], lens=await seed.create_lens(ctx, source), valueRelation="CATEGORIZED")
    assert not derived.errors, derived.errors
    mask = derived.data["createArrayDataset"]["id"]

    labels = [{"key": "@mikro/dtype", "operator": "IN", "value": ["uint8", "uint16", "uint32"]}, {"key": "@mikro/value_kind", "operator": "EQUALS", "value": "categorical"}]
    assert await _ids(ctx, "arrayDatasets", "ArrayDatasetFilter!", labels) == {mask}
    mask_lenses = await _ids(ctx, "lenses", "LensFilter!", labels)
    assert mask_lenses and all([await models.ArrayLens.objects.filter(pk=pk, dataset_id=mask).aexists() for pk in mask_lenses])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_setup_job_gives_a_row_what_it_owes(authenticated_context: HttpContext):
    """A row written before its descriptors were stored has none: the setup job states them, and does nothing the second time."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "old")
    file = await seed.create_file(ctx, "old.nd2", await seed.create_folder(ctx, "archive"), size=10)
    expected = await sync_to_async(lambda: (_stored(models.ArrayDataset, dataset.pk), _stored(models.File, file.pk)))()
    await models.ArrayDataset.objects.filter(pk=dataset.pk).aupdate(stored_descriptors={})
    await models.File.objects.filter(pk=file.pk).aupdate(stored_descriptors={})

    await sync_to_async(call_command)("restore_descriptors")
    assert await sync_to_async(lambda: (_stored(models.ArrayDataset, dataset.pk), _stored(models.File, file.pk)))() == expected
    await sync_to_async(call_command)("restore_descriptors")
    assert await sync_to_async(lambda: (_stored(models.ArrayDataset, dataset.pk), _stored(models.File, file.pk)))() == expected

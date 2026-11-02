"""A lens is named by the positions it pins: `PositionLabel` over `ChannelLabel`, cached on `Lens.name`.

`core.logic.lens_names` is the rule; these hold it from the outside. A name sits at the anchor of
the positions a lens pins, every lens pinning them is called by it, a person's name wins over
what ingest recorded and gives it back when removed, a crop is named by the shape it was cut to,
and the cache follows every one of those writes -- and nothing else, so renaming a dataset
touches no lens.
"""

import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from kante.context import HttpContext

from core import enums, models
from core.logic import coords as coords_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request
from tests.test_lens_geometry import CREATE_LENS, _collection_on, _draw
from tests.test_table_specs import TRACKS
from tests.test_table_specs import _create as _create_table

NAME = "mutation N($input: SetPositionLabelInput!) { setPositionLabel(input: $input) { id name label nameAnchors { coordinates positionLabel { label } channelLabel { label } } } }"
FROM_LENS = "mutation F($input: CreateSceneFromLensInput!) { createSceneFromLens(input: $input) { id name } }"
UPDATE_ANNOTATION = "mutation U($input: UpdateAnnotationInput!) { updateAnnotation(input: $input) { id name } }"

CZYX = [seed.axis("c", enums.AxisType.CHANNEL), seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
CHANNEL_1 = [{"axis": "c", "start": 1, "stop": 2}]
CORNERS = [[1.0, 2.0], [1.0, 6.0], [4.0, 6.0], [4.0, 2.0]]


async def _name(ctx: HttpContext, lens: models.Lens, label: str | None) -> dict:
    result = await schema.execute(NAME, context_value=ctx, variable_values={"input": {"lens": str(lens.pk), "label": label}})
    assert not result.errors, result.errors
    return result.data["setPositionLabel"]


async def _recorded(dataset: models.ArrayDataset, coordinates: dict, label: str) -> None:
    """What ingest writes: a channel label on the anchor at these coordinates."""

    def write() -> None:
        anchor, _ = models.CoordinateAnchor.objects.get_or_create(dataset=dataset, coordinates=coordinates)
        models.ChannelLabel.objects.update_or_create(anchor=anchor, defaults={"label": label})

    await sync_to_async(write)()


async def _name_of(lens: models.Lens) -> str | None:
    return (await models.Lens.objects.aget(pk=lens.pk)).name


# --- the arithmetic ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selection", "pinned"),
    [
        ([], {}),
        ([{"axis": "c", "start": 1, "stop": 2, "step": 1}], {"c": 1}),
        ([{"axis": "c", "start": 1, "stop": 2, "step": 3}], {"c": 1}),
        ([{"axis": "c", "start": 1, "stop": 3, "step": 1}, {"axis": "t", "start": 4, "stop": 5, "step": 1}], {"t": 4}),
        ([{"axis": "t", "min": 4.0, "max": 4.0}, {"axis": "x", "min": 0.0, "max": 10.0}], {"t": 4.0}),
        ([{"axis": "t", "min": 4.0}], {}),
        ([{"axis": "t", "max": 4.0}], {}),
    ],
)
def test_the_pins_of_a_selection_are_the_axes_held_to_one_position(selection: list, pinned: dict):
    assert coords_logic.pins(selection) == pinned


# --- which labels hold ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_label_names_every_lens_through_its_position_and_no_other(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Stack", axes=CZYX, shapes=[[3, 8, 32, 32]])
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    crop = await seed.create_lens(ctx, dataset, [*CHANNEL_1, {"axis": "y", "start": 0, "stop": 16}])
    plane = await seed.create_lens(ctx, dataset, [*CHANNEL_1, {"axis": "z", "start": 5, "stop": 6}])
    other = await seed.create_lens(ctx, dataset, [{"axis": "c", "start": 0, "stop": 1}])
    whole = await seed.create_lens(ctx, dataset)

    named = await _name(ctx, channel, "GFP")

    assert named["name"] == "GFP" and named["label"] == "Stack · GFP · c[1:2]", "the selection stays beside a name every lens through channel 1 shares"
    assert [anchor["coordinates"] for anchor in named["nameAnchors"]] == [{"c": 1}]
    assert await _name_of(crop) == "GFP" and await _name_of(plane) == "GFP"
    assert await _name_of(other) is None and await _name_of(whole) is None

    await _recorded(dataset, {"c": 1, "z": 5}, "GFP, focal plane")
    assert await _name_of(plane) == "GFP, focal plane", "the most specific label wins"
    assert await _name_of(channel) == "GFP", "a label pinned to one z says nothing about every z"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_labels_on_different_axes_compose_in_axis_order(authenticated_context: HttpContext):
    ctx = authenticated_context
    axes = [seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
    dataset = await seed.create_array_dataset(ctx, "Movie", axes=axes, shapes=[[10, 3, 32, 32]])
    await _recorded(dataset, {"c": 1}, "GFP")
    await _recorded(dataset, {"t": 4}, "after washout")

    frame = await seed.create_lens(ctx, dataset, [{"axis": "t", "start": 4, "stop": 5}, *CHANNEL_1])

    assert frame.name == "after washout · GFP", "born named, the time axis first because it comes first"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_persons_name_wins_over_the_recorded_one_and_gives_it_back(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Recorded")
    await _recorded(dataset, {"c": 1}, "Alexa 488")
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    assert channel.name == "Alexa 488", "what ingest recorded names the lens until someone says otherwise"

    named = await _name(ctx, channel, "Microtubules")
    assert named["name"] == "Microtubules"
    assert named["nameAnchors"] == [{"coordinates": {"c": 1}, "positionLabel": {"label": "Microtubules"}, "channelLabel": {"label": "Alexa 488"}}], "the record is kept beside the name"

    cleared = await _name(ctx, channel, None)
    assert cleared["name"] == "Alexa 488"
    assert await models.PositionLabel.objects.acount() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bootstrapped_layer_is_called_what_its_channel_is_called(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    await _recorded(dataset, {"c": 0}, "DAPI")
    await _recorded(dataset, {"c": 1}, "GFP")
    await _name(ctx, await seed.create_lens(ctx, dataset, CHANNEL_1), "Microtubules")

    staged = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str((await seed.create_lens(ctx, dataset)).pk)}})
    assert not staged.errors, staged.errors

    names = [name async for name in models.Layer.objects.filter(scene_id=staged.data["createSceneFromLens"]["id"]).order_by("order", "pk").values_list("name", flat=True)]
    assert "DAPI" in names and "Microtubules" in names and "GFP" not in names


# --- what is not a position ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_cut_to_a_shape_is_called_by_it(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[[3, 6, 8]])
    collection = await _collection_on(ctx, axes=["c", "y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    await models.Annotation.objects.filter(pk=rectangle["id"]).aupdate(name="nucleus 3")
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    await _name(ctx, channel, "GFP")

    result = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": str(channel.pk), "annotation": rectangle["id"]}})
    assert not result.errors, result.errors
    cut = await models.Lens.objects.aget(pk=result.data["createLens"]["id"])
    assert cut.name == "GFP · nucleus 3", "the channel it pins, then the region it was cut to"

    renamed = await schema.execute(UPDATE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"id": rectangle["id"], "name": "nucleus 4"}})
    assert not renamed.errors, renamed.errors
    assert await _name_of(cut) == "GFP · nucleus 4"

    await models.Annotation.objects.filter(pk=rectangle["id"]).adelete()
    assert await _name_of(cut) == "GFP", "the cut went with its shape"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_renaming_a_dataset_touches_no_lens(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Before")
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    await _name(ctx, channel, "GFP")

    dataset.name = "After"
    await dataset.asave()

    row = await models.Lens.objects.aget(pk=channel.pk)
    assert row.name == "GFP"
    assert await sync_to_async(lambda: row.label)() == "After · GFP · c[1:2]"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_window_held_to_one_value_is_a_position(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await models.TableDataset.objects.aget(pk=(await _create_table(ctx, "tracks", TRACKS))["id"])
    frame = await seed.create_windowed_lens(ctx, table, [{"axis": "t", "min": 4.0, "max": 4.0}])
    span = await seed.create_windowed_lens(ctx, table, [{"axis": "t", "min": 0.0, "max": 4.0}])

    named = await _name(ctx, frame, "drug added")

    assert named["name"] == "drug added"
    assert await _name_of(span) is None, "a window that only bounds pins nothing"
    refused = await schema.execute(NAME, context_value=ctx, variable_values={"input": {"lens": str(span.pk), "label": "early"}})
    assert refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_setup_job_repairs_a_name_written_around_the_rule(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Drifted")
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    crop = await seed.create_lens(ctx, dataset, [{"axis": "y", "start": 0, "stop": 8}])
    await _name(ctx, channel, "GFP")
    await models.Lens.objects.filter(pk=channel.pk).aupdate(name="stale")
    await models.Lens.objects.filter(pk=crop.pk).aupdate(name="typed by hand")

    await sync_to_async(call_command)("merge_duplicate_lenses")
    assert await _name_of(channel) == "GFP" and await _name_of(crop) is None

    await sync_to_async(call_command)("merge_duplicate_lenses")
    assert await _name_of(channel) == "GFP", "the second run changes nothing"


# --- the migration that moved the typed names ------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_a_name_on_a_lens_of_nothing_but_positions_moves_to_them(authenticated_context: HttpContext):
    """`c[1:2]` called "GFP" names channel 1; `c[1:2], y[0:16]` called "nucleus 3" named a region, and must not name the channel."""
    import importlib

    from django.apps import apps

    migration = importlib.import_module("core.migrations.0003_lens_names_to_position_labels")
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Typed")
    channel = await seed.create_lens(ctx, dataset, CHANNEL_1)
    crop = await seed.create_lens(ctx, dataset, [*CHANNEL_1, {"axis": "y", "start": 0, "stop": 16}])
    other = await seed.create_lens(ctx, dataset, [{"axis": "c", "start": 0, "stop": 1}])
    await models.Lens.objects.filter(pk=crop.pk).aupdate(name="nucleus 3")
    await models.Lens.objects.filter(pk=channel.pk).aupdate(name="GFP")
    await models.Lens.objects.filter(pk=other.pk).aupdate(name="DAPI")

    await sync_to_async(migration.forwards)(apps, None)

    labels = {tuple(sorted(coordinates.items())): label async for coordinates, label in models.PositionLabel.objects.values_list("anchor__coordinates", "label")}
    assert labels == {(("c", 1),): "GFP", (("c", 0),): "DAPI"}

    await sync_to_async(call_command)("merge_duplicate_lenses")
    assert await _name_of(channel) == "GFP" and await _name_of(crop) == "GFP" and await _name_of(other) == "DAPI"

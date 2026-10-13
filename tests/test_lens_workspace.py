"""A lens is what people work on, so a lens answers for itself: its scenes, its picture, its staging.

The dataset is the container -- it has the name, the folder and the files. A *lens* is the
selection a processing step is handed, and until these existed it could not be opened: it
had no scenes of its own, borrowed its dataset's thumbnail, and could only be staged by
staging the whole dataset.

One rule runs through all of it, and most of these pin one side of it: **the whole lens is
its dataset, looked at whole.** It lives in the dataset's own grid for that reason, and for
the same reason it gets no second nomination -- a column on it would be a copy of the
dataset's answer, free to disagree. Only a lens that selects *something* has anything of its
own to say. One row per selection and the whole lens from creation are
`test_lens_identity`'s to pin; here it is what a lens answers for itself.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_scene_snapshot import _nominate, _snapshot

FROM_LENS = """
mutation FromLens($input: CreateSceneFromLensInput!) {
  createSceneFromLens(input: $input) {
    id
    name
    worldCoordinateSystem { id }
    layers { id placement ... on IntensityLayer { lens { id } } }
  }
}
"""

FROM_SYSTEM = """
mutation FromSystem($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) { id }
}
"""

SET_LENS_DEFAULT = """
mutation SetLensDefault($input: SetLensDefaultSceneInput!) {
  setLensDefaultScene(input: $input) { id defaultScene { id } }
}
"""

CREATE_LENS = """
mutation CreateLens($input: CreateLensInput!) {
  createLens(input: $input) { id slices { axis start stop } }
}
"""

LENS = """
query Lens($id: ID!) {
  lens(id: $id) {
    id
    defaultScene { id }
    ... on ArrayLens { scenes { id } latestSnapshot { name } }
  }
}
"""

DATASET = """
query Dataset($id: ID!) {
  arrayDataset(id: $id) {
    defaultScene { id }
    fullLens { id }
    all: lenses { id }
    sliced: lenses(sliced: true) { id }
    whole: lenses(sliced: false) { id }
  }
}
"""

CROP = [{"axis": "y", "start": 8, "stop": 40}, {"axis": "x", "start": 8, "stop": 40}]


async def _from_lens(ctx: HttpContext, lens: models.Lens, **extra) -> dict:
    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str(lens.pk), **extra}})
    assert not result.errors, result.errors
    return result.data["createSceneFromLens"]


async def _stage_dataset(ctx: HttpContext, dataset: models.ArrayDataset) -> str:
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    result = await schema.execute(FROM_SYSTEM, context_value=ctx, variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": {}}})
    assert not result.errors, result.errors
    return result.data["createSceneFromCoordinateSystem"]["id"]


async def _lens(ctx: HttpContext, lens: models.Lens) -> dict:
    result = await schema.execute(LENS, context_value=ctx, variable_values={"id": str(lens.pk)})
    assert not result.errors, result.errors
    return result.data["lens"]


async def _dataset(ctx: HttpContext, dataset: models.ArrayDataset) -> dict:
    result = await schema.execute(DATASET, context_value=ctx, variable_values={"id": str(dataset.pk)})
    assert not result.errors, result.errors
    return result.data["arrayDataset"]


def _layers_over(lens: models.Lens) -> list[models.Layer]:
    """The layers a scene from this lens drew: each over a single-channel lens cut from it, so they are found by the dataset."""
    return list(models.Layer.objects.filter(lens__arraylens__dataset_id=lens.dataset_id).order_by("order"))


def _channel_of(layer: models.Layer) -> int:
    lens = models.ArrayLens.objects.get(pk=layer.lens_id)
    return next(slice_.start for slice_ in lens.slices_list if slice_.axis == "c")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_from_a_lens_draws_that_lens(authenticated_context: HttpContext):
    """The crop, and nothing else of its dataset -- in the dataset's own grid.

    Staging the crop's *space* cannot do this: what lives behind a sliced lens' space is its
    dataset, so that draws the whole array again over a full lens. Which lens the layers read
    has to be said.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    crop = await seed.create_lens(ctx, dataset, CROP)

    scene = await _from_lens(ctx, crop)

    assert scene["name"] == "Tissue", "a lens has no name of its own; the scene takes its dataset's"
    assert len(scene["layers"]) == 3, "one layer per channel, exactly as a whole dataset is drawn"
    drawn = await sync_to_async(lambda: [models.ArrayLens.objects.get(pk=layer["lens"]["id"]) for layer in scene["layers"]])()
    assert {lens.dataset_id for lens in drawn} == {dataset.pk} and all(lens.pk != crop.pk for lens in drawn), "every layer reads a lens cut from the crop, not the crop itself: a layer draws one channel"
    assert {tuple((s.axis, s.start, s.stop) for s in lens.slices_list if s.axis != "c") for lens in drawn} == {tuple((s["axis"], s["start"], s["stop"]) for s in CROP)}, "each keeps exactly the crop's window"
    assert sorted(next(s.start for s in lens.slices_list if s.axis == "c") for lens in drawn) == [0, 1, 2], "and pins one channel each"
    assert {layer["placement"] for layer in scene["layers"]} == {"PLACED"}, "the lens' own edge into the grid places it; nothing was authored"

    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    assert scene["worldCoordinateSystem"]["id"] == str(intrinsic.pk), "the crop sits where it was cut from"
    assert await models.ArrayLens.objects.filter(dataset=dataset).acount() == 5, "the whole lens the dataset was born with, the crop, and the crop's three channels -- one lens per selection drawn"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_that_cuts_the_channel_axis_keeps_each_channels_facts(authenticated_context: HttpContext):
    """Over ``c[1:3]``, layer index 0 is the dataset's channel 1 -- and must be drawn as channel 1.

    A layer draws through a lens pinning one of the dataset's channels; labels and histograms are recorded
    against the dataset. Without the translation the first layer of this scene would carry
    DAPI's name, DAPI's hue and DAPI's contrast limits over GFP's pixels.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Threeplex")

    def record() -> None:
        for index, (label, window) in enumerate([("DAPI", (10.0, 100.0)), ("GFP", (20.0, 200.0)), ("mCherry", (30.0, 300.0))]):
            anchor = models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={"c": index})
            models.ChannelLabel.objects.create(anchor=anchor, label=label)
            models.ValueHistogram.objects.create(anchor=anchor, histogram=[], bins=[], min=0.0, max=65535.0, p1=window[0], p99=window[1])

    await sync_to_async(record)()
    lens = await seed.create_lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 3}])

    await _from_lens(ctx, lens)

    layers = await sync_to_async(_layers_over)(lens)
    assert await sync_to_async(lambda: [_channel_of(layer) for layer in layers])() == [1, 2], "two single-channel lenses, cut from the crop: the dataset's channels 1 and 2"
    assert [layer.name for layer in layers] == ["GFP", "mCherry"], "named for the channels the lens actually selects"
    assert [(layer.clim_min, layer.clim_max) for layer in layers] == [(20.0, 200.0), (30.0, 300.0)], "and opened at their recorded windows, not at DAPI's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_too_thin_to_draw_is_refused(authenticated_context: HttpContext):
    """Renderability is a fact of the selection: a dataset that draws can have a lens that does not."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    column = await seed.create_lens(ctx, dataset, [{"axis": "x", "start": 5, "stop": 6}])

    result = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": str(column.pk)}})

    assert result.errors and "not renderable" in str(result.errors[0])
    assert not await models.Scene.objects.aexists(), "the refusal leaves no empty scene behind"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sliced_lens_has_its_own_scenes_and_nomination(authenticated_context: HttpContext):
    """What a crop is shown in, and which of those to open, are the crop's own answers."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    whole = await _stage_dataset(ctx, dataset)
    crop = await seed.create_lens(ctx, dataset, CROP)

    before = await _lens(ctx, crop)
    assert before["scenes"] == [], "a scene of the whole dataset does not show the crop"
    assert before["defaultScene"] is None, f"nothing nominated yet, though scene {whole} shows the dataset"

    staged = await _from_lens(ctx, crop)

    after = await _lens(ctx, crop)
    assert [scene["id"] for scene in after["scenes"]] == [staged["id"]]
    assert after["defaultScene"]["id"] == staged["id"], "the first scene staged from a lens becomes the one to open for it"
    assert (await _dataset(ctx, dataset))["defaultScene"] is None, "and the dataset's own nomination is not the crop's to write"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_borrows_its_datasets_picture_until_it_has_one(authenticated_context: HttpContext):
    """A crop nobody has staged still gets a tile -- its dataset's -- and its own once it nominates."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    overview = await seed.create_scene(ctx, "Overview")
    await _nominate(ctx, dataset, overview)
    await _snapshot(ctx, overview, "overview.png", name="overview")
    crop = await seed.create_lens(ctx, dataset, CROP)
    # Both pictures exist before the first read: the latest-snapshot map is built once per
    # request context, and these tests reuse one context across requests.
    close_up = await seed.create_scene(ctx, "Close-up")
    await _snapshot(ctx, close_up, "closeup.png", name="close-up")

    borrowed = await _lens(ctx, crop)
    assert borrowed["latestSnapshot"]["name"] == "overview"
    assert borrowed["defaultScene"]["id"] == str(overview.pk)

    result = await schema.execute(SET_LENS_DEFAULT, context_value=ctx, variable_values={"input": {"lens": str(crop.pk), "scene": str(close_up.pk)}})
    assert not result.errors, result.errors

    own = await _lens(ctx, crop)
    assert own["latestSnapshot"]["name"] == "close-up"
    assert (await _dataset(ctx, dataset))["defaultScene"]["id"] == str(overview.pk), "the dataset still opens its overview"

    cleared = await schema.execute(SET_LENS_DEFAULT, context_value=ctx, variable_values={"input": {"lens": str(crop.pk), "scene": None}})
    assert not cleared.errors, cleared.errors
    assert (await _lens(ctx, crop))["latestSnapshot"]["name"] == "overview", "clearing falls back rather than going blank"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unsliced_lens_answers_as_its_dataset(authenticated_context: HttpContext):
    """Nominating through a whole-array lens is nominating the dataset: one column, whichever door."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")
    first = await seed.create_lens(ctx, dataset)
    second = await seed.create_lens(ctx, dataset)
    scene = await seed.create_scene(ctx, "Composition")

    result = await schema.execute(SET_LENS_DEFAULT, context_value=ctx, variable_values={"input": {"lens": str(first.pk), "scene": str(scene.pk)}})
    assert not result.errors, result.errors

    assert (await _dataset(ctx, dataset))["defaultScene"]["id"] == str(scene.pk)
    assert (await models.Lens.objects.aget(pk=first.pk)).default_scene_id == scene.pk, "the whole lens is where the dataset's nomination lives"
    assert (await _lens(ctx, second))["defaultScene"]["id"] == str(scene.pk), "so its interchangeable sibling answers alike"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_lists_its_lenses_whole_and_sliced_apart(authenticated_context: HttpContext):
    """`fullLens` and the `sliced` switch tell the handle for the whole array from the crops people work on."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")

    async def create(slices: list) -> str:
        result = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": slices}})
        assert not result.errors, result.errors
        return result.data["createLens"]["id"]

    whole = (await _dataset(ctx, dataset))["fullLens"]["id"]
    assert await create([]) == whole, "the whole lens the dataset was born with"
    crop = await create(CROP)

    listed = await _dataset(ctx, dataset)
    assert listed["fullLens"]["id"] == whole
    assert [lens["id"] for lens in listed["whole"]] == [whole]
    assert crop in [lens["id"] for lens in listed["sliced"]] and whole not in [lens["id"] for lens in listed["sliced"]]
    assert len(listed["all"]) == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_datasets_owner_nominates_for_its_lens(authenticated_context: HttpContext, bot_context: HttpContext):
    """A lens has no creator of its own, and it is the dataset's cards a nomination changes."""
    dataset = await seed.create_array_dataset(authenticated_context, "Tissue")
    crop = await seed.create_lens(authenticated_context, dataset, CROP)
    scene = await seed.create_scene(authenticated_context, "Composition")

    refused = await schema.execute(SET_LENS_DEFAULT, context_value=bot_context, variable_values={"input": {"lens": str(crop.pk), "scene": str(scene.pk)}})
    assert refused.errors

    # Staging is not an edit of the data, so it goes through -- only the nomination is skipped.
    staged = await schema.execute(FROM_LENS, context_value=bot_context, variable_values={"input": {"lens": str(crop.pk)}})
    assert not staged.errors, staged.errors
    assert (await models.Lens.objects.aget(pk=crop.pk)).default_scene_id is None


# ---------------------------------------------------------------------------
# The whole array is a lens from the start, and a lens can be called something.
#
# The viewer opens a lens, never a dataset, so "all of it" has to be a row that
# exists the moment the dataset does -- and a cut is easier to find again by
# what someone called it than by its slices.
# ---------------------------------------------------------------------------

UPDATE_LENS = """
mutation UpdateLens($input: UpdateLensInput!) {
  updateLens(input: $input) { id name }
}
"""

NAMED = """
query Named($id: ID!, $filters: LensFilter) {
  arrayDataset(id: $id) { fullLens { id name slices { axis } } }
  lenses(filters: $filters) { id name }
}
"""

DATASETS = """
query Datasets { arrayDatasets { id fullLens { id } } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_is_created_with_its_whole_array_lens(authenticated_context: HttpContext):
    from tests.test_array_axis_declaration import _ZYX, _create

    result = await _create(authenticated_context, "born-whole", ["z", "y", "x"], _ZYX)
    assert not result.errors, result.errors
    dataset_id = result.data["createArrayDataset"]["id"]

    read = await schema.execute(NAMED, context_value=authenticated_context, variable_values={"id": dataset_id, "filters": {"dataset": dataset_id}})
    assert not read.errors, read.errors
    whole = read.data["arrayDataset"]["fullLens"]
    assert whole is not None, "the lens a viewer opens for it exists before any scene does"
    assert whole["slices"] == [] and whole["name"] is None
    assert [lens["id"] for lens in read.data["lenses"]] == [whole["id"]], "one, and staging it later will reuse it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_page_of_datasets_names_each_ones_whole_array_lens(authenticated_context: HttpContext):
    """`fullLens` is on every tile, so it answers for a whole page -- and for each row its own."""
    ctx = authenticated_context
    first = await seed.create_array_dataset(ctx, "First")
    second = await seed.create_array_dataset(ctx, "Second")
    bare = await seed.create_array_dataset(ctx, "Bare")
    first_whole = await seed.create_lens(ctx, first)
    await seed.create_lens(ctx, first)
    await seed.create_lens(ctx, first, CROP)
    second_whole = await seed.create_lens(ctx, second)

    result = await schema.execute(DATASETS, context_value=ctx)
    assert not result.errors, result.errors
    by_dataset = {row["id"]: row["fullLens"] for row in result.data["arrayDatasets"]}

    assert by_dataset[str(first.pk)]["id"] == str(first_whole.pk), "the whole lens, never the crop"
    assert by_dataset[str(second.pk)]["id"] == str(second_whole.pk)
    assert by_dataset[str(bare.pk)] is not None, "a dataset nobody touched was still born with one"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_can_be_named_and_renamed(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue")

    async def create(slices: list, name: str | None) -> dict:
        payload = {"dataset": str(dataset.pk), "slices": slices}
        if name is not None:
            payload["name"] = name
        result = await schema.execute(
            "mutation C($input: CreateLensInput!) { createLens(input: $input) { id name } }",
            context_value=ctx,
            variable_values={"input": payload},
        )
        assert not result.errors, result.errors
        return result.data["createLens"]

    crop = await create(CROP, "  nucleus 3 ")
    assert crop["name"] == "nucleus 3"

    again = await create(CROP, "something else")
    assert again == crop, "the same selection is the same lens, and finding it is not a request to rename it"

    renamed = await schema.execute(UPDATE_LENS, context_value=ctx, variable_values={"input": {"id": crop["id"], "name": "nucleus 4"}})
    assert not renamed.errors, renamed.errors
    assert renamed.data["updateLens"]["name"] == "nucleus 4"

    found = await schema.execute(NAMED, context_value=ctx, variable_values={"id": str(dataset.pk), "filters": {"search": "nucleus"}})
    assert [lens["id"] for lens in found.data["lenses"]] == [crop["id"]]
    by_dataset = await schema.execute(NAMED, context_value=ctx, variable_values={"id": str(dataset.pk), "filters": {"search": "tiss"}})
    whole = by_dataset.data["arrayDataset"]["fullLens"]["id"]
    assert sorted(lens["id"] for lens in by_dataset.data["lenses"]) == sorted([whole, crop["id"]]), "a lens is also found by the dataset it borrows its name from -- the whole lens too"

    staged = await schema.execute(FROM_LENS, context_value=ctx, variable_values={"input": {"lens": crop["id"]}})
    assert staged.data["createSceneFromLens"]["name"] == "Tissue · nucleus 4", "two crops staged side by side are not two scenes called the same"

    cleared = await schema.execute(UPDATE_LENS, context_value=ctx, variable_values={"input": {"id": crop["id"], "name": " "}})
    assert cleared.data["updateLens"]["name"] is None, "blank clears: the lens reads as its slices again"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_datasets_owner_renames_its_lens(authenticated_context: HttpContext, bot_context: HttpContext):
    dataset = await seed.create_array_dataset(authenticated_context, "Tissue")
    crop = await seed.create_lens(authenticated_context, dataset, CROP)

    refused = await schema.execute(UPDATE_LENS, context_value=bot_context, variable_values={"input": {"id": str(crop.pk), "name": "mine now"}})

    assert refused.errors
    assert (await models.Lens.objects.aget(pk=crop.pk)).name is None



"""Scenes rooted directly at an owned coordinate system (RFC-6, resolved open question).

With walk-time choice gone, an owned root is correctness-safe: a scene can compose
straight over a dataset's INTRINSIC pixel grid or a PHYSICAL calibration, and nothing is
authored to make that work. The container's data is placed *by construction* (its fact tree
reaches its own space), only that container's tree can ever compose there (registrations land
exclusively on SHARED spaces), and the lifecycle is RESTRICT: the container is undeletable
while a scene is rooted in its space, exactly as a shared space is.

This is now the *only* way a dataset is staged. `createSceneFromDataset` used to mint a world
whose axes copied the dataset's physical space and author an identity edge into it -- a copy
of a space the dataset was already in. It is gone; the tests it carried live at the bottom of
this file.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.models import RestrictedError
from django.db.models.deletion import ProtectedError
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) {
    id name
    worldCoordinateSystem { id  residents { __typename } }
  }
}
"""

FROM_SYSTEM = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) {
    id
    worldCoordinateSystem { id residents { __typename } registrations { id } }
    layers { id }
  }
}
"""

MAKE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

LAYER_PATHS = """
query LayerPaths($id: ID!) {
  scene(id: $id) {
    layers {
      placement
      placementValidity
      pathToWorld {
        inverted
        transformation { input { id  } output { id  } }
      }
    }
  }
}
"""


async def _adopt(ctx: HttpContext, system: "models.CoordinateSystem", name: str) -> dict:
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": name, "coordinateSystem": str(system.pk)}})
    assert not result.errors, result.errors
    return result.data["createScene"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_over_a_datasets_intrinsic_grid_places_by_construction(authenticated_context: HttpContext):
    """Adopt the pixel grid itself: the dataset's layer is placed with no edge anywhere.

    An unsliced lens' space IS the world, so its path is empty and exact; a sliced
    lens is one fact hop away. Zero transformations exist in the graph throughout.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Bare")  # (c, y, x)
    full = await seed.create_lens(authenticated_context, dataset, slices=[])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    scene = await _adopt(authenticated_context, intrinsic, "PixelSpace")
    assert scene["worldCoordinateSystem"]["id"] == str(intrinsic.pk)
    assert [r["__typename"] for r in scene["worldCoordinateSystem"]["residents"]] == ["ArrayDataset", "DataArray", "Lens"], "the scene roots in the space the dataset, its level 0 and its unsliced lens all live in"

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene["id"], "lens": str(full.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    sliced = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene["id"], "lens": str(sliced.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    result = await schema.execute(LAYER_PATHS, context_value=authenticated_context, variable_values={"id": scene["id"]})
    assert not result.errors, result.errors
    full_layer, sliced_layer = result.data["scene"]["layers"]

    assert full_layer["placement"] == "PLACED"
    assert full_layer["pathToWorld"] == [], "the unsliced lens' space IS the world: nothing to compose"
    assert full_layer["placementValidity"] == "VALIDATED", "an empty path is exact by construction"

    assert sliced_layer["placement"] == "PLACED"
    sliced_system = await sync_to_async(lambda: str(sliced.coordinate_system.pk))()
    hops = [(step["transformation"]["input"]["id"], step["transformation"]["output"]["id"]) for step in sliced_layer["pathToWorld"]]
    assert hops == [(sliced_system, str(intrinsic.pk))], "one hop: the lens' crop into the grid it slices"

    assert await sync_to_async(models.Transformation.objects.filter(output=intrinsic, input__lenses__isnull=True).count)() == 0, "no registration was authored anywhere"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bootstrap_from_an_intrinsic_system_materializes_the_container(authenticated_context: HttpContext):
    """createSceneFromCoordinateSystem over an owned root: the container becomes the layer, nothing is authored.

    This is the exact call that used to be refused with 'owned by a container, not an
    ownerless shared space'.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Owner")
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    before = await sync_to_async(models.Transformation.objects.count)()

    result = await schema.execute(FROM_SYSTEM, context_value=authenticated_context, variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": {}}})
    assert not result.errors, result.errors
    scene = result.data["createSceneFromCoordinateSystem"]

    assert scene["worldCoordinateSystem"]["id"] == str(intrinsic.pk)
    assert len(scene["layers"]) == 3, "the container's own data is the one candidate, and its three channels are three layers"
    sourced = await sync_to_async(lambda: {layer.lens.dataset_id for layer in models.Layer.objects.filter(scene__pk=scene["id"]).select_related("lens")})()
    assert sourced == {dataset.pk}, "one source, however many layers it is drawn as"
    assert scene["worldCoordinateSystem"]["registrations"] == [], "nothing was registered into the grid: the data is in it by definition"
    assert await sync_to_async(models.Transformation.objects.count)() == before, "and the bootstrap authored no edge"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_over_a_calibration_renders_at_physical_scale(authenticated_context: HttpContext):
    """Adopt the PHYSICAL space: the path is the calibration edge, forward, and nothing else.

    Nothing is minted and nothing is authored: pixel -> physical is the
    dataset's own fact, so the layer renders at physical scale with zero authored
    registrations.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Staged")
    physical = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[
            seed.physical_axis("c", enums.AxisType.CHANNEL, unit="a.u."),
            seed.physical_axis("y", enums.AxisType.SPACE, unit="micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, unit="micrometer"),
        ],
        scale=[1.0, 0.325, 0.325],
    )
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    scene = await _adopt(authenticated_context, physical, "PhysicalSpace")
    assert scene["worldCoordinateSystem"]["residents"] == [], "a calibrated space holds nothing; the scene roots in a frame"

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene["id"], "lens": str(lens.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    result = await schema.execute(LAYER_PATHS, context_value=authenticated_context, variable_values={"id": scene["id"]})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]

    assert layer["placement"] == "PLACED"
    intrinsic_id = await sync_to_async(lambda: str(dataset.coordinate_system.pk))()
    hops = [(step["transformation"]["input"]["id"], step["transformation"]["output"]["id"]) for step in layer["pathToWorld"]]
    assert hops == [(intrinsic_id, str(physical.pk))], "the calibration edge is the whole path"
    assert all(step["inverted"] is False for step in layer["pathToWorld"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_containers_tree_composes_in_an_owned_space(authenticated_context: HttpContext):
    """A derived child places through its fact chain; an unrelated dataset has no way in.

    Registrations land exclusively on SHARED spaces, so an owned root composes exactly
    the container's fact tree -- foreign data is a category error there, and the
    `placeableIn` set agrees with the per-layer refusal.
    """
    parent = await seed.create_array_dataset(authenticated_context, "Parent")
    child = await seed.create_array_dataset(authenticated_context, "Crop")
    stranger = await seed.create_array_dataset(authenticated_context, "Stranger")

    def derive():
        # The child's primary (and only) derivation: a fact edge child -> parent.
        parent_intrinsic = parent.intrinsic_coordinate_system
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.IDENTITY.value,
            input=child.intrinsic_coordinate_system,
            output=parent_intrinsic,
            organization=authenticated_context.request.organization,
        )
        return parent_intrinsic

    parent_intrinsic = await sync_to_async(derive)()
    scene_data = await _adopt(authenticated_context, parent_intrinsic, "ParentSpace")

    child_lens = await seed.create_lens(authenticated_context, child, slices=[])
    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_data["id"], "lens": str(child_lens.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    stranger_lens = await seed.create_lens(authenticated_context, stranger, slices=[])
    refused = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_data["id"], "lens": str(stranger_lens.pk), "intensityAxis": "c"}})
    assert refused.errors, "nothing relates the stranger to this space, so its layer is refused"

    def placeable() -> set[int]:
        return graph_logic.placeable_lens_dataset_ids(models.Scene.objects.get(pk=scene_data["id"]).world)

    dataset_ids = await sync_to_async(placeable)()
    assert parent.pk in dataset_ids, "the container itself seeds the placeable set"
    assert child.pk in dataset_ids, "its derivation child arrives through the fact chain"
    assert stranger.pk not in dataset_ids


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_container_is_undeletable_while_a_scene_is_rooted_in_its_space(authenticated_context: HttpContext):
    """The space a scene roots in is undeletable; the data that lives there is not.

    RFC-9 splits a guarantee that used to be one thing. `Scene.world` is still RESTRICT, so a
    space a scene composes over cannot be deleted -- and now `ArrayDataset.coordinate_system` is
    PROTECT, so it cannot be deleted while the dataset lives there either. What is *gone* is
    the transitivity: deleting the dataset no longer cascades into the space, so it no longer
    trips the scene's RESTRICT.

    That is the honest outcome rather than a regression papered over. A scene is rooted in a
    *space*, not in a dataset; the space survives its residents, and a scene left composing
    over an emptied space is exactly what "the space outlives the data" means.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Pinned")
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()

    await _adopt(authenticated_context, intrinsic, "Holder")

    # The space itself is pinned from both sides.
    with pytest.raises((RestrictedError, ProtectedError)):
        await sync_to_async(intrinsic.delete)()

    # The dataset is not: it moves out, and the space stays for the scene that roots in it.
    await sync_to_async(dataset.delete)()
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=intrinsic.pk).exists)(), "the space outlives the data that lived in it"


# --- What a scene over a dataset's own space renders -------------------------------
#
# These moved here when `createSceneFromDataset` was deleted. They used to run against a
# minted world whose axes copied the dataset's; they now run against the space the dataset
# is already in, which is the whole point of the deletion -- the recipes never depended on
# the copy, only on the data's axes.

FROM_SYSTEM_GRAPH = """
mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) {
    id
    layers { id placement placementValidity pathToWorld { transformation { id } } }
  }
}
"""

DATASET_SCENES = """
query DatasetScenes($id: ID!) {
  arrayDataset(id: $id) { id scenes { id name } }
}
"""


def _layer_of(dataset: models.ArrayDataset) -> models.Layer:
    return models.Layer.objects.get(lens__dataset=dataset)


def _layers_of(dataset: models.ArrayDataset) -> list[models.Layer]:
    """Every layer the bootstrap made for this dataset, in the order it stacked them.

    A multi-channel image materializes one layer per channel over a single shared lens, so
    the plural is the normal case; `_layer_of` stays for the recipes that keep their
    channels together (RGB) or have none (LABEL).
    """
    return list(models.Layer.objects.filter(lens__dataset=dataset).order_by("order"))


async def _stage_in_own_grid(ctx: HttpContext, dataset: models.ArrayDataset, **policy) -> dict:
    """A scene over the dataset's own pixel grid -- the replacement for the deleted bootstrap."""
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    result = await schema.execute(
        FROM_SYSTEM_GRAPH,
        context_value=ctx,
        variable_values={"input": {"coordinateSystem": str(intrinsic.pk), "policy": policy}},
    )
    assert not result.errors, result.errors
    return result.data["createSceneFromCoordinateSystem"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_data_in_its_own_space_is_placed_exactly(authenticated_context: HttpContext):
    """The inversion the mirror world used to hide: an empty path is VALIDATED, not UNKNOWN.

    Staging an uncalibrated dataset used to mint a world under assumed micrometre units and
    author an identity edge into it, which honestly wore UNKNOWN -- the units were a guess.
    Over the dataset's own grid there is no guess and no edge: the data is in that space by
    definition, the path is empty, and an empty path is exact by construction.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Uncalibrated")
    before = await sync_to_async(models.Transformation.objects.count)()

    scene = await _stage_in_own_grid(authenticated_context, dataset)

    # Every channel layer, not just the first: they share one lens and one space, so the
    # per-channel split must leave placement exactly where it was.
    assert len(scene["layers"]) == 3
    for layer in scene["layers"]:
        assert layer["placement"] == "PLACED"
        assert layer["pathToWorld"] == [], "the lens' space IS the world: there is nothing to compose"
        assert layer["placementValidity"] == "VALIDATED", "nothing was assumed, so nothing wears UNKNOWN"
    assert await sync_to_async(models.Transformation.objects.count)() == before, "and no edge was invented to say so"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_three_flat_channels_are_three_layers_not_a_photograph(authenticated_context: HttpContext):
    """The guess that had to go: three flat channels are three markers, not red/green/blue.

    Nothing about a 2D three-channel array says whether it is a photograph or a three-marker
    fluorescence acquisition, and on this server it is nearly always the latter. Inferring RGB
    from the shape did not merely pick colours -- it fused three independent signals into one
    layer, where none of them could be hidden or dimmed on its own. So the inference is gone
    and the fluorescence default applies: a layer per channel.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Slide", shapes=[[3, 64, 64]])

    await _stage_in_own_grid(authenticated_context, dataset)

    layers = await sync_to_async(_layers_of)(dataset)
    assert len(layers) == 3
    assert {layer.kind for layer in layers} == {enums.LayerKind.INTENSITY.value}, "three signals, three intensity layers -- not one RGB layer"
    assert [layer.intensity_index for layer in layers] == [0, 1, 2]
    assert [layer.colormap for layer in layers] == ["green", "magenta", "cyan"], "distinguishable hues, not red/green/blue"
    assert {layer.blending for layer in layers} == {enums.Blending.ADDITIVE.value}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_rgb_photograph_is_stated_and_stays_one_layer(authenticated_context: HttpContext):
    """The recipe survives its inference: a caller who actually has a photograph says so.

    Red, green and blue are the colour components of one image, so this is the one recipe
    whose channels stay together -- splitting them would hand a viewer three toggles that
    only mean something all on. `policy.kind` is where that is now stated, which is the
    trade: the guess is gone, the capability is not.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Photo", shapes=[[3, 64, 64]])

    await _stage_in_own_grid(authenticated_context, dataset, kind="RGB")

    layer = await sync_to_async(_layer_of)(dataset)
    # A kind of its own, so the stated fact survives the write. As a three-child blend it was
    # indistinguishable from the three-marker acquisition the test above describes, which is
    # the commoner reading of that shape and the reason RGB is never inferred.
    assert layer.kind == enums.LayerKind.RGB.value
    assert layer.render_graph is None
    assert [layer.red_index, layer.green_index, layer.blue_index] == [0, 1, 2]
    assert layer.blending == enums.Blending.NORMAL.value, "a photograph composites over the scene, it does not sum into it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_z_stack_infers_a_volume_and_each_channel_is_its_own_layer(authenticated_context: HttpContext):
    """Depth wins over channel count: a 3-channel confocal stack is a volume, not a photograph.

    And it is three volumes, one per channel: each channel gets its own layer, its own hue and
    its own place in the stack, so a viewer can hide one signal without touching the others.
    """
    dataset = await seed.create_array_dataset(
        authenticated_context,
        "Stack",
        axes=[
            seed.axis("c", enums.AxisType.CHANNEL),
            seed.axis("z", enums.AxisType.SPACE),
            seed.axis("y", enums.AxisType.SPACE),
            seed.axis("x", enums.AxisType.SPACE),
        ],
        shapes=[[3, 16, 64, 64]],
    )

    await _stage_in_own_grid(authenticated_context, dataset)

    # One layer per channel: three fluorescence signals sharing a grid are three things a
    # viewer turns on and off separately, not one thing with three parts.
    layers = await sync_to_async(_layers_of)(dataset)
    assert len(layers) == 3
    assert [layer.order for layer in layers] == [0, 1, 2], "a deterministic stack, not three layers at index 0"

    # A volume is an intensity layer with a projection, not a kind of its own: a projection
    # collapses z, it does not composite anything.
    assert {layer.kind for layer in layers} == {enums.LayerKind.INTENSITY.value}
    assert {layer.projection_mode for layer in layers} == {enums.ProjectionMode.MIP.value}

    # Each layer draws exactly one channel, and no two draw the same one -- not red/green/blue:
    # these are fluorescence channels, in distinguishable hues.
    assert [layer.intensity_index for layer in layers] == [0, 1, 2]
    assert [layer.colormap for layer in layers] == ["green", "magenta", "cyan"]
    assert [layer.name for layer in layers] == ["channel 0", "channel 1", "channel 2"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_policy_names_the_recipe_inference_cannot_reach(authenticated_context: HttpContext):
    """LABEL is chosen, never inferred from structure -- and `policy.kind` is where it is chosen.

    Nothing about an array distinguishes a label map from an image, so inference reaches
    LABEL only through a derivation declared CATEGORIZED. An imported mask has no such
    derivation, and this is its one-call path.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Mask", axes=seed.YX_AXES, shapes=[[64, 64]])

    await _stage_in_own_grid(authenticated_context, dataset, kind="LABEL")

    layer = await sync_to_async(_layer_of)(dataset)
    assert layer.kind == enums.LayerKindChoices.LABEL.value
    assert layer.render_graph is None and layer.label_render["background"] == 0
    assert layer.blending == enums.Blending.NORMAL.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_finds_its_scenes_by_walking_the_graph(authenticated_context: HttpContext):
    """`ArrayDataset.scenes` is a layers->lens->dataset walk, never a column.

    There is deliberately no `Scene.dataset` FK: which scenes show a dataset is already
    answerable from the graph, and a stored copy would be free to disagree with it.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Shown")
    scene = await _stage_in_own_grid(authenticated_context, dataset)

    result = await schema.execute(DATASET_SCENES, context_value=authenticated_context, variable_values={"id": str(dataset.pk)})
    assert not result.errors, result.errors
    assert [s["id"] for s in result.data["arrayDataset"]["scenes"]] == [scene["id"]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_derivation_still_composes_over_its_own_space(authenticated_context: HttpContext):
    """An UNMAPPABLE derivation denies correspondence with its *source*, not with its own space.

    A phasor-like reduction has no point correspondence with the image it came from, so no
    walk may cross that edge. It is still perfectly placeable in the space its own pixels
    live in -- by construction, with nothing authored either way.
    """
    source = await seed.create_array_dataset(authenticated_context, "Source", shapes=[[2, 64, 64]])
    source_lens = await seed.create_lens(authenticated_context, source)
    derived = await seed.create_array_dataset(authenticated_context, "Phasorish", axes=seed.YX_AXES, shapes=[[64, 64]])

    def derivation() -> None:
        # `space`, not `coordinate_system`: an unsliced lens owns no system, its space
        # is the source dataset's intrinsic system.
        graph_logic.write_relation_edge(
            name="Phasorish <- Source",
            input_system=derived.intrinsic_coordinate_system,
            output_system=source_lens.space,
            kind=enums.TransformKindChoices.UNMAPPABLE.value,
            reason="the geometry does not survive the reduction",
            ctx=seed._creation(authenticated_context),
        )

    await sync_to_async(derivation)()

    scene = await _stage_in_own_grid(authenticated_context, derived)

    (layer,) = scene["layers"]
    assert layer["pathToWorld"] == [], "its own space places it; the refused edge is not on the way"
    intrinsic = await sync_to_async(lambda: derived.intrinsic_coordinate_system)()
    assert await models.Transformation.objects.filter(output=intrinsic).acount() == 0, "nothing was registered into the grid"
    assert await models.Transformation.objects.filter(input=intrinsic, kind=enums.TransformKindChoices.UNMAPPABLE.value).aexists(), "and the derivation edge is untouched"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_staging_a_physical_space_reaches_the_data_through_the_calibration(authenticated_context: HttpContext):
    """The other half of the documented staging path: point the builder at a *physical* space.

    Over the intrinsic grid the builder takes its `residents_exist` branch -- the data is
    right there. A calibrated space holds nothing, so it takes the *registrations* branch and
    has to reach the dataset by following the calibration edge back (`dataset_behind`). Both
    are named in `createSceneFromCoordinateSystem`'s description as the way to stage a
    dataset, so both need to work; only the first was covered.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Staged")
    physical = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[
            seed.physical_axis("c", enums.AxisType.CHANNEL, unit="a.u."),
            seed.physical_axis("y", enums.AxisType.SPACE, unit="micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, unit="micrometer"),
        ],
        scale=[1.0, 0.325, 0.325],
    )
    before = await sync_to_async(models.Transformation.objects.count)()

    result = await schema.execute(FROM_SYSTEM_GRAPH, context_value=authenticated_context, variable_values={"input": {"coordinateSystem": str(physical.pk), "policy": {}}})
    assert not result.errors, result.errors
    scene = result.data["createSceneFromCoordinateSystem"]

    assert len(scene["layers"]) == 3, "one source reached through the calibration, drawn as one layer per channel"
    for layer in scene["layers"]:
        assert layer["placement"] == "PLACED"
        assert len(layer["pathToWorld"]) == 1, "the calibration edge is the whole path -- the data renders at physical scale"
    assert await sync_to_async(models.Transformation.objects.count)() == before, "and nothing was authored to make that true"


def _label_channels(dataset: models.ArrayDataset, axis: str, labels: dict[int, str]) -> None:
    """Record what ingest would have recorded: one ChannelLabel spoke per named channel."""
    for index, label in labels.items():
        anchor = models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={axis: index})
        models.ChannelLabel.objects.create(anchor=anchor, label=label)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_channel_becomes_its_own_layer(authenticated_context: HttpContext):
    """The unit of control is the layer, so the unit of a channel is a layer.

    Four fluorescence channels sharing one grid are four independent signals. Packed into one
    layer's render graph they could only be hidden, dimmed or reordered together; peeled into
    four layers over the same lens, each is an ordinary layer a viewer -- or an ordinary
    `updateLayer` -- can touch on its own.

    "Their own, and not the same" is the whole assertion: distinct channel indices, distinct
    hues, distinct positions in the stack, and distinct labels, so no two layers of the scene
    are interchangeable.
    """
    dataset = await seed.create_array_dataset(
        authenticated_context,
        "Fourplex",
        axes=[
            seed.axis("c", enums.AxisType.CHANNEL),
            seed.axis("y", enums.AxisType.SPACE),
            seed.axis("x", enums.AxisType.SPACE),
        ],
        shapes=[[4, 64, 64]],
    )
    await sync_to_async(_label_channels)(dataset, "c", {0: "DAPI", 1: "GFP"})

    scene = await _stage_in_own_grid(authenticated_context, dataset)
    assert len(scene["layers"]) == 4, "one layer per channel"

    layers = await sync_to_async(_layers_of)(dataset)
    assert [layer.order for layer in layers] == [0, 1, 2, 3], "a deterministic stack, not four layers at index 0"
    assert len({layer.lens_id for layer in layers}) == 1, "one lens: every channel selects the same array, so a lens each would be four rows saying one thing"

    assert {layer.kind for layer in layers} == {enums.LayerKind.INTENSITY.value}
    assert [layer.intensity_axis for layer in layers] == ["c"] * 4
    assert [layer.intensity_index for layer in layers] == [0, 1, 2, 3], "each layer draws its own channel"

    # The named channels are drawn as what they are -- DAPI blue, GFP green -- and only the
    # unnamed two fall through to the index cycle. The hue used to come from the index for
    # every channel, so a DAPI channel came back green as often as blue while the string
    # saying otherwise sat on the same row.
    assert [layer.colormap for layer in layers] == ["blue", "green", "cyan", "yellow"]

    # The labels ingest recorded, on the field that exists to hold them. They used to be
    # written into the render graph root node's `label`, because a layer had no name column
    # and that was the only string on the row; a flat layer has no graph root, so the
    # workaround has nowhere left to stand and `Layer.name` is the honest place.
    assert [layer.name for layer in layers] == ["DAPI", "GFP", "channel 2", "channel 3"]

    # Additively, which is what the single layer's in-layer blend did: the picture is the same
    # one, only now it is made of four things instead of one.
    assert {layer.blending for layer in layers} == {enums.Blending.ADDITIVE.value}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_single_channel_dataset_is_still_one_layer(authenticated_context: HttpContext):
    """Splitting per channel is not "one layer per anything": one channel is one layer, in grey.

    The degenerate case an `enumerate(range(channels))` loop gets wrong twice -- a dataset with
    no channel axis at all has zero channels and must still draw.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Brightfield", axes=seed.YX_AXES, shapes=[[64, 64]])

    scene = await _stage_in_own_grid(authenticated_context, dataset)

    assert len(scene["layers"]) == 1
    (layer,) = await sync_to_async(_layers_of)(dataset)
    assert layer.kind == enums.LayerKind.INTENSITY.value
    assert layer.colormap == "grey"
    assert layer.name is None, "there is nothing to tell it apart from"


# ---------------------------------------------------------------------------
# What the bootstrap reads before it decides. Shape decides nothing new here:
# every test below turns on something a converter *recorded* -- a channel's
# name, a histogram, the media type of the file the arrays were read out of --
# which is the standard the deleted three-channels-is-a-photograph guess failed.
# ---------------------------------------------------------------------------


def _record_histograms(dataset: models.ArrayDataset, axis: str, windows: dict[int, tuple[float, float]]) -> None:
    """Record what ingest would have recorded: a value histogram per channel."""
    for index, (low, high) in windows.items():
        anchor = models.CoordinateAnchor.objects.create(dataset=dataset, coordinates={axis: index})
        models.ValueHistogram.objects.create(anchor=anchor, histogram=[], bins=[], min=0.0, max=65535.0, p1=low, p99=high)


def _link_source_file(dataset: models.ArrayDataset, ctx: HttpContext, *, name: str, content_type: str | None, direction=enums.FileLinkDirectionChoices.SOURCE) -> models.File:
    """The file a converter read to write these arrays -- or, for RENDITION, the one it wrote out of them."""
    file = models.File.objects.create(
        name=name,
        content_type=content_type,
        organization=ctx.request.organization,
        membership=ctx.request.membership,
    )
    models.FileLink.objects.create(file=file, dataset=dataset, direction=direction.value, organization=ctx.request.organization, creator=ctx.request.user)
    return file


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_channels_named_red_green_blue_are_inferred_as_one_photograph(authenticated_context: HttpContext):
    """The evidence that shape could never be: a converter that named the components.

    Three channels *called* red, green and blue are the three components of one picture --
    nobody labels a fluorescence panel that way. So the recipe that had to be stated by hand
    is inferred here, without reintroducing the guess: the same array with no labels is still
    three intensity layers (see the test above).
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Snapshot", shapes=[[3, 64, 64]])
    await sync_to_async(_label_channels)(dataset, "c", {0: "Red", 1: "Green", 2: "Blue"})

    scene = await _stage_in_own_grid(authenticated_context, dataset)
    assert len(scene["layers"]) == 1, "the components of one picture, not three signals"

    layer = await sync_to_async(_layer_of)(dataset)
    assert layer.kind == enums.LayerKind.RGB.value
    assert [layer.red_index, layer.green_index, layer.blue_index] == [0, 1, 2]
    assert layer.blending == enums.Blending.NORMAL.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_components_follow_the_labels_not_the_positions(authenticated_context: HttpContext):
    """A converter that wrote (blue, green, red) gets its picture back in the right colours.

    Hardcoding 0, 1, 2 renders such a file with the channels swapped -- a picture that looks
    plausible and is wrong, which is the worst kind. The labels say which is which and are the
    only reason this dataset was read as a photograph at all, so they decide the mapping too.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "BGR", shapes=[[3, 64, 64]])
    await sync_to_async(_label_channels)(dataset, "c", {0: "Blue", 1: "Green", 2: "Red"})

    await _stage_in_own_grid(authenticated_context, dataset)

    layer = await sync_to_async(_layer_of)(dataset)
    assert layer.kind == enums.LayerKind.RGB.value
    assert [layer.red_index, layer.green_index, layer.blue_index] == [2, 1, 0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_arrays_read_out_of_a_png_are_inferred_as_a_photograph(authenticated_context: HttpContext):
    """A source link to a picture format is the converter saying it read a picture.

    Recorded at ingest, by the thing that actually knows -- which is exactly what the shape
    of a three-channel array is not.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Scan", shapes=[[3, 64, 64]])
    await sync_to_async(_link_source_file)(dataset, authenticated_context, name="field.png", content_type="image/png")

    scene = await _stage_in_own_grid(authenticated_context, dataset)
    assert len(scene["layers"]) == 1

    layer = await sync_to_async(_layer_of)(dataset)
    assert layer.kind == enums.LayerKind.RGB.value
    assert [layer.red_index, layer.green_index, layer.blue_index] == [0, 1, 2], "unlabelled components fall back to position"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_png_exported_from_an_acquisition_proves_nothing(authenticated_context: HttpContext):
    """Direction is the whole meaning of the link, and a preview must not rewrite its source.

    A PNG *written from* a three-marker acquisition says someone exported a picture of it.
    Read as evidence, every dataset anyone ever snapshotted would turn into a photograph and
    lose its per-channel layers.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Acquired", shapes=[[3, 64, 64]])
    await sync_to_async(_link_source_file)(dataset, authenticated_context, name="preview.png", content_type="image/png", direction=enums.FileLinkDirectionChoices.RENDITION)

    scene = await _stage_in_own_grid(authenticated_context, dataset)
    assert len(scene["layers"]) == 3, "three signals, exported once"

    layers = await sync_to_async(_layers_of)(dataset)
    assert {layer.kind for layer in layers} == {enums.LayerKind.INTENSITY.value}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_czi_is_not_a_photograph(authenticated_context: HttpContext):
    """The negative that keeps the media-type list honest: an acquisition format decides nothing."""
    dataset = await seed.create_array_dataset(authenticated_context, "Confocal", shapes=[[3, 64, 64]])
    await sync_to_async(_link_source_file)(dataset, authenticated_context, name="stack.czi", content_type="application/octet-stream")

    scene = await _stage_in_own_grid(authenticated_context, dataset)
    assert len(scene["layers"]) == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_brightfield_channel_sits_under_the_fluorescence_and_does_not_sum_into_it(authenticated_context: HttpContext):
    """Transmitted light is not a marker, and additively blending it washes the scene out.

    Brightfield is the sample lit from behind -- an opaque picture of the field. Summed with
    the fluorescence (which is what ADDITIVE means, and what light does when two markers glow
    at once) it drags every pixel towards white, and the first thing anyone does in the viewer
    is drop it to the bottom and blend it NORMAL. The name ingest recorded is enough to do that
    here.
    """
    dataset = await seed.create_array_dataset(
        authenticated_context,
        "Widefield",
        axes=[seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        shapes=[[3, 64, 64]],
    )
    await sync_to_async(_label_channels)(dataset, "c", {0: "GFP", 1: "Brightfield", 2: "mCherry"})

    await _stage_in_own_grid(authenticated_context, dataset)

    layers = await sync_to_async(_layers_of)(dataset)
    # Back to front: the picture of the field first, the two markers over it. The channel
    # indices are untouched -- only the stack order is.
    assert [layer.name for layer in layers] == ["Brightfield", "GFP", "mCherry"]
    assert [layer.intensity_index for layer in layers] == [1, 0, 2], "the stack was reordered, not the data"
    assert [layer.blending for layer in layers] == [enums.Blending.NORMAL.value, enums.Blending.ADDITIVE.value, enums.Blending.ADDITIVE.value]
    assert [layer.colormap for layer in layers] == ["grey", "green", "red"], "a hue per fluorophore, and grey for the one that is not one"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_contrast_limits_come_from_the_histogram_ingest_recorded(authenticated_context: HttpContext):
    """The numbers were already on the server; every viewer was working them out again.

    A ValueHistogram is recorded per channel precisely so a client can open the contrast
    limits without reading the array, and the bootstrap left both limits null. p1/p99 rather
    than min/max: one hot pixel sets the maximum, and a window opened on it renders the whole
    channel black.
    """
    dataset = await seed.create_array_dataset(
        authenticated_context,
        "Measured",
        axes=[seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        shapes=[[2, 64, 64]],
    )
    await sync_to_async(_record_histograms)(dataset, "c", {0: (12.0, 900.0), 1: (5.0, 4000.0)})

    await _stage_in_own_grid(authenticated_context, dataset)

    layers = await sync_to_async(_layers_of)(dataset)
    assert [(layer.clim_min, layer.clim_max) for layer in layers] == [(12.0, 900.0), (5.0, 4000.0)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_channel_with_no_histogram_keeps_its_null_limits(authenticated_context: HttpContext):
    """Null is "nobody measured", and it is the answer a viewer's own auto-contrast needs.

    Filling it with a number derived from nothing would be a limit that looks recorded.
    """
    dataset = await seed.create_array_dataset(
        authenticated_context,
        "Unmeasured",
        axes=[seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        shapes=[[2, 64, 64]],
    )
    await sync_to_async(_record_histograms)(dataset, "c", {1: (5.0, 4000.0)})

    await _stage_in_own_grid(authenticated_context, dataset)

    layers = await sync_to_async(_layers_of)(dataset)
    assert [(layer.clim_min, layer.clim_max) for layer in layers] == [(None, None), (5.0, 4000.0)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_rgb_layer_gets_one_window_wide_enough_for_all_three(authenticated_context: HttpContext):
    """The components of one picture share one pair of limits, so the three windows become one.

    The widest of them: clipping a component shows up as a colour cast over the whole image,
    which is worse than a slightly flat one.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Wide", shapes=[[3, 64, 64]])
    await sync_to_async(_label_channels)(dataset, "c", {0: "Red", 1: "Green", 2: "Blue"})
    await sync_to_async(_record_histograms)(dataset, "c", {0: (10.0, 200.0), 1: (4.0, 180.0), 2: (7.0, 250.0)})

    await _stage_in_own_grid(authenticated_context, dataset)

    layer = await sync_to_async(_layer_of)(dataset)
    assert (layer.clim_min, layer.clim_max) == (4.0, 250.0)

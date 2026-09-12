"""Creating scenes and layers over spaces that already exist.

:func:`bootstrap_scene_from_system` is the way data gets staged: point it at a coordinate
system and it materializes the layers for what lives in or is registered into that space.
It composes only facts that already exist elsewhere (the space, the axis types, the anchors'
channel labels and value histograms, the files a dataset was converted from) into ordinary
rows -- an ordinary scene, an ordinary lens, ordinary layers -- and **authors no edges**.
Nothing here fabricates a placement, and nothing here infers a render recipe from a shape:
what it does not read out of a recorded fact, it defaults.

There used to be a second entry point that took a *dataset*: it minted a world whose axes
copied the dataset's physical space and authored an identity edge into it. That world was a
copy of a space the dataset was already in, and the edge existed only to justify the copy.
Both are gone. A dataset already has coordinate systems -- its pixel grid, and any physical
space it is registered into -- and staging it means picking one of those.

There is deliberately no schema tying a scene to a dataset -- no ``Scene.dataset`` column, no
"default scene" flag -- because "which scenes show this dataset" is already answerable through
the graph, and a second stored copy of that fact would be free to disagree with it.

**This module makes compositions, not spaces.** Minting a world and creating a lens' own
coordinate system are facts about spaces, answerable with no scene in sight, and they live in
:mod:`core.logic.coordinate_system`. What is left here takes a scene or makes one.
"""

import datetime
import dataclasses
from collections.abc import Callable

from django.db import transaction

from core import enums, models
from core.creation import CreationContext
from core.inputs.coords import ScenePolicyInputModel
from core.logic import channels as channels_logic
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import file_link as file_link_logic
from core.logic import graph as graph_logic
from core.render.layer import label as label_models

#: Distinguishable single-hue colormaps for "one source per channel", cycled. Green and
#: magenta first: they are the standard two-channel pairing that survives red-green
#: color blindness.
_CHANNEL_COLORMAPS = [
    enums.ColorMap.GREEN,
    enums.ColorMap.MAGENTA,
    enums.ColorMap.CYAN,
    enums.ColorMap.YELLOW,
    enums.ColorMap.RED,
    enums.ColorMap.BLUE,
    enums.ColorMap.ORANGE,
    enums.ColorMap.WHITE,
]

#: How a materialized layer composites over the layers below it, per recipe. The same
#: choices the dedicated layer mutations make: fluorescence sums, an RGB photograph and
#: a label overlay sit *over* whatever is beneath them.
_LAYER_BLENDING = {
    enums.BootstrapLayerKind.RGB: enums.Blending.NORMAL,
    enums.BootstrapLayerKind.INTENSITY: enums.Blending.ADDITIVE,
    enums.BootstrapLayerKind.VOLUME: enums.Blending.ADDITIVE,
    enums.BootstrapLayerKind.LABEL: enums.Blending.NORMAL,
    enums.BootstrapLayerKind.VECTOR: enums.Blending.NORMAL,
}


def _viewer_preferences(preferred_view: "enums.PreferredView | None", background_color: list[float] | None) -> dict:
    """The viewer preferences to stamp on a new scene, omitting the ones nobody stated.

    Omitted rather than defaulted here: the columns already carry their defaults, and
    passing an explicit None for `preferred_view` would write null over AUTO.
    """
    preferences: dict = {}
    if preferred_view is not None:
        preferences["preferred_view"] = preferred_view.value
    if background_color is not None:
        preferences["background_color"] = background_color
    return preferences


def create_scene(
    *,
    name: str,
    ctx: CreationContext,
    axes: list | None = None,
    blending: "enums.Blending | None" = None,
    preferred_view: "enums.PreferredView | None" = None,
    background_color: list[float] | None = None,
    epoch: datetime.datetime | None = None,
    world: "models.CoordinateSystem | None" = None,
) -> "models.Scene":
    """Create a scene over a world: an adopted existing system, or one created for convenience.

    Adopting composes over the space as it is -- many scenes can share it, and its axes and
    epoch are already its own. **Every space is adoptable** (RFC-9): under residence a
    pyramid level's grid is a space like any other, related to everything else by edges, so
    the old refusal of an ARRAY system had nothing left to stand on. Composing in one is
    unusual rather than wrong.

    Without ``world``, an ordinary ownerless SHARED space is created first and
    adopted -- pure convenience, nothing scene-owned: the space can be adopted by
    other scenes, outlives every scene over it, and is deleted only explicitly
    (``deleteCoordinateSystem``), never by a scene going away.
    """
    if world is not None:
        if axes is not None or epoch is not None:
            raise ValueError("A scene adopting an existing coordinate system takes its axes and epoch from it; do not pass `axes` or `epoch` alongside `coordinateSystem`.")
        if not world.axes.filter(type__in=coordinate_system_logic.NAVIGABLE_TYPES).exists():
            raise ValueError(f"World '{world.name}' has no navigable (time/space) axes, so nothing could be placed in a scene over it.")
        return models.Scene.objects.create(
            name=name,
            world=world,
            organization=ctx.organization,
            blending=blending or enums.Blending.ADDITIVE,
            **_viewer_preferences(preferred_view, background_color),
        )

    # Minting the space is not this module's work -- it makes scenes and layers -- so the
    # convenience path calls the space module and then adopts what it gets back, which is
    # exactly the shape of the two-call version a client can write itself.
    with transaction.atomic():
        world = coordinate_system_logic.create_world_space(name=f"{name}/world", axes=axes, epoch=epoch, ctx=ctx)
        scene = models.Scene.objects.create(
            name=name,
            world=world,
            organization=ctx.organization,
            blending=blending or enums.Blending.ADDITIVE,
            **_viewer_preferences(preferred_view, background_color),
        )
    return scene


def _is_renderable(dataset: "models.ArrayDataset") -> bool:
    """Whether a dataset has an x and a y axis with more than one pixel -- the minimum to draw.

    The same condition :func:`_bootstrap_image_layers` raises on, factored out so the scene
    builder can *skip* a non-renderable source (like a table with too few coordinate columns)
    instead of aborting the whole batch over one bad one. The condition itself now lives in
    :func:`core.logic.coords.is_renderable`, shared with the `placeableIn` filter's `asLayer`
    gate so a picker cannot offer what this would skip -- and it swallows the too-few-spatial-axes
    ValueError this used to let through, which is what "skip instead of abort" meant all along.
    """
    return coords_logic.is_renderable(dataset.axis_specs, dataset.axis_names, dataset.shape_list)


def _bootstrap_image_layers(
    dataset: "models.ArrayDataset",
    scene: "models.Scene",
    ctx: CreationContext,
    *,
    kind: "enums.BootstrapLayerKind | None" = None,
    order: int = 0,
) -> "list[models.Layer]":
    """Create the default array layers for a dataset in a scene: a lens, and one layer per channel.

    The array half of :func:`bootstrap_scene_from_system`. It writes no placement edge -- the
    caller must already have made the dataset placeable in the scene -- so it is pure layer
    materialization over the graph, and rejects a dataset too small to render.

    **One channel, one layer.** A multi-channel acquisition is several independent signals that
    happen to share a grid, and a viewer's unit of control -- visibility, opacity, the blend into
    the scene, deletion -- is the layer. Packing every channel into one layer's render graph made
    those per-channel choices unreachable, so the channels are peeled apart here: each gets its
    own layer over the *same* lens (``Layer.lens`` is many-to-one for exactly this), carrying a
    one-child render graph for its index, its own hue, and its own ``order`` so the stack is
    deterministic. They composite additively, which is what the single layer's in-layer blend
    did, so the picture is unchanged -- only now each channel can be touched on its own.

    RGB is the one exception, and stays one layer: there the three "channels" are the colour
    components of one photograph rather than three signals, and splitting them would offer a
    viewer three toggles that only mean something together. It is never inferred from *shape*
    though -- a flat three-channel image is fluorescence far more often than it is a
    photograph, and guessing wrong fused three signals into one layer. What it is inferred
    from is what ingest recorded: see :func:`_infer_rgb`. A caller whose photograph arrived
    with nothing recorded still says so with ``policy.kind = RGB``.

    **The recorded facts are read, not just carried.** A channel's label decides its hue and
    whether it is transmitted light (:mod:`core.logic.channels`), and its value histogram
    decides its contrast limits (:func:`_value_windows`). All of it was already on the server
    and none of it was consulted: the hue came from an index cycle and the limits were left
    null for every viewer to work out again, differently.

    ``kind`` overrides the recipe :func:`_infer_kind` would pick, and arrives from
    ``ScenePolicyInput.kind``. Worth having for LABEL alone: nothing structural distinguishes
    a label map from an image, so a mask whose derivation was never declared CATEGORIZED is
    unreachable by inference. LABEL makes a single LABEL layer carrying a label recipe -- its
    values are ids, so it has no channels to peel apart and none of the graph's vocabulary
    applies to them.

    Either way each layer must come out indistinguishable from one the matching mutation would
    have authored -- ``createLabelLayer`` here, ``createLayer`` there -- so that every later
    edit is an ordinary update.
    """
    render = coords_logic.resolve_render_axes(dataset.axis_specs)
    axis_names, shape = dataset.axis_names, dataset.shape_list

    def size(axis: str | None) -> int:
        return shape[axis_names.index(axis)] if axis is not None and axis in axis_names else 0

    if size(render.x) <= 1 or size(render.y) <= 1:
        raise ValueError(f"Dataset {dataset.pk} is not renderable: its x axis '{render.x}' ({size(render.x)} px) and y axis '{render.y}' ({size(render.y)} px) must both have more than one pixel")

    # Read once, used three times: to pick the recipe, to name and colour the channel layers,
    # and to say which channel is red. Each of those used to ask the database itself, or not
    # ask at all.
    labels = _channel_labels(dataset, render.intensity) if render.intensity is not None else {}

    resolved_kind = kind or _infer_kind(dataset, render, size, labels)
    # One lens for the whole dataset, whatever it becomes: a lens is a selection over an array,
    # and every channel layer selects the same thing. Minting one per channel would write a row
    # per layer saying exactly what its siblings say -- and a *sliced* one would mint a
    # coordinate system and an edge, which this module does not do.
    lens = coordinate_system_logic.create_lens(dataset, [], ctx)
    blending = _LAYER_BLENDING[resolved_kind]

    if resolved_kind == enums.BootstrapLayerKind.LABEL:
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.LABEL,
                lens=lens,
                scene=scene,
                blending=blending,
                order=order,
                label_render=label_models.LabelRenderModel(intensity_axis=render.intensity).model_dump(mode="json"),
            )
        ]

    if resolved_kind == enums.BootstrapLayerKind.VECTOR:
        # `_assert_rgb_capacity`'s reason, one branch over: an explicit `policy.kind = VECTOR`
        # must not materialize a layer `createVectorLayer` would have refused.
        if not _infer_vector(render, size):
            raise ValueError(
                f"Dataset {dataset.pk} cannot be drawn as a vector layer: it needs a DISPLACEMENT value axis of 2 or 3 positions, no wider than its spatial axes "
                f"(axes: {[f'{spec.name} ({spec.type})' for spec in dataset.axis_specs]})"
            )
        # One layer, no channels to peel: the value axis's positions are components of one
        # offset, not signals to composite. The defaults are `createVectorLayer`'s own, so
        # every later edit is an ordinary updateVectorLayer.
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.VECTOR,
                lens=lens,
                scene=scene,
                blending=blending,
                order=order,
                glyph=enums.VectorGlyph.ARROW,
                colormap=enums.ColorMap.VIRIDIS,
            )
        ]

    if resolved_kind == enums.BootstrapLayerKind.RGB:
        _assert_rgb_capacity(render, size)
        # Positional 0, 1, 2 is the fallback, not the rule: a converter that labelled its
        # components tells us which is which, and several of them write (blue, green, red).
        components = channels_logic.rgb_components(labels, size(render.intensity)) or {"red": 0, "green": 1, "blue": 2}
        windows = _value_windows(dataset, render.intensity)
        clim_min, clim_max = _combined_window(windows, sorted(components.values()))
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.RGB,
                lens=lens,
                scene=scene,
                blending=blending,
                order=order,
                intensity_axis=render.intensity,
                red_index=components["red"],
                green_index=components["green"],
                blue_index=components["blue"],
                clim_min=clim_min,
                clim_max=clim_max,
            )
        ]

    projection_mode = _projection_mode(render, resolved_kind)
    return [
        models.Layer.objects.create(
            kind=enums.LayerKind.INTENSITY,
            lens=lens,
            scene=scene,
            name=channel.name,
            blending=channel.blending or blending,
            order=order + offset,
            intensity_axis=channel.intensity_axis,
            intensity_index=channel.intensity_index,
            colormap=channel.colormap,
            clim_min=channel.clim_min,
            clim_max=channel.clim_max,
            gamma=1.0,
            projection_mode=projection_mode,
        )
        for offset, channel in enumerate(_channel_sources(dataset, render, size, labels))
    ]


def bootstrap_scene_from_system(
    system: "models.CoordinateSystem",
    policy: "ScenePolicyInputModel",
    ctx: CreationContext,
    *,
    name: str | None = None,
) -> "models.Scene":
    """Materialize a renderable scene over an existing coordinate system and what lives in it.

    The scene *adopts* the system as its world -- no fresh world is created and no edge
    is authored, because a node for the same space joined by an identity edge would store
    nothing. What becomes a layer depends on what the space is. Over a **shared space**,
    each source already registered one hop into it becomes one or more layers, in
    registration order, up to ``policy.nchildren`` **sources** -- the registration alone
    places it, so each source's path to world is exactly the one edge
    ``createCoordinateSystem`` authored. The cap counts sources and not layers because a
    multi-channel dataset materializes one layer per channel: truncating mid-dataset would
    hand back half an acquisition, which is worse than overshooting the cap. Over an **owned** system (a dataset's intrinsic
    pixels, a physical space, a collection's space), the container's own data becomes the
    layer: it fact-reaches its own space by construction, no registration exists or is
    needed, and nothing foreign can be claimed into an owned space. Rerunning shares the
    space -- two scenes, one space -- and the space outlives every scene over it
    (Scene.world is RESTRICT).
    """
    # There is no adoptability rule left to assert -- RFC-9 made every space adoptable, and
    # the ARRAY refusal went with it. `create_scene`'s adopt path checks one thing: that the
    # space has navigable axes, without which nothing could be placed in a scene over it.
    with transaction.atomic():
        scene = create_scene(name=name or system.name, world=system, ctx=ctx)

        if graph_logic.residents_exist(system):
            # Data lives right here, so it is the layer: it is in its own space by
            # definition, and there is no registration to iterate for it.
            _materialize_layers(system, scene, ctx, policy, order=0)
            return scene

        # The candidate set: the sources registered one hop into the shared space, in the
        # order the registrations were authored (pk). Bounded and predictable against
        # nchildren -- a multi-hop reachable closure would be a larger, less obvious set.
        edges = (
            models.Transformation.objects.filter(output=system, parent__isnull=True)
            .select_related("input").prefetch_related("input__datasets", "input__table_datasets", "input__mesh_collections", "input__annotation_collections")
            .order_by("pk")
        )

        made = 0
        # One counter across the whole build, never restarting per source: `order` is the
        # scene's back-to-front stack, so two datasets' channels numbered from zero apiece
        # would collide and leave the compositing order to the database.
        order = 0
        for edge in edges:
            if made >= policy.nchildren:
                break
            if edge.input is None:
                continue
            # The registration into the shared space already places: materializing a layer
            # composes over it, and skipping a source leaves the claim untouched -- it is a
            # fact about the space, not about this scene.
            layers = _materialize_layers(edge.input, scene, ctx, policy, order=order)
            if not layers:
                continue
            order += len(layers)
            made += 1

    return scene


def _gate_placement(source: "models.CoordinateSystem", scene: "models.Scene", policy: "ScenePolicyInputModel") -> bool:
    """Whether this source may become a layer here -- refusing, or skipping, when it may not.

    One place for the choice `policy.skip_unplaceable` names, because every branch of
    `_materialize_layers` faces it and three copies of an if would be three chances to answer
    it differently. Off (the default), this is exactly `assert_placeable_in`: the builder holds
    the same line `createLayer` does, and a source with no affine route to the world refuses the
    whole build with a message naming the edge. On, it returns False and the caller skips --
    the treatment an array too small to render (`_is_renderable`) and a table with too few
    coordinate columns already get, and for the same reason: a scene missing one layer is worth
    more than no scene at all when the caller has said so.
    """
    if policy.skip_unplaceable:
        return graph_logic.is_placeable_in(scene.world, source, require_affine=True)
    graph_logic.assert_placeable_in(scene.world, source, destination=f"the world of scene '{scene.name}'")
    return True


def _materialize_layers(
    source: "models.CoordinateSystem",
    scene: "models.Scene",
    ctx: CreationContext,
    policy: "ScenePolicyInputModel",
    *,
    order: int = 0,
) -> "list[models.Layer]":
    """Turn one registered source into the layers its kind implies, or an empty list to skip it.

    A dataset's system (its intrinsic pixels or a physical space) becomes an image
    layer **per channel**, drawn by ``policy.kind`` or by inference; a table dataset a point
    or track layer (behind ``policy.transform_tables``); a mesh collection a mesh layer
    (behind ``policy.include_meshes``); a network collection a network layer (behind
    ``policy.include_networks``). A bare, ownerless system is skipped -- there is no
    data to draw. Placeability is asserted first, the same gate the layer mutations apply, so
    this can never compose a layer the graph does not already place.

    A list rather than one layer because only the array branch can produce several, and a
    caller that had to know which branch it hit to know how many layers came back would be
    the same conditional written twice. ``order`` is where this source's layers start in the
    scene's stack; the caller advances it by however many come back.
    """
    table = next(iter(source.table_datasets.all()[:1]), None)
    if table is not None:
        if not policy.transform_tables:
            return []
        layer = _materialize_table_layer(table, scene, policy, order=order)
        return [layer] if layer is not None else []

    mesh = next(iter(source.mesh_collections.all()[:1]), None)
    if mesh is not None:
        if not policy.include_meshes:
            return []
        if not _gate_placement(source, scene, policy):
            return []
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.MESH,
                scene=scene,
                mesh_collection=mesh,
                material_color=[255, 255, 255, 255],
                wireframe=False,
                blending=enums.Blending.NORMAL,
                opacity=1.0,
                visible=True,
                order=order,
            )
        ]

    network = next(iter(source.network_collections.all()[:1]), None)
    if network is not None:
        if not policy.include_networks:
            return []
        if not _gate_placement(source, scene, policy):
            return []
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.NETWORK,
                scene=scene,
                network_collection=network,
                material_color=[255, 255, 255, 255],
                directed=False,
                show_nodes=False,
                blending=enums.Blending.NORMAL,
                opacity=1.0,
                visible=True,
                order=order,
            )
        ]

    # The collections are asked *first*, and the array case last, because `dataset_behind`
    # deliberately follows an edge back: for a collection's space that edge leads to the
    # image the meshes were extracted from, and answering with it would draw the image
    # wherever a mesh was registered -- straight past `include_meshes`. Every collection kind
    # has to be named in this guard, not just the ones that had branches when it was written:
    # a collection missing from it falls through to the array case and draws the volume it was
    # derived from, which is the one outcome the branches above exist to prevent.
    dataset = graph_logic.dataset_behind(source)
    if (
        dataset is not None
        and not source.mesh_collections.exists()
        and not source.network_collections.exists()
        and not source.table_datasets.exists()
        and not source.annotation_collections.exists()
        # `sparse_datasets` has no branch above -- there is no sparse layer kind, so a sparse
        # dataset is simply not drawable -- but it still has to be named here. It owns its space
        # (`is_collection`), so without this it falls through and bootstraps the volume it was
        # derived from, which is the same wrong picture the branches above prevent. Not
        # drawable and drawn as something else are different answers.
        and not source.sparse_datasets.exists()
    ):
        if not _is_renderable(dataset):
            # Skip, don't raise: a dataset too small to render is not layerable, exactly like
            # a table with too few coordinate columns. Letting _bootstrap_image_layers raise
            # here would abort the whole atomic build over one bad source.
            return []
        if not _gate_placement(source, scene, policy):
            return []
        # `policy.kind` reaches the render graph only here. It is deliberately not asked of
        # the mesh/table/annotation branches above: those have no recipe to choose.
        return _bootstrap_image_layers(dataset, scene, ctx, kind=policy.kind, order=order)

    annotations = next(iter(source.annotation_collections.all()[:1]), None)
    if annotations is not None:
        if not _gate_placement(source, scene, policy):
            return []
        return [
            models.Layer.objects.create(
                kind=enums.LayerKind.ANNOTATION,
                scene=scene,
                annotation_collection=annotations,
                blending=enums.Blending.NORMAL,
                opacity=1.0,
                visible=True,
                order=order,
            )
        ]

    return []


def _materialize_table_layer(table_dataset: "models.TableDataset", scene: "models.Scene", policy: "ScenePolicyInputModel", *, order: int = 0) -> "models.Layer | None":
    """A registered table dataset as a track layer when it declares tracks, else a point layer.

    Only a table with at least two SPACE coordinate columns has a place in a scene; one
    without (a per-object measurement) is skipped rather than forced into an undefined space
    -- the same minimum the point/track layer mutations require.
    """
    system = table_dataset.coordinate_system_or_none
    spatial = [col for col in table_dataset.columns_by_role(enums.ColumnRoleChoices.COORDINATE.value) if col.axis_type == enums.AxisTypeChoices.SPACE.value]
    if system is None or len(spatial) < 2:
        return None

    if not _gate_placement(system, scene, policy):
        return None

    is_track = bool(table_dataset.columns_by_role(enums.ColumnRoleChoices.TRACK_ID.value))
    return models.Layer.objects.create(
        kind=enums.LayerKind.TRACK if is_track else enums.LayerKind.POINT,
        scene=scene,
        table_dataset=table_dataset,
        point_size=None if is_track else 3.0,
        line_width=1.0 if is_track else None,
        colormap=enums.ColorMap.VIRIDIS,
        blending=enums.Blending.NORMAL,
        opacity=1.0,
        visible=True,
        order=order,
    )


def _infer_rgb(dataset: "models.ArrayDataset", render: coords_logic.RenderAxes, size: Callable[[str | None], int], labels: dict[int, str]) -> bool:
    """Whether something *recorded* says this dataset is a colour photograph.

    Two signals, and what they have in common is the whole point: each of them is a
    sentence a converter wrote down, not a shape this function measured.

    * The **components are named**. Three channels labelled red, green and blue are the
      three components of one picture; nobody labels a fluorescence panel that way, and a
      converter that writes those three names has said what the array is.
    * The **source file is a picture**. A SOURCE link to a PNG or a JPEG says a converter
      read a photograph to write these arrays (:func:`core.logic.file_link.is_photographic_source`).

    Shape alone still infers nothing, which is the rule that has not moved: a flat
    three-channel array with no labels and no source file is a three-marker acquisition
    until something says otherwise, and it gets a layer per channel.
    """
    if render.intensity is None or size(render.intensity) < 3:
        return False
    if channels_logic.rgb_components(labels, size(render.intensity)) is not None:
        return True
    return file_link_logic.is_photographic_source(dataset)


def _infer_vector(render: coords_logic.RenderAxes, size: Callable[[str | None], int]) -> bool:
    """Whether this dataset is a vector field a glyph can draw.

    A DISPLACEMENT value axis is authored evidence in LABEL's CATEGORIZED sense -- someone
    stated the values are the components of a per-point offset -- so unlike RGB this *is*
    inferred, and it is checked before RGB because a field whose components happen to be
    labelled cannot be drawn as a picture either way.

    The extra conditions mirror ``assert_vector_axis``: a bootstrapped layer must come out
    indistinguishable from one ``createVectorLayer`` would have authored, so a field that
    mutation would refuse -- one component, four, or more components than spatial axes --
    falls through to the ordinary recipes rather than materializing a layer no mutation
    can update into validity.
    """
    if render.vector is None:
        return False
    components = size(render.vector)
    spatial = 2 if render.z is None else 3
    return 2 <= components <= 3 and components <= spatial


def _infer_kind(dataset: "models.ArrayDataset", render: coords_logic.RenderAxes, size: Callable[[str | None], int], labels: dict[int, str]) -> "enums.BootstrapLayerKind":
    """The default recipe: stated facts first, then structure.

    A CATEGORIZED primary derivation says the values became labels -- the one
    structural signal that distinguishes a label map from an image, stated where the
    derivation is stated. Then recorded evidence that the data is a photograph
    (:func:`_infer_rgb`). Absent both: z with depth wins (a confocal stack is a volume),
    and everything else is intensity -- one layer per channel. LABEL is still never
    inferred from array structure alone, and an explicit ``kind`` always overrides.

    **RGB is inferred from evidence, never from shape.** It used to be inferred from shape,
    for flat data with exactly three channels, and that guess was wrong far more often than
    it was right: on a microscopy server a three-channel 2D image is a three-marker
    fluorescence acquisition, not a photograph. The cost was not a colormap -- it fused three
    independent signals into one layer, where none of them could be hidden, dimmed or
    reordered on its own, which is exactly what a layer per channel exists to allow. So the
    shape-based guess stays deleted, and what replaced it reads what ingest recorded: channels
    *named* red, green and blue, or arrays read out of a PNG. Both are the converter's
    statement rather than this function's inference, which is why they are allowed to decide
    something a shape is not. A caller who has a photograph nothing recorded still says so
    with ``policy.kind = RGB``, and an explicit kind still overrides all of this.

    RGB is checked before VOLUME deliberately: a z-stack of photographs is still photographs,
    and an RGB layer's three components are not something a projection knows how to collapse.
    """
    primary = graph_logic.primary_derivation_edge(dataset)
    if primary is not None and primary.value_relation == enums.ValueRelationChoices.CATEGORIZED.value:
        return enums.BootstrapLayerKind.LABEL
    if _infer_vector(render, size):
        return enums.BootstrapLayerKind.VECTOR
    if _infer_rgb(dataset, render, size, labels):
        return enums.BootstrapLayerKind.RGB
    if render.z is not None and size(render.z) > 1:
        return enums.BootstrapLayerKind.VOLUME
    return enums.BootstrapLayerKind.INTENSITY


def _channel_labels(dataset: "models.ArrayDataset", axis: str) -> dict[int, str]:
    """The per-channel labels ingest recorded, keyed by channel index."""
    labels: dict[int, str] = {}
    spokes = models.ChannelLabel.objects.filter(anchor__dataset=dataset, anchor__coordinates__has_key=axis).select_related("anchor").order_by("pk")
    for spoke in spokes:
        index = spoke.anchor.coordinates.get(axis)
        if isinstance(index, int) and index not in labels and spoke.label:
            labels[index] = spoke.label
    return labels


def _value_windows(dataset: "models.ArrayDataset", axis: str | None) -> dict[int | None, tuple[float | None, float | None]]:
    """The recorded intensity window per channel: what to open the contrast limits at.

    Ingest writes a :class:`~core.models.ValueHistogram` per anchor, and the server has been
    handing it to clients for exactly this purpose -- seed the contrast limits without reading
    the array -- while the bootstrap left ``climMin``/``climMax`` null and made every viewer
    work it out again, differently. The numbers are already there; this reads them.

    ``p1``/``p99`` rather than ``min``/``max``, falling back when a converter recorded only the
    extremes: a single hot pixel sets the maximum, and a window opened on it renders the whole
    channel black. The percentiles are the same choice every viewer's auto-contrast makes.

    Keyed by channel index, with ``None`` for a histogram taken over the whole array (an anchor
    that pins nothing). First anchor wins per index, matching :func:`_channel_labels` -- the two
    read the same spokes and must not disagree about which one they mean.
    """
    windows: dict[int | None, tuple[float | None, float | None]] = {}
    spokes = models.ValueHistogram.objects.filter(anchor__dataset=dataset).select_related("anchor").order_by("pk")
    for spoke in spokes:
        coordinates = spoke.anchor.coordinates or {}
        index = coordinates.get(axis) if axis is not None else None
        if index is None and coordinates:
            # Pinned somewhere else entirely -- a z plane, a timepoint. It describes a slice of
            # a channel rather than the channel, and there is no honest key for it here.
            continue
        if not isinstance(index, int) and index is not None:
            continue
        if index in windows:
            continue
        low = spoke.p1 if spoke.p1 is not None else spoke.min
        high = spoke.p99 if spoke.p99 is not None else spoke.max
        if low is None and high is None:
            continue
        windows[index] = (low, high)
    return windows


def _combined_window(windows: dict[int | None, tuple[float | None, float | None]], indices: list[int]) -> tuple[float | None, float | None]:
    """One window wide enough for several channels: what an RGB layer needs.

    ``Layer.clim_min``/``clim_max`` are shared across an RGB layer's three components -- they
    are components of one picture, not three signals -- so the three recorded windows have to
    become one. The widest of them, because clipping a component is visible as a colour cast
    across the whole image, which is worse than a slightly flat one.
    """
    lows = [windows[index][0] for index in indices if index in windows and windows[index][0] is not None]
    highs = [windows[index][1] for index in indices if index in windows and windows[index][1] is not None]
    if not lows and not highs:
        fallback = windows.get(None)
        return fallback if fallback is not None else (None, None)
    return (min(lows) if lows else None, max(highs) if highs else None)


@dataclasses.dataclass(frozen=True)
class _BootstrapChannel:
    """One channel of a dataset, and how a bootstrapped layer should draw it."""

    intensity_axis: str | None
    intensity_index: int
    colormap: "enums.ColorMap"
    #: What the layer is called. From the dataset's ChannelLabel spokes when ingest recorded
    #: them, so a materialized layer says "DAPI" where the acquisition did.
    name: str | None
    #: The recorded intensity window, or None where ingest recorded no histogram.
    clim_min: float | None = None
    clim_max: float | None = None
    #: How this one channel composites, when its name says it does not sum with the others.
    #: None means "whatever the recipe blends with" -- ADDITIVE for fluorescence.
    blending: "enums.Blending | None" = None
    #: Whether this is transmitted light: it sorts to the back of the stack, under the
    #: fluorescence that is drawn over it.
    transmitted: bool = False


def _channel_sources(dataset: "models.ArrayDataset", render: coords_logic.RenderAxes, size: Callable[[str | None], int], labels: dict[int, str]) -> "list[_BootstrapChannel]":
    """One channel per layer, drawn the way its recorded name says -- and the index cycle where it says nothing.

    Each of these becomes an INTENSITY layer of its own (:func:`_bootstrap_image_layers`), so
    the list is the scene's channel stack rather than one layer's children.

    The names come from the dataset's ChannelLabel spokes when ingest recorded them. Where it
    did not, the index is spelled out rather than left null: a stack of unnamed siblings is
    exactly the confusion splitting them was meant to end. A lone channel keeps its null --
    there is nothing to tell it apart from.

    **The name is read, not just displayed.** It used to be a string that reached ``Layer.name``
    and nothing else, so a DAPI channel came back green as often as blue and every viewer's
    first act was to fix the hue by hand. :func:`core.logic.channels.colormap_for` turns the
    recorded name into the hue that channel is conventionally drawn in, and only an unrecognized
    name falls through to the index cycle -- which is what *every* channel used to get. A
    transmitted-light channel is told apart too, and gets more than a colour: NORMAL blending
    and the back of the stack, because brightfield summed into fluorescence washes a scene out.

    The contrast limits come from the same place -- the ValueHistogram spoke for that channel
    (:func:`_value_windows`), which ingest recorded so that exactly this would not need the array.

    Order is the stack, back to front: transmitted light first, so the fluorescence composites
    over the picture of the field rather than under it. Within each group the channel index
    orders, so the stack stays deterministic and an unlabelled dataset is laid out exactly as
    it was before.
    """
    axis = render.intensity
    channels = size(axis) if axis is not None else 0
    windows = _value_windows(dataset, axis)

    def window(index: int | None) -> tuple[float | None, float | None]:
        return windows.get(index) or windows.get(None) or (None, None)

    if channels <= 1:
        label = labels.get(0) if channels == 1 else None
        clim_min, clim_max = window(0 if channels == 1 else None)
        return [
            _BootstrapChannel(
                intensity_axis=axis if channels == 1 else None,
                intensity_index=0,
                # Grey stays the lone channel's default whatever it is called: there is nothing
                # for a hue to distinguish it from, and grey is the honest rendering of one signal.
                colormap=enums.ColorMap.GREY,
                name=None,
                clim_min=clim_min,
                clim_max=clim_max,
                blending=enums.Blending.NORMAL if channels_logic.is_transmitted(label) else None,
                transmitted=channels_logic.is_transmitted(label),
            )
        ]

    sources = []
    for index in range(channels):
        label = labels.get(index)
        transmitted = channels_logic.is_transmitted(label)
        clim_min, clim_max = window(index)
        sources.append(
            _BootstrapChannel(
                intensity_axis=axis,
                intensity_index=index,
                colormap=enums.ColorMap.GREY if transmitted else (channels_logic.colormap_for(label) or _CHANNEL_COLORMAPS[index % len(_CHANNEL_COLORMAPS)]),
                name=labels.get(index, f"channel {index}"),
                clim_min=clim_min,
                clim_max=clim_max,
                blending=enums.Blending.NORMAL if transmitted else None,
                transmitted=transmitted,
            )
        )

    return sorted(sources, key=lambda channel: (not channel.transmitted, channel.intensity_index))


def _assert_rgb_capacity(render: coords_logic.RenderAxes, size: Callable[[str | None], int]) -> None:
    """Refuse an RGB recipe over an axis with no three channels to be red, green and blue.

    The same refusal :func:`core.mutations.layer.create_rgb_layer` makes for the identical
    condition -- one rule, stated at each of the two places data enters, and it should read
    the same in both.
    """
    if render.intensity is None or size(render.intensity) < 3:
        raise ValueError(f"An RGB recipe needs a channel axis with at least three positions, but '{render.intensity}' has {size(render.intensity)}. Pass a different kind, or none for the default: one layer per channel.")


def _projection_mode(render: coords_logic.RenderAxes, kind: "enums.BootstrapLayerKind") -> "enums.ProjectionMode | None":
    """The projection a bootstrapped intensity layer carries. None -- draw the plane -- unless the recipe is VOLUME.

    VOLUME is the one :class:`~core.enums.BootstrapLayerKind` with no ``LayerKind`` of its
    own, and this is why: it names a *setting* on an intensity layer rather than a different
    sort of layer. A projection collapses z; it does not composite anything, which is the
    thing that would need a graph.
    """
    if kind != enums.BootstrapLayerKind.VOLUME:
        return None
    if render.z is None:
        raise ValueError("A VOLUME recipe projects over a z axis, and this dataset has none. Pass a different kind, or none to infer one.")
    return enums.ProjectionMode.MIP

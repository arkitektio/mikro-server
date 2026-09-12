
from kante.types import Info
import strawberry

from core import types, models


from core import enums
import kante
from pydantic import BaseModel, Field, field_validator, model_validator
from core.logic import attribute_plans as attribute_plans_logic
from core.logic import column_options as column_options_logic
from core.logic.column_options import mesh_collection_system
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha, assert_contrast_limits, assert_rgba
from core.scoping import get_for_org
from core.mutations._generic import make_delete
from core.render.layer import inputs as layer_inputs
from core.render.layer import label as label_models
from core.render import color_by as color_by_models
from core.render import filter_by as filter_by_models
from core.render import joins
from core.render.layer import models as layer_models


def _build_layer_node(node: layer_inputs.LayerNodeInputModel, lens) -> layer_models.LayerNodeUnion:
    """Lower a fat GraphQL render-graph node into its strict tagged-union model, validating axes."""
    if node.kind == "channel":
        index = node.intensity_index or 0
        if node.intensity_axis:
            assert_channel_index(lens, node.intensity_axis, index)
        transfer = node.transfer or layer_inputs.TransferFunctionInputModel()
        return layer_models.ChannelSourceModel(
            intensity_axis=node.intensity_axis,
            intensity_index=index,
            label=node.label,
            visible=node.visible if node.visible is not None else True,
            transfer=layer_models.TransferFunctionModel(**transfer.model_dump()),
        )
    if node.kind == "blend":
        children = node.children or []
        if not children:
            raise ValueError("A 'blend' render node requires at least one child")
        return layer_models.BlendNodeModel(
            blending=node.blending or enums.Blending.ADDITIVE,
            label=node.label,
            children=[_build_layer_node(child, lens) for child in children],
        )
    if node.kind == "projection":
        children = node.children or []
        if not children:
            raise ValueError("A 'projection' render node requires at least one child")
        return layer_models.ProjectionNodeModel(
            mode=node.mode or enums.ProjectionMode.MIP,
            label=node.label,
            children=[_build_layer_node(child, lens) for child in children],
        )
    if node.kind == "phasor":
        phasor_axis = default_phasor_axis(lens, node.phasor_axis)
        if not phasor_axis:
            raise ValueError(f"A 'phasor' render node needs a phasor_axis, and this lens has no MICROTIME or SPECTRUM axis to default to ({[spec.name for spec in lens.axis_specs]})")
        assert_phasor_axis(lens, phasor_axis)
        return layer_models.PhasorNodeModel(
            phasor_axis=phasor_axis,
            intensity_axis=_phasor_intensity_axis(lens, node.intensity_axis, node.intensity_index or 0),
            intensity_index=node.intensity_index or 0,
            harmonic=assert_harmonic(node.harmonic),
            label=node.label,
            visible=node.visible if node.visible is not None else True,
            transfer=_build_phasor_transfer(node.phasor_transfer),
        )
    raise ValueError(f"Unknown render node kind '{node.kind}'")


def _phasor_intensity_axis(lens, intensity_axis: str | None, index: int) -> str | None:
    """Validate the detection-channel selection of a phasor node. May be None: most FLIM cubes have one detector."""
    if not intensity_axis:
        return None
    assert_channel_index(lens, intensity_axis, index)
    return intensity_axis


def _build_phasor_transfer(transfer: layer_inputs.PhasorTransferInputModel | None) -> layer_models.PhasorTransferModel:
    """Lower a phasor transfer input, validating its cursors."""
    if transfer is None:
        return layer_models.PhasorTransferModel()

    cursors = [_build_phasor_cursor(cursor) for cursor in transfer.cursors or []]
    intensity = transfer.intensity or layer_inputs.TransferFunctionInputModel()
    return layer_models.PhasorTransferModel(
        mode=transfer.mode or enums.PhasorColorMode.PHASE,
        min=transfer.min,
        max=transfer.max,
        colormap=transfer.colormap or enums.ColorMap.RAINBOW,
        weight_by_intensity=transfer.weight_by_intensity if transfer.weight_by_intensity is not None else True,
        intensity=layer_models.TransferFunctionModel(**intensity.model_dump()),
        cursors=cursors,
    )


def _build_phasor_cursor(cursor: layer_inputs.PhasorCursorInputModel) -> layer_models.PhasorCursorModel:
    """Lower one phasor cursor, checking it actually describes a region.

    A circle without a radius and a polygon with two points are not degenerate regions,
    they are *no* region: they select nothing, so the color rule they carry silently never
    fires and the client sees an overlay that ignores a cursor it can see in the response.
    """
    kind = cursor.kind or enums.PhasorCursorKind.CIRCLE

    if kind == enums.PhasorCursorKind.CIRCLE:
        if cursor.g is None or cursor.s is None or cursor.radius is None:
            raise ValueError("A 'circle' phasor cursor requires g, s and radius")
        if cursor.radius <= 0:
            raise ValueError(f"A 'circle' phasor cursor needs a positive radius, got {cursor.radius}")
    elif kind == enums.PhasorCursorKind.POLYGON:
        points = cursor.points or []
        if len(points) < 3:
            raise ValueError(f"A 'polygon' phasor cursor requires at least three (g, s) vertices, got {len(points)}")
        if any(len(point) != 2 for point in points):
            raise ValueError("Every vertex of a 'polygon' phasor cursor must be a (g, s) pair")

    return layer_models.PhasorCursorModel(
        kind=kind,
        g=cursor.g,
        s=cursor.s,
        radius=cursor.radius,
        points=cursor.points,
        color=cursor.color,
        label=cursor.label,
        visible=cursor.visible if cursor.visible is not None else True,
    )


def assert_harmonic(harmonic: int | None) -> int:
    """The harmonic of a phasor transform. 1 is the fundamental; there is no zeroth."""
    value = harmonic if harmonic is not None else 1
    if value < 1:
        raise ValueError(f"A phasor harmonic must be at least 1, got {value}")
    return value


def default_phasor_axis(lens, phasor_axis: str | None) -> str | None:
    """The axis a phasor node reduces, defaulting to the lens' first phasor-capable axis."""
    return phasor_axis or coords_logic.resolve_render_axes(lens.axis_specs).phasor


def assert_phasor_axis(lens, phasor_axis: str) -> None:
    """Check that the axis a phasor node reduces is an axis a DFT means something over.

    Only MICROTIME and SPECTRUM qualify: both are *continuous* samplings of a periodic
    signal, which is what the transform assumes. A SPACE or TIME axis is something you
    navigate and a CHANNEL axis indexes acquisitions rather than positions -- a phasor over
    any of them is arithmetic that runs, produces a (g, s), and means nothing. Nothing
    downstream can tell that from a real phasor, which is why it is rejected here.
    """
    axis = next((spec for spec in lens.axis_specs if spec.name == phasor_axis), None)
    if axis is None:
        raise ValueError(f"phasor_axis '{phasor_axis}' is not an axis of this lens ({[spec.name for spec in lens.axis_specs]})")
    if not coords_logic.is_phasor_axis(axis.type):
        raise ValueError(
            f"phasor_axis '{phasor_axis}' is a {axis.type} axis, not a MICROTIME or SPECTRUM axis. A phasor is the Fourier transform of a pixel's profile along a continuously sampled axis -- an arrival-time histogram or a spectrum. Taking it over a {axis.type} axis produces a (g, s) that means nothing."
        )


def build_render_graph(graph_input: layer_inputs.LayerRenderGraphInputModel, lens) -> dict:
    """Validate a layer render-graph input against the lens and return its JSON representation."""
    root = _build_layer_node(graph_input.root, lens)
    if not isinstance(root, layer_models.BlendNodeModel):
        raise ValueError("The root of a layer render graph must be a 'blend' node")
    return layer_models.LayerRenderGraphModel(root=root).model_dump(mode="json")


def assert_renderable(lens) -> coords_logic.RenderAxes:
    """Check a lens can be drawn, and return the axes a renderer maps to screen.

    The x/y/z/t/intensity mapping is no longer stored on the layer: it follows from
    the axis types and names, so two layers over one lens cannot disagree about it.
    See :func:`core.logic.coords.resolve_render_axes`.

    **These were two bare ``assert`` statements until 2026-08-21.** An ``assert`` is
    removed by ``python -O``, so the renderability gate vanished entirely in exactly
    the deployment most likely to run that way -- and where it did fire it surfaced as
    an ``AssertionError``, which reads as a server fault rather than as something the
    caller can act on. The same class of bug was deliberately removed from
    :func:`core.mutations.array_dataset.assert_axes_describe_the_store` a day earlier;
    this one was missed because it sits behind a helper rather than in the mutation.

    The message matches the one :func:`core.logic.scene.bootstrap_scene_from_system` raises for the
    identical condition -- the rule is stated in two places and should read the same
    in both.
    """
    axes = coords_logic.resolve_render_axes(lens.axis_specs)
    width, height = lens.get_size_of_axis(axes.x), lens.get_size_of_axis(axes.y)
    if width <= 1 or height <= 1:
        raise ValueError(
            f"Lens {lens.pk} is not renderable: its x axis '{axes.x}' ({width} px) and y axis "
            f"'{axes.y}' ({height} px) must both have more than one pixel"
        )
    return axes


def default_intensity_axis(lens, intensity_axis: str | None) -> str | None:
    """The channel axis a render node samples, defaulting to the lens' first channel axis."""
    return intensity_axis or coords_logic.resolve_render_axes(lens.axis_specs).intensity


def assert_channel_axis(lens, intensity_axis: str) -> None:
    """Check that the axis a render node samples as intensity really is a channel axis.

    The axis was only ever resolved by *name*, so `intensityDim: "t"` was accepted -- and a
    renderer then treats every timepoint as another channel to composite, stacking a
    16-frame timelapse into sixteen slabs and consuming the time axis so no time slider
    can appear. Nothing about that failure points back here, which is why it is rejected
    here.

    An axis is samplable as intensity only if it is a CHANNEL axis. A spatial or time axis
    is something you *navigate*, not something you blend.
    """
    axis = next((spec for spec in lens.axis_specs if spec.name == intensity_axis), None)
    if axis is None:
        raise ValueError(f"intensity_axis '{intensity_axis}' is not an axis of this lens ({[spec.name for spec in lens.axis_specs]})")
    if axis.type != enums.AxisTypeChoices.CHANNEL.value:
        raise ValueError(
            f"intensity_axis '{intensity_axis}' is a {axis.type} axis, not a CHANNEL axis. Rendering a {axis.type} axis as intensity composites each of its positions as a separate channel -- for a time axis that stacks every frame at once. Use the lens' channel axis, or null for single-valued data."
        )


def assert_intensity_index(lens, intensity_axis: str, index: int, *, field: str = "intensity_index") -> None:
    """Check that an index falls inside the channel axis it indexes.

    One function because it is one check. It was written out four times -- once per node
    kind that samples a channel -- before an RGB layer needed it three more times, for its
    red, green and blue. ``field`` names the input the number came from, so an RGB refusal
    says `blue_index` rather than an `intensity_index` the client never sent.
    """
    size = lens.get_size_of_axis(intensity_axis)  # raises ValueError if the axis is unknown
    if index < 0 or index >= size:
        raise ValueError(f"{field} {index} is out of range for axis '{intensity_axis}' (size {size})")


def assert_channel_index(lens, intensity_axis: str, index: int, *, field: str = "intensity_index") -> None:
    """The pair of checks every channel sample makes: the axis is a channel axis, and the index is on it."""
    assert_channel_axis(lens, intensity_axis)
    assert_intensity_index(lens, intensity_axis, index, field=field)


def _channel_source(lens, intensity_axis: str | None, index: int, transfer: layer_models.TransferFunctionModel, label: str | None = None) -> layer_models.ChannelSourceModel:
    """Build a validated channel source node. ``intensity_axis`` may be None for single-valued data (e.g. a label map)."""
    if intensity_axis:
        assert_channel_index(lens, intensity_axis, index)
    return layer_models.ChannelSourceModel(intensity_axis=intensity_axis, intensity_index=index, label=label, transfer=transfer)


class CreateLayerInputModel(BaseModel):
    lens: str
    scene: str
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None
    render_graph: layer_inputs.LayerRenderGraphInputModel


@prose_errors
@kante.pydantic_input(CreateLayerInputModel, description="Input type for creating an image from an array-like object")
class CreateLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of an existing scene to create the layer in. If not provided, a new scene will be created for the layer")
    lens: strawberry.ID = strawberry.field(description="The ID of an existing lens to create the layer from. If not provided, a new lens will be created for the layer")
    render_graph: layer_inputs.LayerRenderGraphInput = strawberry.field(
        description="The composable in-layer render graph (channels + transfer functions + in-layer blend). This is the single source of truth for how the image layer is rendered. For a simple single-channel layer use the createIntensityLayer mutation, which builds the graph for you."
    )
    blending: enums.Blending | None = strawberry.field(description="Optional blending mode used to composite this layer over the layers below it. Defaults to 'additive'.")
    opacity: float | None = strawberry.field(description="Optional layer alpha, from 0 (transparent) to 1 (opaque), for alpha-over compositing. Defaults to 1.0")
    visible: bool | None = strawberry.field(description="Optional flag controlling whether the layer participates in compositing. Defaults to true.")
    order: int | None = strawberry.field(description="Optional explicit z-index for deterministic back-to-front compositing. Defaults to 0.")


def create_layer(
    info: Info,
    input: CreateLayerInput,
) -> types.ImageLayer:
    model = input.to_pydantic()

    lens = get_for_org(models.Lens, info, id=model.lens)
    scene = get_for_org(models.Scene, info, id=model.scene)

    assert_renderable(lens)

    # The render graph is the single source of truth for how the image layer is
    # rendered; it may combine several channels, and per-channel axes are validated
    # while building it.
    render_graph = build_render_graph(model.render_graph, lens)

    graph_logic.assert_placeable_in(scene.world, graph_logic.lens_source_system(lens), destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.IMAGE,
        lens=lens,
        scene=scene,
        blending=model.blending or enums.Blending.ADDITIVE,  # Default blending mode if not provided
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
        render_graph=render_graph,
    )


class UpdateLayerInputModel(BaseModel):
    id: str
    lens: str | None = None
    scene: str | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None
    render_graph: layer_inputs.LayerRenderGraphInputModel | None = None


@prose_errors
@kante.pydantic_input(UpdateLayerInputModel, description="Input type for creating an image from an array-like object")
class UpdateLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the layer to update")
    scene: strawberry.ID | None = strawberry.field(description="The ID of an existing scene to create the layer in. If not provided, a new scene will be created for the layer")
    lens: strawberry.ID | None = strawberry.field(description="The ID of an existing lens to create the layer from. If not provided, a new lens will be created for the layer")
    blending: enums.Blending | None = strawberry.field(description="Optional blending mode used to composite this layer over the layers below it.")
    opacity: float | None = strawberry.field(description="Optional layer alpha, from 0 (transparent) to 1 (opaque), for alpha-over compositing")
    visible: bool | None = strawberry.field(description="Optional flag controlling whether the layer participates in compositing.")
    order: int | None = strawberry.field(description="Optional explicit z-index for deterministic back-to-front compositing.")
    render_graph: layer_inputs.LayerRenderGraphInput | None = strawberry.field(description="Optional composable in-layer render graph. When provided, it replaces the layer's render graph (the single source of truth for how the image layer is rendered).")


#: What each kind's render settings are called, and which mutation writes them. One table so
#: a refusal can name the mutation the caller should have used instead of saying only "no",
#: and so adding a kind cannot leave a guard silently listing eight of nine.
_KIND_VOCABULARY: "dict[str, tuple[str, str]]" = {
    enums.LayerKind.IMAGE.value: ("a composable render graph", "updateLayer"),
    enums.LayerKind.INTENSITY.value: ("one channel's colormap, contrast limits, gamma and projection", "updateIntensityLayer"),
    enums.LayerKind.RGB.value: ("three channel indices and one pair of contrast limits", "updateRgbLayer"),
    enums.LayerKind.PHASOR.value: ("a phasor reduction and its color transfer", "updatePhasorLayer"),
    enums.LayerKind.LABEL.value: ("an id-to-color hashing, a background id and its pickers", "updateLabelLayer"),
    enums.LayerKind.ANNOTATION.value: ("no render settings of its own -- per-shape styling lives on the annotations", ""),
    enums.LayerKind.POINT.value: ("per-point size and color columns", "updatePointLayer"),
    enums.LayerKind.TRACK.value: ("a track color column and line width", "updateTrackLayer"),
    enums.LayerKind.MESH.value: ("a material, shading and its pickers", "updateMeshLayer"),
    enums.LayerKind.NETWORK.value: ("a colour, its segment widths, whether direction and nodes are drawn, and its pickers", "updateNetworkLayer"),
    enums.LayerKind.VECTOR.value: ("a glyph style, a sampling stride, a magnitude scale and a magnitude colormap window", "updateVectorLayer"),
}


def _a(kind: str) -> str:
    """"a label" / "an image". A vowel is not worth a sentence, but a refusal that reads wrong reads as a bug."""
    return f"an {kind}" if kind[:1] in "aeiou" else f"a {kind}"


def assert_kind(layer, expected: enums.LayerKind, *, mutation: str) -> None:
    """Refuse a layer of the wrong kind, naming the mutation that does want it.

    Every update mutation carries this, and it is load-bearing rather than tidy. Without it
    one call writes a render graph onto a label layer -- or intensity columns onto an RGB one
    -- and leaves the row carrying two recipes at once, which is exactly the state that
    keeping each kind's recipe in its own columns exists to make unrepresentable.

    A kind is fixed for the life of a row, and this is what fixes it. Turning an intensity
    layer into one that composites means creating an image layer and deleting this one, not
    mutating this one into a different shape: a row that changed kind would be a row whose
    columns and whose `kind` disagreed for as long as the write took, and every reader
    resolving its GraphQL type by `kind` would have to cope with that.
    """
    if layer.kind == expected.value:
        return
    theirs, theirs_mutation = _KIND_VOCABULARY.get(layer.kind, ("different render settings", ""))
    mine, _ = _KIND_VOCABULARY[expected.value]
    instead = f" Use {theirs_mutation}." if theirs_mutation else ""
    raise ValueError(f"Layer {layer.pk} is {_a(layer.kind)} layer, not {_a(expected.value)} layer. {mine.capitalize()} is not its render vocabulary -- what it carries is {theirs}.{instead}")


def update_layer(
    info: Info,
    input: UpdateLayerInput,
) -> types.ImageLayer:
    model = input.to_pydantic()

    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.IMAGE, mutation="updateLayer")
    lens = get_for_org(models.Lens, info, id=model.lens) if model.lens else layer.lens
    scene = get_for_org(models.Scene, info, id=model.scene) if model.scene else layer.scene

    assert_renderable(lens)

    render_graph = None
    if model.render_graph is not None:
        # The render graph is the single source of truth for how the image layer is
        # rendered; per-channel axes are validated while building it.
        render_graph = build_render_graph(model.render_graph, lens)

    # Rebinding a layer into a scene (or onto a lens) is the same claim creating it
    # there makes, so it meets the same gate. Only an actual change is checked: a
    # no-op resend of the current ids must not re-litigate an existing layer.
    if (model.lens and lens.pk != layer.lens_id) or (model.scene and scene.pk != layer.scene_id):
        graph_logic.assert_placeable_in(scene.world, graph_logic.lens_source_system(lens), destination=f"the world of scene '{scene.name}'")

    if model.lens:
        layer.lens = lens
    if model.scene:
        layer.scene = scene
    if model.blending:
        layer.blending = model.blending
    if model.opacity is not None:
        layer.opacity = model.opacity
    if model.visible is not None:
        layer.visible = model.visible
    if model.order is not None:
        layer.order = model.order
    if render_graph is not None:
        layer.render_graph = render_graph
    layer.save()
    return layer


# ---------------------------------------------------------------------------
# The fixed-shape layer kinds
#
# These are not convenience wrappers around ``create_layer`` any more. Each one
# creates a layer of its own ``kind``, carrying its render settings as columns:
#   * createIntensityLayer - one channel through a colormap (fluorescence)
#   * createVolumeLayer    - the same, with `projection_mode` set: a projection
#                            collapses z, it does not composite, so a volume is
#                            an INTENSITY layer with a mode and not a kind
#   * createRgbLayer       - three channels as the red, green and blue of one picture
#   * createPhasorLayer    - one axis reduced to a phasor
# (createLabelLayer is the fifth and oldest of them, in the label section below.)
#
# They stopped building render graphs because the graphs they built said nothing
# a column could not: a blend of one child is that child, and a three-child
# red/green/blue blend is indistinguishable from three fluorescence markers
# somebody tinted, which is the commoner reading -- so the authored fact was lost
# at write time. What each still does is resolve the lens' axes, run every check
# it always ran, and write the row. The layer is still the alpha-blended unit
# (opacity + layer-level blending); the recipe is now the layer's own fields.
#
# ``createLayer`` and the render graph remain, for the layers that genuinely
# composite: several channels together, an authored transfer curve, an inverted
# mapping, per-channel opacity. That is what ``LayerKind.IMAGE`` means now. A
# solid tint is not on that list -- ``createIntensityLayer`` takes one, because a
# tint names the hue one ramp ends at rather than describing how two things
# combine, and a colour read off an emission wavelength is the commonest thing a
# converter knows about a channel.
# ---------------------------------------------------------------------------


def _create_graph_layer(info: Info, *, lens_id: str, scene_id: str, root: layer_models.BlendNodeModel, blending: enums.Blending, opacity: float | None, visible: bool | None, order: int | None) -> "models.Layer":
    """Create an image layer from a prebuilt render graph. Placement is a scene-level edge, not a layer field."""
    lens = get_for_org(models.Lens, info, id=lens_id)
    scene = get_for_org(models.Scene, info, id=scene_id)
    render_graph = layer_models.LayerRenderGraphModel(root=root).model_dump(mode="json")

    graph_logic.assert_placeable_in(scene.world, graph_logic.lens_source_system(lens), destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.IMAGE,
        lens=lens,
        scene=scene,
        blending=blending,
        opacity=opacity if opacity is not None else 1.0,
        visible=visible if visible is not None else True,
        order=order or 0,
        render_graph=render_graph,
    )


def _create_flat_layer(info: Info, *, lens_id: str, scene_id: str, kind: enums.LayerKind, blending: enums.Blending, opacity: float | None, visible: bool | None, order: int | None, name: str | None = None, **render) -> "models.Layer":
    """Create a layer of a fixed-shape kind from its render columns.

    The flat sibling of :func:`_create_graph_layer`, and it does the same three things in the
    same order: fetch the lens and scene under the org scope, refuse the layer if its data
    cannot be placed in the scene's world, and write the row. ``**render`` is the kind's own
    columns, which is exactly what differs between the callers -- everything above it is
    shared, and shared here rather than copied four times.
    """
    lens = get_for_org(models.Lens, info, id=lens_id)
    scene = get_for_org(models.Scene, info, id=scene_id)

    graph_logic.assert_placeable_in(scene.world, graph_logic.lens_source_system(lens), destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=kind,
        lens=lens,
        scene=scene,
        name=name,
        blending=blending,
        opacity=opacity if opacity is not None else 1.0,
        visible=visible if visible is not None else True,
        order=order or 0,
        **render,
    )


class CreateRgbLayerInputModel(BaseModel):
    lens: str
    scene: str
    intensity_axis: str | None = None
    red_index: int = 0
    green_index: int = 1
    blue_index: int = 2
    clim_min: float | None = None
    clim_max: float | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None

    @model_validator(mode="after")
    def _contrast_limits_are_a_range(self) -> "CreateRgbLayerInputModel":
        assert_contrast_limits(self.clim_min, self.clim_max)
        return self


@prose_errors
@kante.pydantic_input(CreateRgbLayerInputModel, description="Create a layer that composites three channels of a lens as red, green and blue")
class CreateRgbLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the data")
    intensity_axis: str | None = strawberry.field(default=None, description="The channel axis to index for the three colors. Defaults to the lens' first channel axis.")
    red_index: int | None = strawberry.field(default=None, description="Channel index mapped to red (default 0)")
    green_index: int | None = strawberry.field(default=None, description="Channel index mapped to green (default 1)")
    blue_index: int | None = strawberry.field(default=None, description="Channel index mapped to blue (default 2)")
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units, applied to all three channels")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units, applied to all three channels")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_rgb_layer(info: Info, input: CreateRgbLayerInput) -> types.RgbLayer:
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.lens)
    assert_renderable(lens)
    intensity_axis = default_intensity_axis(lens, model.intensity_axis)

    red, green, blue = (
        model.red_index if model.red_index is not None else 0,
        model.green_index if model.green_index is not None else 1,
        model.blue_index if model.blue_index is not None else 2,
    )
    if intensity_axis:
        assert_channel_axis(lens, intensity_axis)
        # The same >=3 refusal `core.logic.scene._rgb_root` raises for the identical
        # condition, and worth raising before the per-index checks: "this axis has two
        # channels" is the useful sentence, where "blue_index 2 is out of range" makes the
        # caller work out why an RGB layer wanted a third.
        size = lens.get_size_of_axis(intensity_axis)
        if size < 3:
            raise ValueError(f"An RGB layer needs a channel axis with at least three positions, but '{intensity_axis}' has {size}. Use createIntensityLayer for a single channel, or createLayer to composite the ones you have.")
        for index, field in ((red, "red_index"), (green, "green_index"), (blue, "blue_index")):
            assert_intensity_index(lens, intensity_axis, index, field=field)

    # NORMAL, not ADDITIVE: a photograph sits *over* what is beneath it. Summing it with the
    # layers below would be light arriving from two acquisitions at once, which is what
    # additive means and is not what a picture is.
    return _create_flat_layer(
        info,
        lens_id=model.lens,
        scene_id=model.scene,
        kind=enums.LayerKind.RGB,
        blending=enums.Blending.NORMAL,
        opacity=model.opacity,
        visible=model.visible,
        order=model.order,
        intensity_axis=intensity_axis,
        red_index=red,
        green_index=green,
        blue_index=blue,
        clim_min=model.clim_min,
        clim_max=model.clim_max,
    )


class CreateIntensityLayerInputModel(BaseModel):
    lens: str
    scene: str
    intensity_axis: str | None = None
    intensity_index: int = 0
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    clim_min: float | None = None
    clim_max: float | None = None
    gamma: float | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None

    @field_validator("color")
    @classmethod
    def _color_is_rgba(cls, color: list[int] | None) -> list[int] | None:
        if color is not None:
            assert_rgba(color, field="color", maximum=255)
        return color

    @model_validator(mode="after")
    def _contrast_limits_are_a_range(self) -> "CreateIntensityLayerInputModel":
        assert_contrast_limits(self.clim_min, self.clim_max)
        return self


@prose_errors
@kante.pydantic_input(CreateIntensityLayerInputModel, description="Create a single-channel intensity layer rendered through a colormap (e.g. a fluorescence channel)")
class CreateIntensityLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the data")
    intensity_axis: str | None = strawberry.field(default=None, description="The channel axis to index. Defaults to the lens' first channel axis; may be null for single-valued data.")
    intensity_index: int | None = strawberry.field(default=None, description="The channel index to render (default 0)")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap to render the intensity through (default 'grey')")
    color: list[int] | None = strawberry.field(default=None, description="A solid RGBA color to tint the channel with, instead of a colormap: four components, each 0..255. Overrides `colormap` where both are given -- for a channel whose colour is a measured fact (an emission wavelength, or what the acquisition software saved) and matches no named map")
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units -- not a normalized fraction")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units -- not a normalized fraction")
    gamma: float | None = strawberry.field(default=None, description="Gamma correction (default 1.0)")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'additive', suitable for fluorescence)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_intensity_layer(info: Info, input: CreateIntensityLayerInput) -> types.IntensityLayer:
    return _create_intensity_layer(info, input.to_pydantic(), projection_mode=None)


def _create_intensity_layer(info: Info, model, *, projection_mode: enums.ProjectionMode | None) -> "models.Layer":
    """The body `createIntensityLayer` and `createVolumeLayer` share.

    They differ in one field. A volume is one channel drawn through z rather than one channel
    drawn flat -- a projection collapses an axis, it does not composite anything -- so it is
    an intensity layer with `projection_mode` set, and not a kind of its own. Two mutations
    because they are two things to ask for; one function because they are one thing to build.
    """
    lens = get_for_org(models.Lens, info, id=model.lens)
    render = assert_renderable(lens)
    intensity_axis = default_intensity_axis(lens, model.intensity_axis)
    intensity_index = model.intensity_index or 0
    if intensity_axis:
        assert_channel_index(lens, intensity_axis, intensity_index)
    elif render.vector is not None:
        # The explicit case is already refused: `assert_channel_axis` rejects a DISPLACEMENT
        # axis named as `intensity_axis`. This closes the default path, which was worse --
        # with the axis omitted, "single-valued data" resolved and the field drew as one grey
        # layer over its components. Wrong picture, no error, nothing pointing back here.
        raise ValueError(
            f"This lens carries a DISPLACEMENT axis ('{render.vector}'), so its values are the components of a per-point vector, not intensities. "
            "Drawing it as a grey intensity layer renders the components as if they were a picture. Use createVectorLayer -- or name a CHANNEL axis explicitly if the lens genuinely carries one beside the vector."
        )

    return _create_flat_layer(
        info,
        lens_id=model.lens,
        scene_id=model.scene,
        kind=enums.LayerKind.INTENSITY,
        blending=model.blending or enums.Blending.ADDITIVE,
        opacity=model.opacity,
        visible=model.visible,
        order=model.order,
        intensity_axis=intensity_axis,
        intensity_index=intensity_index,
        colormap=model.colormap or enums.ColorMap.GREY,
        # Both, and the colour wins on read -- `TransferFunction`'s rule. The grey default
        # still lands beside a tint rather than being suppressed by it: a client that later
        # clears the colour gets the same layer it would have had, not a colourless one.
        color=model.color,
        clim_min=model.clim_min,
        clim_max=model.clim_max,
        gamma=model.gamma if model.gamma is not None else 1.0,
        projection_mode=projection_mode,
    )


# ---------------------------------------------------------------------------
# Label layers
#
# A label layer is its own kind, not an image layer with a flag. Its source is a
# lens like an image layer's -- as POINT and TRACK share one table dataset -- but
# none of an image's render vocabulary survives the change of value domain, so
# none of it is reachable from here. What it carries instead lives in
# ``core.render.layer.label``, and the one part that needs the coordinate graph is
# ``colorBy``, checked below.
# ---------------------------------------------------------------------------

# The measure-vs-categorical split moved to `core.logic.column_options`, where the options
# query can publish the same frozenset it is enforced against. Two copies would be a picker
# offering a colormap this boundary then refuses.
_MEASURE_ROLES = column_options_logic.MEASURE_ROLES


def _column_on(table, column_name: str):
    """A named column of a table, or the refusal that lists what it does declare."""
    column = next((candidate for candidate in table.columns.all() if candidate.name == column_name), None)
    if column is None:
        declared = ", ".join(f"'{candidate.name}'" for candidate in table.columns.all()) or "none"
        raise ValueError(f"Table '{table.name}' declares no column '{column_name}'. Its columns are: {declared}.")
    return column


def _resolve_joined_column(reachable: dict, join_path, table_id: str, column_name: str, *, source: str) -> tuple:
    """The claims a joined-column reference makes, checked against the facts in hand.

    Shared by `colorBy` and `filterBy` because it is one relation, not two: both name a column
    this source's ids can reach, and both are unrunnable in exactly the same ways. A reference
    naming an unrelated table is not a preference a client can hold onto until the edge shows
    up; it is a join nothing can execute.

    With an empty ``join_path`` this is the original check and nothing more: the named table must
    be FIELD-reachable and must declare the column. A path walks further, and every hop is
    checked against the *schema*, never against the coordinate graph -- ``references`` is a
    declared foreign key, not an edge, and only the first table is reached by an edge at all.

    Returns the terminal table and column plus the **canonical** steps: table pks and column
    names as the server resolved them, so what gets stored is what was checked rather than what
    was typed.
    """
    if len(join_path) > column_options_logic.MAX_JOIN_DEPTH:
        raise ValueError(
            f"`joinPath` takes {len(join_path)} hops, more than the {column_options_logic.MAX_JOIN_DEPTH} this server will follow. "
            "Each hop is another lookup a renderer performs per object; a chain this long is almost always a table that wants a column of its own."
        )

    first_id = str(join_path[0].table) if join_path else str(table_id)
    table = reachable.get(first_id)
    if table is None:
        known = ", ".join(f"'{candidate.name}' ({pk})" for pk, candidate in reachable.items()) or "none"
        reached = "The first hop of a `joinPath` starts there too" if join_path else "Author the edge with createTableDataset(keyedBy:) naming it"
        raise ValueError(
            f"Table dataset {first_id} is not reachable from {source} by a FIELD edge, so its rows cannot be looked up from the ids it carries. "
            f"{reached}, or use one of the tables that already key off it: {known}."
        )

    steps: list[joins.JoinStepModel] = []
    visited = {str(table.pk)}
    for index, step in enumerate(join_path):
        column = _column_on(table, step.column)
        target = column.references
        if target is None:
            raise ValueError(
                f"joinPath[{index}]: column '{column.name}' of table '{table.name}' references no table, so there is nothing to hop to. "
                "A hop column declares its target with `references` at createTableDataset; a column that identifies nothing is a value, not a join."
            )

        next_id = str(join_path[index + 1].table) if index + 1 < len(join_path) else str(table_id)
        if str(target.pk) != next_id:
            raise ValueError(
                f"joinPath[{index}]: column '{column.name}' of table '{table.name}' identifies rows of table '{target.name}' ({target.pk}), "
                f"but the next step names table {next_id}. A hop goes where the column says it goes."
            )
        if next_id in visited:
            raise ValueError(f"joinPath[{index}]: hopping into table '{target.name}' ({target.pk}) revisits a table this path already stands in. A cycle reads the same rows forever.")

        steps.append(joins.JoinStepModel(table=str(table.pk), column=column.name))
        visited.add(next_id)
        table = target

    return table, _column_on(table, column_name), steps


def reachable_tables(info: Info, system) -> dict:
    """One walk of the FIELD edges, for a mutation that checks both pickers against them.

    Every layer kind that publishes pickers publishes *two* of them -- a colour picker and a
    filter picker -- over the same relation, so a call naming both would otherwise walk the
    coordinate graph twice to answer one question. Rooted on the system rather than the source
    for the same reason :func:`_build_color_by` is: a mask and a mesh collection are the same
    thing to a FIELD edge.
    """
    return attribute_plans_logic.field_reachable_tables(system, info.context.request.organization)


def mesh_reachable_tables(info: Info, collection) -> dict:
    """That walk, rooted on a mesh collection."""
    return reachable_tables(info, mesh_collection_system(collection))


def _resolve_sparse_slice(info: Info, system, dataset_id, at, *, source: str, reachable: dict | None = None):
    """Resolve and check one slice: the matrix, and a position along every identified axis.

    Factored out of the colouring because a **rule** asks exactly the same three questions —
    is the matrix reachable, does `at` name the axes it identifies itself by, is there a
    layout that makes the read one contiguous range. Sharing it is what keeps "the set
    offered is the set accepted" true across the two pickers rather than only within each;
    two copies of these checks would be two chances to drift.

    Returns the resolved dataset and the positions, sorted by axis so a slice named in two
    orders stores once.
    """
    if reachable is None:
        reachable = attribute_plans_logic.field_reachable_sparse_datasets(system, info.context.request.organization)

    dataset = reachable.get(str(dataset_id))
    if dataset is None:
        known = ", ".join(f"'{candidate.name}' ({pk})" for pk, candidate in reachable.items()) or "none"
        raise ValueError(
            f"Sparse dataset {dataset_id} is not reachable from {source} by a FIELD edge, so the ids it is indexed by are not the ids this source supplies. "
            f"Author the edge with createSparseDataset(keyedBy:) naming this source, or use one that already keys off it: {known}."
        )

    names = dataset.axis_names
    indexed = {names[array.indexed_axis] for array in dataset.arrays.all() if 0 <= array.indexed_axis < len(names)}
    identified = {reference.axis for reference in dataset.axis_references.all()}
    shape = dataset.shape

    positions = list(at)
    named = [position.axis for position in positions]
    if sorted(named) != sorted(identified):
        raise ValueError(
            f"`at` names {sorted(named)}, but '{dataset.name}' is selected along {sorted(identified)} -- the axes it identifies itself. "
            f"The other axis ({sorted(set(names) - identified)}) is the one {source} supplies ids for, and naming a position along it would be asking for one object's whole profile rather than one value per object."
        )

    for position in positions:
        extent = shape[names.index(position.axis)] if position.axis in names and len(shape) == len(names) else None
        if extent is not None and not 0 <= position.value < extent:
            raise ValueError(
                f"`at` names position {position.value} along '{position.axis}', which runs 0..{extent - 1} in '{dataset.name}'. A position is a row of the table that axis references, not an id of its own."
            )

    if not any(position.axis in indexed for position in positions):
        available = ", ".join(sorted(indexed)) or "none"
        raise ValueError(
            f"'{dataset.name}' holds no layout indexed on any of {sorted(named)}, so there is no contiguous slice to read and answering would mean scanning every byte of the store "
            f"-- measured at 1 777 ms against 2.2 ms on a 16 um matrix. It is indexed on: {available}. Register a layout compressing one of the axes `at` names."
        )

    resolved = [color_by_models.AxisPositionModel(axis=position.axis, value=position.value) for position in positions]
    return dataset, sorted(resolved, key=lambda position: position.axis)


def _build_sparse_color_by(info: Info, system, color_by, *, source: str, reachable: dict | None = None) -> color_by_models.ColorByModel:
    """Resolve and check a SPARSE `colorBy`: one slice of a matrix this source's ids index.

    Three things have to hold and none is knowable from the input alone.

    **The matrix has to be reachable** by a FIELD edge from this source, exactly as a table
    does -- the ids that select a value have to be the ids this source supplies.

    **The named position has to be along an axis the source does *not* index.** A sparse
    dataset is indexed on one axis and identified on the other; the source supplies the first,
    so a colouring names a position along the second. Naming a position along the keyed axis
    would be asking for one object's whole profile, which is a hover, not a colouring.

    **A layout indexed on that axis has to exist.** This is the check with no analogue on the
    table side, and the reason the model carries stores at all: reading one slice from the
    store whose `indptr` indexes the other axis is not slower, it is a scan of every byte --
    1 777 ms against 2.2 ms, measured. A colouring the server knows would do that is one it
    refuses rather than publishes.
    """
    dataset, positions = _resolve_sparse_slice(
        info, system, color_by.dataset, color_by.at, source=source, reachable=reachable
    )

    return color_by_models.ColorByModel(
        kind="SPARSE",
        dataset=str(dataset.pk),
        at=positions,
        colormap=color_by.colormap,
        min=color_by.min,
        max=color_by.max,
    )


def _build_graph_color_by(collection, color_by) -> color_by_models.ColorByModel:
    """Resolve and check a GRAPH `colorBy`: a per-node value the collection itself carries.

    The one entry kind validated against the *collection* rather than against the coordinate
    graph: the vocabulary is what its manifest declared at registration
    (``KonnektionStore.attribute_vocabulary``, `radius` included when the encoding carries
    one), which is also exactly the set the options query offers -- one list, so the offered
    set and the accepted set cannot drift.
    """
    vocabulary = collection.store.attribute_vocabulary()
    if color_by.attribute not in vocabulary:
        declared = ", ".join(f"'{name}'" for name in vocabulary)
        carried = (
            f"It carries: {declared} -- the set its manifest declares."
            if vocabulary
            else "It declares none at all -- it was built before konnektion stored attributes, so rebuild and re-upload it to carry the intrinsic metrics."
        )
        raise ValueError(f"Network collection {collection.pk} carries no per-node attribute '{color_by.attribute}'. {carried}")

    # Re-checked here even though the union member refuses it, because this is the boundary
    # every path funnels through -- the same both-ends discipline the COLUMN role rule gets.
    if color_by.colormap in enums.QUALITATIVE_COLORMAPS:
        raise ValueError(
            f"Attribute '{color_by.attribute}' is measured -- a per-node metric is an ordered value -- so it is coloured by a continuous colormap over its range, "
            f"and '{color_by.colormap.value}' is qualitative. Name a continuous one."
        )

    target = getattr(color_by, "target", None)
    return color_by_models.ColorByModel(
        kind="GRAPH",
        attribute=color_by.attribute,
        target=None if target is None else getattr(target, "value", target),
        colormap=color_by.colormap,
        min=color_by.min,
        max=color_by.max,
    )


def _build_color_by(info: Info, system, color_by: layer_inputs.ColorByInputModel, *, source: str = "this mask", reachable: dict | None = None, reachable_sparse: dict | None = None, join_path=(), collection=None, node_targets: dict | None = None) -> color_by_models.ColorByModel:
    """Resolve and check a `colorBy` against the FIELD edge that makes it answerable.

    Two things have to hold, and neither is knowable from the input alone: the table must
    be one this source's ids actually dereference into -- a FIELD edge, the same relation
    ``attributePlans`` publishes -- and the named column must exist on it. A `colorBy`
    naming an unrelated table is not a display preference the client can hold onto until
    the edge shows up; it is a join nothing can execute.

    Takes the **system**, not the container, because that is the one identifier that means
    the same thing for a mask and for a mesh collection -- the same reason `attributePlans`
    is rooted on one. ``source`` only names the thing in the refusal.

    ``reachable`` is that walk's result, passed in when a caller is checking a *list* of
    colourings against one system: a layer publishes a picker, and resolving the same FIELD
    edges once per entry would be N walks of the coordinate graph to answer one question.
    Omitted, it is resolved here, which is what a single colouring wants.

    Returns the shared :class:`~core.render.color_by.ColorByModel`, never a picker entry: the
    caption is the caller's, because the caller is what knows which picker is being filled.
    """
    if color_by.kind == enums.ColorSourceKind.GRAPH:
        # Validated against the collection, not the graph walk: the value lives *in* the
        # collection's own geometry, so reachability is not even a question.
        if collection is None:
            raise ValueError(
                "a GRAPH colouring reads a per-node value a network collection carries, and this layer kind has no such collection. It is for network layers only"
            )
        return _build_graph_color_by(collection, color_by)

    if color_by.kind == enums.ColorSourceKind.SPARSE:
        # A different source, the same question -- so a sibling rather than a branch threaded
        # through the checks below, none of which is about a column here.
        return _build_sparse_color_by(info, system, color_by, source=source, reachable=reachable_sparse)

    if reachable is None:
        reachable = attribute_plans_logic.field_reachable_tables(system, info.context.request.organization)
    # Passed in rather than read off `color_by`, because this function is also the one-colouring
    # entry point -- a caller resolving a single colouring with no picker around it has no path
    # to give -- and a parameter says that where a `getattr` would only imply it.
    table, column, steps = _resolve_joined_column(reachable, join_path, color_by.table, color_by.column, source=source)

    # Which *sort* of colormap the column takes, which is the same question the role has always
    # answered -- it used to be spelled "a colormap or a `classColors` map", and a qualitative
    # colormap is what that map always was. So one rule with two halves rather than two fields.
    is_measure = column.role in _MEASURE_ROLES
    is_qualitative = color_by.colormap in enums.QUALITATIVE_COLORMAPS
    if is_measure and is_qualitative:
        raise ValueError(
            f"Column '{column.name}' is a {column.role} column -- its values are measured and ordered, so they are coloured by a continuous colormap over their range, "
            f"and '{color_by.colormap.value}' is qualitative. Name a continuous one."
        )
    if not is_measure and color_by.colormap is not None and not is_qualitative:
        raise ValueError(
            f"Column '{column.name}' is a {column.role} column -- its values are categorical, so '{color_by.colormap.value}' would impose an order they do not have. "
            f"Name a qualitative colormap instead: {', '.join(sorted(member.value for member in enums.QUALITATIVE_COLORMAPS))}."
        )
    if not is_measure and (color_by.min is not None or color_by.max is not None):
        raise ValueError(f"Column '{column.name}' is a {column.role} column -- its values are categorical, so a `min`/`max` window would impose an order they do not have. Drop them.")

    # A node/edge table's entry carries the granularity its shape derives -- STAMPED here,
    # never sent: the caller has no field for it on a COLUMN entry, and re-deriving at every
    # read would put a per-render query where a stored fact belongs. Joins out of one are
    # refused in v1: a hop would carry the (object, node) composite key into a table declared
    # for a single id -- colour by the hop target through its own picker instead.
    first_id = str(join_path[0].table) if join_path else str(color_by.table)
    stamped_target = (node_targets or {}).get(first_id)
    if stamped_target is not None and steps:
        raise ValueError(
            f"a per-{stamped_target.lower()} table's rows are addressed by a composite key, and a `joinPath` out of one would carry it into a table declared for a single id. "
            "Colour by the hop target's own picker entry instead."
        )

    return color_by_models.ColorByModel(
        kind="COLUMN",
        table=str(table.pk),
        column=column.name,
        join_path=steps,
        target=stamped_target,
        colormap=color_by.colormap,
        min=color_by.min,
        max=color_by.max,
    )


def build_color_bys(
    info: Info,
    system,
    color_bys: "list[layer_inputs.ColorByInputModel] | None",
    *,
    source: str,
    entry_model: type = color_by_models.PickerColorByModel,
    reachable: dict | None = None,
    reachable_sparse: dict | None = None,
    collection=None,
    node_targets: dict | None = None,
) -> list[dict] | None:
    """Validate a layer's whole colour picker and return what the JSON column stores.

    One builder for both layer kinds, because the one check -- is this table actually reachable
    by a FIELD edge, and does the column exist -- is one check: a mesh collection reaches its
    table by exactly the relation a mask does. Rooted on the **system** for that reason, the
    same one :func:`_build_color_by` gives.

    ``None`` means the caller did not name the picker (an update that leaves it alone) and is
    passed straight back; an empty list means the caller *cleared* it, which is a different
    thing and is stored as one. Every entry is checked against a single walk of the FIELD
    edges, and the entry's index rides in the refusal, because "some table is unreachable"
    is not actionable when a client sent five.
    """
    if color_bys is None:
        return None
    if reachable is None:
        reachable = reachable_tables(info, system)
    # Resolved once for the whole picker, and only if something in it is sparse: the walk is the
    # same one `reachable` came from, but a picker of column colourings should not pay for it.
    # A caller with a differently-rooted walk (the network wrapper's depth-zero one) passes its
    # own in, exactly as it does `reachable`.
    if reachable_sparse is None and any(entry.kind == enums.ColorSourceKind.SPARSE for entry in color_bys):
        reachable_sparse = attribute_plans_logic.field_reachable_sparse_datasets(system, info.context.request.organization)

    entries: list[dict] = []
    seen: dict[tuple, int] = {}
    for index, color_by in enumerate(color_bys):
        try:
            checked = _build_color_by(
                info, system, color_by, source=source, reachable=reachable, reachable_sparse=reachable_sparse, join_path=color_by.join_path, collection=collection, node_targets=node_targets
            )
        except ValueError as error:
            raise ValueError(f"colorBys[{index}]: {error}") from error

        # A repeat is refused, and what counts as a repeat is *the whole rendering*, not the
        # column: two colormaps over one measure are two colourings someone might genuinely want
        # to switch between, while two entries agreeing on column, colormap and window
        # render identically and ask a viewer to choose between a thing and itself. The caption
        # is deliberately not part of the key -- a second name is not a second colouring.
        # Refused rather than deduplicated, because dropping one silently would renumber
        # `activeColorBy` under the caller.
        # The class-colour map is compared as canonical JSON: its values are lists, so the dict
        # is not hashable, and two maps that differ only in key order are the same map.
        key = (
            # The variant is part of what a colouring *is*, so it is part of the key -- and so
            # are the fields only one variant carries, or two slices of one matrix would key
            # identically and the second be refused as a duplicate of the first. `attribute`
            # and `target` join for the GRAPH member: one metric aimed at nodes and the same
            # metric aimed at edges are two colourings someone might genuinely switch between.
            checked.kind,
            checked.dataset,
            tuple((position.axis, position.value) for position in checked.at),
            tuple((step.table, step.column) for step in checked.join_path),
            checked.table,
            checked.column,
            checked.attribute,
            checked.target,
            checked.colormap,
            # The window is part of the rendering, not a detail of it: one measure through one
            # colormap over two windows is two colourings someone might genuinely switch between.
            checked.min,
            checked.max,
        )
        if key in seen:
            if checked.kind == "COLUMN":
                what = f"'{checked.column}' of table {checked.table}"
            elif checked.kind == "GRAPH":
                what = f"graph attribute '{checked.attribute}' ({checked.target})"
            else:
                what = f"{', '.join(f'{position.axis}={position.value}' for position in checked.at)} of sparse dataset {checked.dataset}"
            raise ValueError(
                f"colorBys[{index}] colours by {what} exactly as colorBys[{seen[key]}] does -- same source, same colormap, same window. "
                "Two entries that render identically are one colouring wearing two names; drop one, or give it a different colormap, window or source."
            )
        seen[key] = index
        entries.append(entry_model(**checked.model_dump(), label=color_by.label).model_dump(mode="json"))

    return entries


def build_mesh_color_bys(info: Info, collection, color_bys: "list[layer_inputs.MeshColorByInputModel] | None", *, reachable: dict | None = None) -> list[dict] | None:
    """That builder, rooted on a mesh collection and storing mesh-named entries."""
    return build_color_bys(
        info,
        mesh_collection_system(collection),
        color_bys,
        source="this collection",
        entry_model=color_by_models.MeshColorByModel,
        reachable=reachable,
    )


def network_reachable_walks(info: Info, collection) -> tuple[dict, dict, dict]:
    """Both reachability walks for a network picker, rooted at depth zero, plus the node tables.

    Depth zero is the load-bearing part, and it is a correctness rule rather than a budget:
    the fact walk crosses derivations in both directions, so an unbounded walk rooted on a
    network's system reaches the image the network was traced from and offers tables keyed by
    *mask instance* ids -- real tables, genuinely reachable, and not executable from an object
    id. Only FIELD edges standing on the collection's own system (the ones
    `createTableDataset(keyedBy: {kind: NETWORK_COLLECTION})` authors) key by object ids, so
    only those are accepted -- and :func:`core.logic.column_options.build_network_column_options`
    offers exactly the same set, which is the invariant the whole picker rests on.

    The third element is the other door: the collection's node/edge tables
    (:func:`core.logic.column_options.network_node_tables`), which are product spaces the walk
    drops by design -- an object id alone cannot address a row keyed by the (object, node)
    pair -- and which THIS layer kind alone can execute, its geometry already holding every
    node id. They are seeded into ``reachable`` here, the `point_reachable_tables` move, and
    the mapping ``table_pk -> "NODE"|"EDGE"`` is what the builders stamp `target` from.
    """
    system = column_options_logic.network_collection_system(collection)
    organization = info.context.request.organization
    node_tables = column_options_logic.network_node_tables(collection)
    reachable = attribute_plans_logic.field_reachable_tables(system, organization, max_depth=0)
    reachable.update({pk: table for pk, (table, _) in node_tables.items()})
    return (
        reachable,
        attribute_plans_logic.field_reachable_sparse_datasets(system, organization, max_depth=0),
        {pk: granularity for pk, (_, granularity) in node_tables.items()},
    )


def build_network_color_bys(info: Info, collection, color_bys, *, walks: tuple[dict, dict, dict] | None = None) -> list[dict] | None:
    """The colour builder, rooted on a network collection and storing network-named entries.

    The one call site where GRAPH entries are legal (`collection` is what resolves them), the
    one whose walks are depth-zero-rooted, and the one whose reachable set carries the
    collection's node/edge tables with their stamped granularities -- see
    :func:`network_reachable_walks`.
    """
    if color_bys is None:
        return None
    reachable, reachable_sparse, node_targets = walks if walks is not None else network_reachable_walks(info, collection)
    return build_color_bys(
        info,
        column_options_logic.network_collection_system(collection),
        color_bys,
        source="this network collection",
        entry_model=color_by_models.NetworkColorByModel,
        reachable=reachable,
        reachable_sparse=reachable_sparse,
        collection=collection,
        node_targets=node_targets,
    )


def build_filter_bys(
    info: Info,
    system,
    filter_bys: "list[filter_by_models.PickerFilterByModel] | None",
    *,
    source: str,
    entry_model: type = filter_by_models.PickerFilterByModel,
    reachable: dict | None = None,
    reachable_sparse: dict | None = None,
    collection=None,
    node_targets: dict | None = None,
) -> list[dict] | None:
    """Validate a layer's filter picker and return what the JSON column stores.

    The colour picker's sibling, checked against the same FIELD edges by the same code: a rule
    naming a table nothing reaches, or a column that table does not declare, is a predicate a
    viewer cannot run, and it would sit in the column looking valid until one tried.

    What the shape validators cannot check is the one thing the table knows: whether the column
    is measured or categorical, and so whether bounds or a value set is the rule it admits. That
    is the same split `colorBy` turns on, and it is checked the same way.

    Two entries over one column are **allowed** here, unlike in the colour picker: "small cells"
    and "large cells" are two rules over one measure, which is what a picker is for.
    """
    if filter_bys is None:
        return None
    if reachable is None:
        reachable = reachable_tables(info, system)
    # Resolved once for the whole picker, and only if something in it is sparse -- the same
    # hoist `build_color_bys` makes, so a picker of column rules pays for no walk it does not
    # use. A caller with a differently-rooted walk passes its own in, as with `reachable`.
    if reachable_sparse is None and any(rule.kind == enums.ColorSourceKind.SPARSE for rule in filter_bys):
        reachable_sparse = attribute_plans_logic.field_reachable_sparse_datasets(system, info.context.request.organization)

    entries: list[dict] = []
    for index, filter_by in enumerate(filter_bys):
        if filter_by.kind == enums.ColorSourceKind.GRAPH:
            # The rule twin of `_build_graph_color_by`'s check, against the same vocabulary --
            # so a rule and a colouring over one attribute are checked by one list. The shape
            # rules (bounds only, never `values`) already held on the input model.
            try:
                if collection is None:
                    raise ValueError(
                        "a GRAPH rule tests a per-node value a network collection carries, and this layer kind has no such collection. It is for network layers only"
                    )
                vocabulary = collection.store.attribute_vocabulary()
                if filter_by.attribute not in vocabulary:
                    declared = ", ".join(f"'{name}'" for name in vocabulary)
                    carried = (
                        f"It carries: {declared} -- the set its manifest declares."
                        if vocabulary
                        else "It declares none at all -- it was built before konnektion stored attributes, so rebuild and re-upload it to carry the intrinsic metrics."
                    )
                    raise ValueError(f"Network collection {collection.pk} carries no per-node attribute '{filter_by.attribute}'. {carried}")
            except ValueError as error:
                raise ValueError(f"filterBys[{index}]: {error}") from error
            target = getattr(filter_by, "target", None)
            entries.append(
                entry_model(
                    kind="GRAPH",
                    attribute=filter_by.attribute,
                    target=None if target is None else getattr(target, "value", target),
                    min=filter_by.min,
                    max=filter_by.max,
                    exclude=filter_by.exclude,
                    label=filter_by.label,
                ).model_dump(mode="json")
            )
            continue
        if filter_by.kind == enums.ColorSourceKind.SPARSE:
            # A rule over a slice, checked exactly as a colouring over one is: the same
            # reachability, the same axes, the same layout requirement. Sharing
            # `_resolve_sparse_slice` is what keeps "offered means accepted" true across the
            # two pickers rather than only within each.
            try:
                dataset, positions = _resolve_sparse_slice(
                    info, system, filter_by.dataset, filter_by.at, source=source, reachable=reachable_sparse
                )
            except ValueError as error:
                raise ValueError(f"filterBys[{index}]: {error}") from error
            entries.append(
                entry_model(
                    kind="SPARSE",
                    dataset=str(dataset.pk),
                    at=positions,
                    min=filter_by.min,
                    max=filter_by.max,
                    exclude=filter_by.exclude,
                    label=filter_by.label,
                ).model_dump(mode="json")
            )
            continue
        try:
            table, column, steps = _resolve_joined_column(reachable, filter_by.join_path, filter_by.table, filter_by.column, source=source)

            is_measure = column.role in _MEASURE_ROLES
            if is_measure and filter_by.values is not None:
                raise ValueError(f"Column '{column.name}' is a {column.role} column -- its values are measured, so they are filtered by a `min`/`max` range over them, not by a `values` list naming each one.")
            if not is_measure and (filter_by.min is not None or filter_by.max is not None):
                raise ValueError(f"Column '{column.name}' is a {column.role} column -- its values are categorical, so a bound would impose an order they do not have. Pass `values` instead.")

            # The colour builder's stamp, applied to rules by the same door: a COLUMN rule
            # over a node/edge table hides nodes or segments rather than whole objects, and
            # which is a fact of the table's shape -- stamped, never sent, so a caller-sent
            # `target` on a COLUMN rule is refused rather than trusted.
            first_id = str(filter_by.join_path[0].table) if filter_by.join_path else str(filter_by.table)
            stamped_target = (node_targets or {}).get(first_id)
            sent_target = getattr(filter_by, "target", None)
            if sent_target is not None:
                raise ValueError(
                    "`target` on a COLUMN rule is the server's stamp, derived from the table's shape -- a per-node table hides nodes, a per-edge one segments -- so it is not "
                    "a field a caller chooses. Drop it; a GRAPH rule is where `target` is an aim."
                )
            if stamped_target is not None and steps:
                raise ValueError(
                    f"a per-{stamped_target.lower()} table's rows are addressed by a composite key, and a `joinPath` out of one would carry it into a table declared for a "
                    "single id. Filter by the hop target's own picker entry instead."
                )
        except ValueError as error:
            raise ValueError(f"filterBys[{index}]: {error}") from error

        entries.append(
            entry_model(
                table=str(table.pk),
                column=column.name,
                join_path=steps,
                target=stamped_target,
                min=filter_by.min,
                max=filter_by.max,
                values=filter_by.values,
                exclude=filter_by.exclude,
                label=filter_by.label,
            ).model_dump(mode="json")
        )

    return entries


def build_mesh_filter_bys(info: Info, collection, filter_bys: "list[layer_inputs.MeshFilterByInputModel] | None", *, reachable: dict | None = None) -> list[dict] | None:
    """That builder, rooted on a mesh collection and storing mesh-named entries."""
    return build_filter_bys(
        info,
        mesh_collection_system(collection),
        filter_bys,
        source="this collection",
        entry_model=filter_by_models.MeshFilterByModel,
        reachable=reachable,
    )


def build_network_filter_bys(info: Info, collection, filter_bys, *, walks: tuple[dict, dict, dict] | None = None) -> list[dict] | None:
    """The filter builder, rooted on a network collection and storing network-named entries.

    Shares :func:`network_reachable_walks`'s depth-zero rooting and node-table door with the
    colour builder -- a caller writing both pickers passes one ``walks`` triple so the graph
    is walked once.
    """
    if filter_bys is None:
        return None
    reachable, reachable_sparse, node_targets = walks if walks is not None else network_reachable_walks(info, collection)
    return build_filter_bys(
        info,
        column_options_logic.network_collection_system(collection),
        filter_bys,
        source="this network collection",
        entry_model=filter_by_models.NetworkFilterByModel,
        reachable=reachable,
        reachable_sparse=reachable_sparse,
        collection=collection,
        node_targets=node_targets,
    )


def assert_active_filter_bys(filter_bys: list, active: list[int] | None) -> None:
    """Refuse an `activeFilterBys` that indexes nothing, or the same rule twice.

    A list, not one index, because filters compose: several being on at once is the normal case.
    A repeat is refused rather than collapsed -- applying one rule twice narrows nothing, so a
    caller who sent it meant a different index and should hear about it.

    Takes the entries as whatever the caller holds -- stored dumps on a mesh layer, rehydrated
    models on a label one -- because the only thing asked of them here is how many there are.
    """
    if active is None:
        return
    for position, index in enumerate(active):
        # Same trap as `activeColorBy`, and worse here: this column is JSON, so a negative index
        # is not even caught by the database on the way in. `filterBys[-1]` applies the last rule.
        if index < 0:
            raise ValueError(f"`activeFilterBys[{position}]` is {index}. An index into the picker counts from 0; a negative one would silently apply a rule from the end.")
        if index >= len(filter_bys):
            raise ValueError(
                f"`activeFilterBys[{position}]` is {index}, but this layer publishes {len(filter_bys)} filter(s)"
                + (f", indexed 0..{len(filter_bys) - 1}." if filter_bys else ". Pass `filterBys` as well, or leave `activeFilterBys` empty to draw everything.")
            )
    if len(set(active)) != len(active):
        raise ValueError(f"`activeFilterBys` names the same filter twice ({active}). Applying one rule twice narrows nothing -- each index appears at most once.")


def assert_active_color_by(color_bys: list, active: int | None, *, fallback: str = "draw the flat material color") -> None:
    """Refuse an `activeColorBy` that indexes nothing.

    Checked against the list *being written*, never the stored one: a patch that shortens the
    picker and leaves the index alone is exactly how a layer ends up pointing past its own
    last entry.

    ``fallback`` is the only thing that differs between the two layer kinds, and it is prose:
    what a null index means is the material colour on a mesh and the id hash on a mask.
    """
    if active is None:
        return
    # Refused here rather than left to the column: a negative index is a valid `Int` and a valid
    # Python one -- `colorBys[-1]` quietly draws the *last* entry -- so nothing below this line
    # would ever notice, and the caller would get someone else's colouring instead of an error.
    if active < 0:
        raise ValueError(f"`activeColorBy` is {active}. An index into the picker counts from 0; a negative one would silently select from the end.")
    if not color_bys:
        raise ValueError(f"`activeColorBy` is set, but this layer publishes no colourings to index into. Pass `colorBys` as well, or leave `activeColorBy` null to {fallback}.")
    if active >= len(color_bys):
        raise ValueError(f"`activeColorBy` is {active}, but this layer publishes {len(color_bys)} colouring(s), indexed 0..{len(color_bys) - 1}.")


def build_label_render(info: Info, render: layer_inputs.LabelRenderInputModel | None, lens, *, base: label_models.LabelRenderModel | None = None) -> label_models.LabelRenderModel:
    """Lower a label render input onto a base recipe, validating the axis and the joins.

    Omitted fields keep the base's value, which is what makes ``updateLabelLayer`` a patch
    rather than a replacement: a client toggling `contour` must not silently drop the
    selection it is not sending.

    The two **pickers** are the exception, and deliberately: they are replaced wholesale rather
    than merged, because their order is the display order and there is no key to merge on that
    is not the order itself. That is also what makes a colouring removable at all -- `[]` clears
    the picker, where a patch over a single `colorBy` could never tell an omitted field from an
    explicit null. This is the one place a label layer has both the incoming render and the
    stored one in hand, so it is also where an index left dangling by a shortened picker is
    dropped.
    """
    current = base or label_models.LabelRenderModel()
    if render is None:
        return current

    # What the caller actually NAMED. `LabelRenderInput.to_pydantic` drops the fields it left
    # out, so pydantic's own record of which were set carries the three-way distinction this
    # needs: omitted (keep), explicitly null (clear), or a value. Before this, null was the only
    # spelling for the first two, and "publish a picker but draw none of it" could not be said.
    named = render.model_fields_set

    intensity_axis = render.intensity_axis if "intensity_axis" in named else current.intensity_axis
    intensity_index = current.intensity_index if render.intensity_index is None else render.intensity_index
    if intensity_axis:
        assert_channel_index(lens, intensity_axis, intensity_index)

    # One walk of the FIELD edges for both pickers: they are two questions about the same
    # relation, and the coordinate graph does not need traversing twice to answer them.
    names_a_picker = render.color_bys is not None or render.filter_bys is not None
    system = column_options_logic.lens_source_system(lens) if names_a_picker else None
    reachable = reachable_tables(info, system) if names_a_picker else None

    built_color_bys = build_color_bys(
        info,
        system,
        render.color_bys,
        source="this mask",
        entry_model=color_by_models.LabelColorByModel,
        reachable=reachable,
    )
    color_bys = current.color_bys if built_color_bys is None else [label_models.LabelColorByModel(**entry) for entry in built_color_bys]

    if "active_color_by" not in named:
        # Not named: keep the stored choice. A shorter picker cannot leave the old index
        # dangling, so one that no longer holds it falls back to the hash.
        active_color_by = current.active_color_by
        if built_color_bys is not None and active_color_by is not None and active_color_by >= len(color_bys):
            active_color_by = None
    elif render.active_color_by is None:
        # Named, and null: publish the picker and draw NONE of it -- every id back to its hashed
        # colour. This is the case that had no spelling at all while null also meant "omitted";
        # switching a colouring off was only reachable as a side effect of shortening the list.
        active_color_by = None
    else:
        assert_active_color_by(color_bys, render.active_color_by, fallback="hash each id to a colour")
        active_color_by = render.active_color_by

    built_filter_bys = build_filter_bys(
        info,
        system,
        render.filter_bys,
        source="this mask",
        entry_model=filter_by_models.LabelFilterByModel,
        reachable=reachable,
    )
    filter_bys = current.filter_bys if built_filter_bys is None else [label_models.LabelFilterByModel(**entry) for entry in built_filter_bys]

    if render.active_filter_bys is None:
        # The same fallback the colour picker takes, for the same reason. The indices that
        # survive keep pointing at what they pointed at, because the list is replaced wholesale
        # and never reordered under a caller.
        active_filter_bys = list(current.active_filter_bys)
        if built_filter_bys is not None:
            active_filter_bys = [index for index in active_filter_bys if index < len(filter_bys)]
    else:
        assert_active_filter_bys(filter_bys, render.active_filter_bys)
        active_filter_bys = render.active_filter_bys

    def pick(name: str):
        value = getattr(render, name)
        return getattr(current, name) if value is None else value

    return label_models.LabelRenderModel(
        intensity_axis=intensity_axis,
        intensity_index=intensity_index,
        seed=pick("seed"),
        background=pick("background"),
        opacity=pick("opacity"),
        contour=pick("contour"),
        contour_width=pick("contour_width"),
        selected=pick("selected"),
        selection_color=pick("selection_color"),
        show_unselected=pick("show_unselected"),
        color_bys=color_bys,
        active_color_by=active_color_by,
        filter_bys=filter_bys,
        active_filter_bys=active_filter_bys,
    )


class CreateLabelLayerInputModel(BaseModel):
    lens: str
    scene: str
    render: layer_inputs.LabelRenderInputModel | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(CreateLabelLayerInputModel, description="Create a label layer that renders an instance / segmentation map: an array whose values are discrete object ids")
class CreateLabelLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the label / instance-map data")
    render: layer_inputs.LabelRenderInput | None = strawberry.field(default=None, description="How the ids become color. Omit for the defaults: the pixel value itself is the id, hashed to a color, with 0 transparent")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_label_layer(info: Info, input: CreateLabelLayerInput) -> types.LabelLayer:
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.lens)
    scene = get_for_org(models.Scene, info, id=model.scene)
    assert_renderable(lens)

    label_render = build_label_render(info, model.render, lens)

    graph_logic.assert_placeable_in(scene.world, graph_logic.lens_source_system(lens), destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.LABEL,
        lens=lens,
        scene=scene,
        # NORMAL, never ADDITIVE: adding two objects' colors together makes a third color
        # belonging to neither.
        blending=enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
        label_render=label_render.model_dump(mode="json"),
    )


class UpdateLabelLayerInputModel(BaseModel):
    id: str
    render: layer_inputs.LabelRenderInputModel | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(UpdateLabelLayerInputModel, description="Update a label layer's render settings. Every field is a patch: what is not sent keeps its current value")
class UpdateLabelLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the label layer to update")
    render: layer_inputs.LabelRenderInput | None = strawberry.field(default=None, description="The render settings to change. Fields you omit keep their current value, so toggling `contour` does not drop the selection")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_label_layer(info: Info, input: UpdateLabelLayerInput) -> types.LabelLayer:
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.LABEL, mutation="updateLabelLayer")

    base = label_models.LabelRenderModel(**layer.label_render) if layer.label_render else label_models.LabelRenderModel()
    layer.label_render = build_label_render(info, model.render, layer.lens, base=base).model_dump(mode="json")
    if model.opacity is not None:
        layer.opacity = model.opacity
    if model.visible is not None:
        layer.visible = model.visible
    if model.order is not None:
        layer.order = model.order
    layer.save()
    return layer


class CreateVolumeLayerInputModel(BaseModel):
    lens: str
    scene: str
    mode: enums.ProjectionMode | None = None
    intensity_axis: str | None = None
    intensity_index: int = 0
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    clim_min: float | None = None
    clim_max: float | None = None
    gamma: float | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None

    @field_validator("color")
    @classmethod
    def _color_is_rgba(cls, color: list[int] | None) -> list[int] | None:
        if color is not None:
            assert_rgba(color, field="color", maximum=255)
        return color

    @model_validator(mode="after")
    def _contrast_limits_are_a_range(self) -> "CreateVolumeLayerInputModel":
        assert_contrast_limits(self.clim_min, self.clim_max)
        return self


@prose_errors
@kante.pydantic_input(CreateVolumeLayerInputModel, description="Create a single-channel layer rendered as a 3D volume projection (MIP / attenuated-MIP / volume / isosurface) over its z-axis")
class CreateVolumeLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the volumetric data")
    mode: enums.ProjectionMode | None = strawberry.field(default=None, description="The 3D projection / rendering mode over the z-axis (default 'mip')")
    intensity_axis: str | None = strawberry.field(default=None, description="The channel axis to index. Defaults to the lens' first channel axis; may be null for single-valued data.")
    intensity_index: int | None = strawberry.field(default=None, description="The channel index to render (default 0)")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap to render the intensity through (default 'grey')")
    color: list[int] | None = strawberry.field(default=None, description="A solid RGBA color to tint the channel with, instead of a colormap: four components, each 0..255. Overrides `colormap` where both are given")
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units -- not a normalized fraction")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units -- not a normalized fraction")
    gamma: float | None = strawberry.field(default=None, description="Gamma correction (default 1.0)")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'additive')")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_volume_layer(info: Info, input: CreateVolumeLayerInput) -> types.IntensityLayer:
    model = input.to_pydantic()
    return _create_intensity_layer(info, model, projection_mode=model.mode or enums.ProjectionMode.MIP)


class CreatePhasorLayerInputModel(BaseModel):
    lens: str
    scene: str
    phasor_axis: str | None = None
    intensity_axis: str | None = None
    intensity_index: int = 0
    harmonic: int | None = None
    transfer: layer_inputs.PhasorTransferInputModel | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(CreatePhasorLayerInputModel, description="Create a layer that reduces one axis of a lens to a phasor and colors each pixel by it -- a lifetime overlay over a FLIM cube, or a spectral one over a hyperspectral cube")
class CreatePhasorLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the data")
    phasor_axis: str | None = strawberry.field(default=None, description="The axis the phasor is taken over. Must be a MICROTIME or SPECTRUM axis; defaults to the lens' only such axis")
    intensity_axis: str | None = strawberry.field(default=None, description="The detection-channel axis to index, or null when the cube has none (the common case)")
    intensity_index: int | None = strawberry.field(default=None, description="The detection channel to reduce (default 0)")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic of the transform (default 1)")
    transfer: layer_inputs.PhasorTransferInput | None = strawberry.field(default=None, description="How the phasor becomes the pixel's color: the mode, the value range, the colormap and any phasor-space cursors. Defaults to a plain phase colormap")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal' -- this is an overlay, alpha-composited over the layers beneath it, not additive fluorescence)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_phasor_layer(info: Info, input: CreatePhasorLayerInput) -> types.PhasorLayer:
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.lens)
    assert_renderable(lens)

    phasor_axis = default_phasor_axis(lens, model.phasor_axis)
    if not phasor_axis:
        raise ValueError(f"This lens has no MICROTIME or SPECTRUM axis to take a phasor over ({[spec.name for spec in lens.axis_specs]})")
    assert_phasor_axis(lens, phasor_axis)

    render = layer_models.PhasorRenderModel(
        phasor_axis=phasor_axis,
        intensity_axis=_phasor_intensity_axis(lens, model.intensity_axis, model.intensity_index or 0),
        intensity_index=model.intensity_index or 0,
        harmonic=assert_harmonic(model.harmonic),
        transfer=_build_phasor_transfer(model.transfer),
    )

    # NORMAL, not ADDITIVE: the pixel's color here is a *hue* carrying a lifetime, and summing
    # hues with the layers underneath does not mean anything. An overlay, like a label map.
    return _create_flat_layer(
        info,
        lens_id=model.lens,
        scene_id=model.scene,
        kind=enums.LayerKind.PHASOR,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity,
        visible=model.visible,
        order=model.order,
        phasor_render=render.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Updating the fixed-shape kinds
#
# Patches, in the sense `updateLabelLayer` established: an omitted field keeps its
# current value, so changing a colormap does not silently reset the contrast limits
# somebody spent a minute setting. Each one refuses every other kind through
# `assert_kind`, and the refusals are what keep a row from carrying two recipes.
# ---------------------------------------------------------------------------


def _patch_layer_compositing(layer, model) -> None:
    """Apply the compositing fields every update mutation accepts. Omitted means unchanged."""
    if getattr(model, "name", None) is not None:
        layer.name = model.name
    if getattr(model, "blending", None) is not None:
        layer.blending = model.blending
    if model.opacity is not None:
        layer.opacity = model.opacity
    if model.visible is not None:
        layer.visible = model.visible
    if model.order is not None:
        layer.order = model.order


class UpdateIntensityLayerInputModel(BaseModel):
    id: str
    name: str | None = None
    intensity_axis: str | None = None
    intensity_index: int | None = None
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    clim_min: float | None = None
    clim_max: float | None = None
    gamma: float | None = None
    projection_mode: enums.ProjectionMode | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None

    @field_validator("color")
    @classmethod
    def _color_is_rgba(cls, color: list[int] | None) -> list[int] | None:
        if color is not None:
            assert_rgba(color, field="color", maximum=255)
        return color


@prose_errors
@kante.pydantic_input(UpdateIntensityLayerInputModel, description="Update an intensity layer's render settings. Every field is a patch: what is not sent keeps its current value")
class UpdateIntensityLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the intensity layer to update")
    name: str | None = strawberry.field(default=None, description="A human-readable name for the layer, e.g. the channel it draws")
    intensity_axis: str | None = strawberry.field(default=None, description="The channel axis to index")
    intensity_index: int | None = strawberry.field(default=None, description="The channel index to render")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap to render the intensity through")
    color: list[int] | None = strawberry.field(default=None, description="A solid RGBA color to tint the channel with, overriding the colormap: four components, each 0..255. Omitting this keeps the current tint -- there is no spelling here for 'go back to the colormap', because null already means 'unchanged'")
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units -- not a normalized fraction")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units -- not a normalized fraction")
    gamma: float | None = strawberry.field(default=None, description="Gamma correction applied to the normalized intensities")
    projection_mode: enums.ProjectionMode | None = strawberry.field(default=None, description="How z is collapsed. Omitting this keeps the current mode -- there is no spelling here for 'stop projecting', because null already means 'unchanged'; recreate the layer to go back to drawing the plane")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_intensity_layer(info: Info, input: UpdateIntensityLayerInput) -> types.IntensityLayer:
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.INTENSITY, mutation="updateIntensityLayer")

    # Resolved against what the row will hold *after* the patch, not before: sending an axis
    # and an index together must be checked as the pair it is, or a valid move from a
    # 3-channel axis to a 30-channel one is refused on the old axis' size.
    intensity_axis = model.intensity_axis if model.intensity_axis is not None else layer.intensity_axis
    intensity_index = model.intensity_index if model.intensity_index is not None else (layer.intensity_index or 0)
    if intensity_axis:
        assert_channel_index(layer.lens, intensity_axis, intensity_index)

    clim_min = model.clim_min if model.clim_min is not None else layer.clim_min
    clim_max = model.clim_max if model.clim_max is not None else layer.clim_max
    assert_contrast_limits(clim_min, clim_max)

    layer.intensity_axis = intensity_axis
    layer.intensity_index = intensity_index
    layer.clim_min = clim_min
    layer.clim_max = clim_max
    if model.colormap is not None:
        layer.colormap = model.colormap
    if model.color is not None:
        layer.color = model.color
    if model.gamma is not None:
        layer.gamma = model.gamma
    if model.projection_mode is not None:
        layer.projection_mode = model.projection_mode
    _patch_layer_compositing(layer, model)
    layer.save()
    return layer


class UpdateRgbLayerInputModel(BaseModel):
    id: str
    name: str | None = None
    intensity_axis: str | None = None
    red_index: int | None = None
    green_index: int | None = None
    blue_index: int | None = None
    clim_min: float | None = None
    clim_max: float | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(UpdateRgbLayerInputModel, description="Update an RGB layer's render settings. Every field is a patch: what is not sent keeps its current value")
class UpdateRgbLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the RGB layer to update")
    name: str | None = strawberry.field(default=None, description="A human-readable name for the layer")
    intensity_axis: str | None = strawberry.field(default=None, description="The channel axis the three components are indexed on")
    red_index: int | None = strawberry.field(default=None, description="Channel index mapped to red")
    green_index: int | None = strawberry.field(default=None, description="Channel index mapped to green")
    blue_index: int | None = strawberry.field(default=None, description="Channel index mapped to blue")
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units, applied to all three channels")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units, applied to all three channels")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_rgb_layer(info: Info, input: UpdateRgbLayerInput) -> types.RgbLayer:
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.RGB, mutation="updateRgbLayer")

    intensity_axis = model.intensity_axis if model.intensity_axis is not None else layer.intensity_axis
    indices = {
        "red_index": model.red_index if model.red_index is not None else layer.red_index,
        "green_index": model.green_index if model.green_index is not None else layer.green_index,
        "blue_index": model.blue_index if model.blue_index is not None else layer.blue_index,
    }
    if intensity_axis:
        assert_channel_axis(layer.lens, intensity_axis)
        for field, index in indices.items():
            if index is not None:
                assert_intensity_index(layer.lens, intensity_axis, index, field=field)

    clim_min = model.clim_min if model.clim_min is not None else layer.clim_min
    clim_max = model.clim_max if model.clim_max is not None else layer.clim_max
    assert_contrast_limits(clim_min, clim_max)

    layer.intensity_axis = intensity_axis
    for field, index in indices.items():
        setattr(layer, field, index)
    layer.clim_min = clim_min
    layer.clim_max = clim_max
    _patch_layer_compositing(layer, model)
    layer.save()
    return layer


class UpdatePhasorLayerInputModel(BaseModel):
    id: str
    name: str | None = None
    phasor_axis: str | None = None
    intensity_axis: str | None = None
    intensity_index: int | None = None
    harmonic: int | None = None
    transfer: layer_inputs.PhasorTransferInputModel | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(UpdatePhasorLayerInputModel, description="Update a phasor layer's render settings. Every field is a patch: what is not sent keeps its current value, except `transfer`, which replaces the whole transfer when given")
class UpdatePhasorLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the phasor layer to update")
    name: str | None = strawberry.field(default=None, description="A human-readable name for the layer")
    phasor_axis: str | None = strawberry.field(default=None, description="The axis the phasor is taken over. Must be a MICROTIME or SPECTRUM axis")
    intensity_axis: str | None = strawberry.field(default=None, description="The detection-channel axis to index")
    intensity_index: int | None = strawberry.field(default=None, description="The detection channel to reduce")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic of the transform")
    transfer: layer_inputs.PhasorTransferInput | None = strawberry.field(default=None, description="How the phasor becomes the pixel's color. Given, it replaces the whole transfer -- the cursors included, which is what makes 'remove a cursor' expressible at all")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_phasor_layer(info: Info, input: UpdatePhasorLayerInput) -> types.PhasorLayer:
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.PHASOR, mutation="updatePhasorLayer")

    current = layer_models.PhasorRenderModel(**layer.phasor_render) if layer.phasor_render else None
    if current is None:
        raise ValueError(f"Layer {model.id} is a phasor layer with no phasor render settings, which should not be possible -- it cannot be patched, only recreated.")

    phasor_axis = model.phasor_axis if model.phasor_axis is not None else current.phasor_axis
    assert_phasor_axis(layer.lens, phasor_axis)

    intensity_index = model.intensity_index if model.intensity_index is not None else current.intensity_index
    intensity_axis = model.intensity_axis if model.intensity_axis is not None else current.intensity_axis

    layer.phasor_render = layer_models.PhasorRenderModel(
        phasor_axis=phasor_axis,
        intensity_axis=_phasor_intensity_axis(layer.lens, intensity_axis, intensity_index),
        intensity_index=intensity_index,
        harmonic=assert_harmonic(model.harmonic) if model.harmonic is not None else current.harmonic,
        # Replaced wholesale rather than merged, for the reason a label layer's pickers are:
        # `cursors` is a list, and a merge has no spelling for removing an entry from one.
        transfer=_build_phasor_transfer(model.transfer) if model.transfer is not None else current.transfer,
    ).model_dump(mode="json")
    _patch_layer_compositing(layer, model)
    layer.save()
    return layer


class DeleteLayerInputModel(BaseModel):
    id: str = Field(description="The ID of the layer to delete")


@kante.pydantic_input(DeleteLayerInputModel, description="Input for deleting a layer by ID")
class DeleteLayerInput:
    """Input for deleting a layer by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the layer to delete")


delete_layer = make_delete(models.Layer, DeleteLayerInput)

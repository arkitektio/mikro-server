"""Mutations for network layers.

The graph twin of :mod:`core.mutations.mesh_layer`.

**What it shares:** a layer is view state over a collection that owns its own coordinate system,
so placement is the collection's `derivedFrom` walk and nothing here states geometry. `maxLevel`
means exactly what it means for a mesh -- a budget, not a choice of level. The two pickers are
built by the same `build_color_bys`/`build_filter_bys` every other picker-bearing kind uses.

**What only this kind has:** a GRAPH picker entry -- a per-node value the collection itself
carries (Strahler order, degree, depth, component, a stored radius, a writer's own column),
validated against the collection's manifest rather than against a table -- and depth-zero-rooted
reachability walks (`network_reachable_walks`), because the unrestricted fact walk would offer
tables keyed by the source image's mask-instance ids, joins an object id cannot execute.
"""

from kante.types import Info
import strawberry

from core import types, models, enums
import kante
from pydantic import BaseModel
from core.logic import graph as graph_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.mutations.layer import (
    assert_active_color_by,
    assert_active_filter_bys,
    build_network_color_bys,
    build_network_filter_bys,
    network_reachable_walks,
)
from core.render.layer import inputs as layer_inputs
from core.scoping import get_for_org

_LINE_WIDTH_DESCRIPTION = (
    "The flat width of every segment, in scene units -- the world's spatial-axis unit, which is a well-defined length for a layer only when its `placementInvariance` is SIMILARITY or better "
    "(RFC-8). The fallback: `nodeSizeColumn` and `edgeWidthColumn` override it where they are set"
)

_NODE_SIZE_DESCRIPTION = (
    "The per-node column giving each node's radius, so a segment **tapers** between its endpoints. This is the shape traced morphology actually has -- a dendrite's calibre is a measurement per "
    "node, not per branch -- and it is why this wins over `edgeWidthColumn` when both are given: a per-node profile is strictly the more specific statement"
)

_EDGE_WIDTH_DESCRIPTION = (
    "The per-edge column giving each segment one uniform width -- a connectome's weight, a vessel segment's calibre where the tracer recorded one per segment rather than per node. Ignored when "
    "`nodeSizeColumn` is set"
)

_DIRECTED_DESCRIPTION = (
    "Whether to draw each edge's direction, e.g. as arrowheads. **A render setting, never a fact about the graph**: an edge is always stored source-to-target, so the direction is in the data "
    "whether or not it is drawn. A traced arbor is directed away from the soma and usually drawn without arrows; a connectome usually with them"
)

_MAX_LEVEL_DESCRIPTION = (
    "The deepest octree level this layer may load, capping detail against the collection's declared `grid.levels`. A budget, not a choice of level: which level a viewer fetches still follows from "
    "the zoom. Null lets the viewer decide. Commonly moot here -- a traced arbor is usually a single-level collection, because konnektion picks its depth from the data"
)

_COLOR_BYS_DESCRIPTION = (
    "The colourings this layer offers, in the order a picker should show them, instead of the flat `materialColor`. Three entry kinds: a COLUMN entry colours whole objects by a table reachable "
    "from this collection by a FIELD edge (author it with `createTableDataset(keyedBy: {kind: NETWORK_COLLECTION})` -- the table's rows are keyed by **object** ids, one per filament or arbor); "
    "a SPARSE entry colours objects by one slice of a matrix those ids index; and a GRAPH entry -- this kind's own -- colours per **node**, by a value the collection itself carries: Strahler "
    "order, degree, depth, component, a stored radius, or a writer's own column, exactly the set the collection's manifest declares and `networkColorByOptions` offers. Which entry is drawn is "
    "`activeColorBy`; publishing a picker is not the same as choosing within it"
)

_ACTIVE_DESCRIPTION = "Which entry of `colorBys` is drawn, as an index into it. Null draws the flat `materialColor` -- what having no colouring has always meant"

_FILTER_BYS_DESCRIPTION = (
    "The filters this layer offers, in the order a picker should show them. A COLUMN or SPARSE rule keeps or drops whole objects; a GRAPH rule -- always `min`/`max` bounds over a per-node value "
    "the collection carries -- hides individual nodes and segments, which is what 'trunk only' (Strahler order at least 3) means on an arbor. Which of them are actually applied is `activeFilterBys`"
)

_ACTIVE_FILTERS_DESCRIPTION = (
    "Which entries of `filterBys` are applied, as indices into it. Several at once is the normal case -- they combine with AND, and something is drawn when every active rule keeps it. Empty applies "
    "none of them, so everything draws"
)


def assert_max_level(collection, max_level: int | None) -> None:
    """Refuse an LOD cap the collection has no such level for.

    A cap past the last level is not a harmless over-estimate: it is a claim about a store, and
    the store is the only thing that knows how many levels it has. The grid was read off the
    manifest at registration, so this compares against what the writer wrote.
    """
    if max_level is None:
        return
    levels = int((collection.grid or {}).get("levels", 0) or 0)
    if max_level < 0:
        raise ValueError(f"`maxLevel` is an octree level, so it is not negative, got {max_level}.")
    if levels and max_level >= levels:
        raise ValueError(
            f"`maxLevel` is {max_level} but this collection's manifest declares {levels} level(s), numbered 0..{levels - 1}. A cap past the last level is a claim about a store that the store "
            f"does not support -- and since konnektion picks its depth from the data, a single-level collection is the common case rather than a small one."
        )


class CreateNetworkLayerInputModel(BaseModel):
    scene: str
    network_collection: str
    material_color: list[int] | None = None
    line_width: float | None = None
    node_size_column: str | None = None
    edge_width_column: str | None = None
    directed: bool | None = None
    show_nodes: bool | None = None
    max_level: int | None = None
    color_bys: list[layer_inputs.NetworkColorByInputModel] | None = None
    active_color_by: int | None = None
    filter_bys: list[layer_inputs.NetworkFilterByInputModel] | None = None
    active_filter_bys: list[int] | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(
    CreateNetworkLayerInputModel,
    description="Input for drawing a network collection -- a traced arbor, a vessel tree, a connectome -- in a scene. Its segments are drawn as camera-facing quads rather than GL lines, which is what makes a width in scene units meaningful at all",
)
class CreateNetworkLayerInput:
    """Input for creating a network layer."""

    scene: strawberry.ID = strawberry.field(description="The scene this layer is drawn in")
    network_collection: strawberry.ID = strawberry.field(description="The network collection to render. It owns its own coordinate system, and `derivedFrom` is what relates that system to the scene's world")
    material_color: list[int] | None = strawberry.field(default=None, description="The flat colour of every segment, as RGBA. Default opaque white")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH_DESCRIPTION)
    node_size_column: str | None = strawberry.field(default=None, description=_NODE_SIZE_DESCRIPTION)
    edge_width_column: str | None = strawberry.field(default=None, description=_EDGE_WIDTH_DESCRIPTION)
    directed: bool | None = strawberry.field(default=None, description=_DIRECTED_DESCRIPTION)
    show_nodes: bool | None = strawberry.field(default=None, description="Whether to draw a glyph at each node as well as the segments. Useful on a sparse graph, noise on a dense arbor")
    max_level: int | None = strawberry.field(default=None, description=_MAX_LEVEL_DESCRIPTION)
    color_bys: list[layer_inputs.NetworkColorByInput] | None = strawberry.field(default=None, description=_COLOR_BYS_DESCRIPTION)
    active_color_by: int | None = strawberry.field(default=None, description=_ACTIVE_DESCRIPTION)
    filter_bys: list[layer_inputs.NetworkFilterByInput] | None = strawberry.field(default=None, description=_FILTER_BYS_DESCRIPTION)
    active_filter_bys: list[int] | None = strawberry.field(default=None, description=_ACTIVE_FILTERS_DESCRIPTION)
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_network_layer(info: Info, input: CreateNetworkLayerInput) -> types.NetworkLayer:
    """Draw a network collection in a scene.

    Placement is the collection's, not this layer's: it owns a coordinate system and
    `derivedFrom` relates that system to whatever the network was traced in. A collection whose
    space has no route to the scene's world is refused here rather than registering as a layer
    that never draws.
    """
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    collection = get_for_org(models.NetworkCollection, info, id=model.network_collection)

    graph_logic.assert_placeable_in(scene.world, getattr(collection, "coordinate_system", None), destination=f"the world of scene '{scene.name}'")
    assert_max_level(collection, model.max_level)

    # One pair of depth-zero walks for both pickers -- two questions about the same relation,
    # and the graph does not need walking twice to answer them.
    walks = network_reachable_walks(info, collection) if (model.color_bys is not None or model.filter_bys is not None) else None
    color_bys = build_network_color_bys(info, collection, model.color_bys, walks=walks) or []
    assert_active_color_by(color_bys, model.active_color_by)
    filter_bys = build_network_filter_bys(info, collection, model.filter_bys, walks=walks) or []
    assert_active_filter_bys(filter_bys, model.active_filter_bys)

    return models.Layer.objects.create(
        kind=enums.LayerKind.NETWORK,
        scene=scene,
        network_collection=collection,
        material_color=model.material_color if model.material_color is not None else [255, 255, 255, 255],
        line_width=model.line_width,
        node_size_column=model.node_size_column,
        edge_width_column=model.edge_width_column,
        directed=model.directed if model.directed is not None else False,
        show_nodes=model.show_nodes if model.show_nodes is not None else False,
        max_level=model.max_level,
        network_color_bys=color_bys,
        active_color_by=model.active_color_by,
        network_filter_bys=filter_bys,
        active_filter_bys=model.active_filter_bys or [],
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )


class UpdateNetworkLayerInputModel(BaseModel):
    id: str
    material_color: list[int] | None = None
    line_width: float | None = None
    node_size_column: str | None = None
    edge_width_column: str | None = None
    directed: bool | None = None
    show_nodes: bool | None = None
    max_level: int | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@strawberry.input(
    description="Retune how a network layer is drawn. A patch: an OMITTED field keeps its current value. The collection and the scene are not editable -- a layer renders what it was created to render",
)
class UpdateNetworkLayerInput:
    """Input for updating a network layer's render settings."""

    id: strawberry.ID = strawberry.field(description="The ID of the network layer to update")
    material_color: list[int] | None = strawberry.field(default=None, description="The flat colour of every segment, as RGBA")
    line_width: float | None = strawberry.field(default=strawberry.UNSET, description=f"{_LINE_WIDTH_DESCRIPTION}. An explicit `null` clears it")
    node_size_column: str | None = strawberry.field(default=strawberry.UNSET, description=f"{_NODE_SIZE_DESCRIPTION}. An explicit `null` clears it, falling back to `edgeWidthColumn` and then to `lineWidth`")
    edge_width_column: str | None = strawberry.field(default=strawberry.UNSET, description=f"{_EDGE_WIDTH_DESCRIPTION}. An explicit `null` clears it")
    directed: bool | None = strawberry.field(default=None, description=_DIRECTED_DESCRIPTION)
    show_nodes: bool | None = strawberry.field(default=None, description="Whether to draw a glyph at each node as well as the segments")
    max_level: int | None = strawberry.field(default=strawberry.UNSET, description=f"{_MAX_LEVEL_DESCRIPTION}. An omitted field keeps the cap, an explicit `null` removes it")
    color_bys: list[layer_inputs.NetworkColorByInput] | None = strawberry.field(
        default=None,
        description=f"{_COLOR_BYS_DESCRIPTION}. Replaces the published picker wholesale: its order is the display order, so there is nothing to merge on. Pass `[]` to remove every colouring and fall back to `materialColor`",
    )
    active_color_by: int | None = strawberry.field(
        default=strawberry.UNSET,
        description=f"{_ACTIVE_DESCRIPTION}. Pass `null` to publish the picker and draw none of it; omit to leave the choice alone. Re-checked against the picker being written, never the stored one. If a new `colorBys` no longer holds the entry that was active, the layer falls back to `materialColor` -- name `activeColorBy` in the same call to point at another entry instead",
    )
    filter_bys: list[layer_inputs.NetworkFilterByInput] | None = strawberry.field(
        default=None,
        description=f"{_FILTER_BYS_DESCRIPTION}. Replaces the published filters wholesale, as `colorBys` does. Pass `[]` to remove every rule and draw everything",
    )
    active_filter_bys: list[int] | None = strawberry.field(
        default=None,
        description=f"{_ACTIVE_FILTERS_DESCRIPTION}. Re-checked against the filters being written: a new `filterBys` that no longer holds an applied rule drops it from this set rather than leaving it dangling",
    )
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha, from 0 to 1")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_network_layer(info: Info, input: UpdateNetworkLayerInput) -> types.NetworkLayer:
    """Patch a network layer's render settings, leaving omitted fields alone."""
    layer = get_for_org(models.Layer, info, id=input.id)
    if layer.kind != enums.LayerKindChoices.NETWORK.value:
        raise ValueError(f"Layer {layer.pk} is a {layer.kind} layer, not a network layer.")

    if input.material_color is not None:
        layer.material_color = input.material_color
    if input.line_width is not strawberry.UNSET:
        layer.line_width = input.line_width
    if input.node_size_column is not strawberry.UNSET:
        layer.node_size_column = input.node_size_column
    if input.edge_width_column is not strawberry.UNSET:
        layer.edge_width_column = input.edge_width_column
    if input.directed is not None:
        layer.directed = input.directed
    if input.show_nodes is not None:
        layer.show_nodes = input.show_nodes
    if input.max_level is not strawberry.UNSET:
        assert_max_level(layer.network_collection, input.max_level)
        layer.max_level = input.max_level

    # The pickers, with `updateMeshLayer`'s exact patch semantics: replaced wholesale when
    # named, and an index left dangling by a shortened picker is dropped rather than kept.
    color_bys_input = None if input.color_bys is None else [entry.to_pydantic() for entry in input.color_bys]
    filter_bys_input = None if input.filter_bys is None else [entry.to_pydantic() for entry in input.filter_bys]
    walks = network_reachable_walks(info, layer.network_collection) if (color_bys_input is not None or filter_bys_input is not None) else None

    color_bys = build_network_color_bys(info, layer.network_collection, color_bys_input, walks=walks)
    if color_bys is not None:
        layer.network_color_bys = color_bys
        if input.active_color_by is strawberry.UNSET and layer.active_color_by is not None and layer.active_color_by >= len(color_bys):
            layer.active_color_by = None
    if input.active_color_by is not strawberry.UNSET:
        if input.active_color_by is None:
            # Named, and null: publish the picker and draw none of it.
            layer.active_color_by = None
        else:
            assert_active_color_by(layer.network_color_bys or [], input.active_color_by)
            layer.active_color_by = input.active_color_by

    filter_bys = build_network_filter_bys(info, layer.network_collection, filter_bys_input, walks=walks)
    if filter_bys is not None:
        layer.network_filter_bys = filter_bys
        if input.active_filter_bys is None:
            layer.active_filter_bys = [index for index in (layer.active_filter_bys or []) if index < len(filter_bys)]
    if input.active_filter_bys is not None:
        assert_active_filter_bys(layer.network_filter_bys or [], input.active_filter_bys)
        layer.active_filter_bys = input.active_filter_bys

    if input.blending is not None:
        layer.blending = input.blending
    if input.opacity is not None:
        layer.opacity = input.opacity
    if input.visible is not None:
        layer.visible = input.visible
    if input.order is not None:
        layer.order = input.order

    layer.save()
    return layer

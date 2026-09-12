"""GraphQL output types for the in-layer render graph.

Mirrors the interface + ``pydantic.type`` pattern used by
``lightpath/objects/types.py``: a common ``LayerRenderNode`` interface with two
concrete implementations (``ChannelSourceNode`` and ``BlendNode``). Because the
interface is declared before ``BlendNode``, the recursive ``children`` field can
reference it directly without a lazy forward reference. Returning the raw
pydantic models from a resolver is enough — strawberry resolves each node to its
concrete type via the pydantic model it is bound to.
"""

import strawberry
from strawberry.experimental import pydantic

from kanne_server import scalars as kanne_scalars

from core import enums
from core.render import color_by as color_by_models
from core.render import filter_by as filter_by_models
from core.render import joins
from core.render.layer import label as label_models
from core.render.layer import models


@pydantic.type(color_by_models.AxisPositionModel, description="One position along one named axis: which slice of a matrix a colouring reads")
class AxisPosition:
    axis: str = strawberry.field(description="The name of the axis, as the dataset declares it")
    value: int = strawberry.field(description="The position along it. A row of the table that axis references")


@pydantic.type(
    models.LookupStopModel,
    description="One control point of an intensity transfer curve: a raw intensity, and the normalized value it maps to. The two sides are on different scales -- `position` in the data's units, `value` in the 0..1 the colormap is indexed with",
)
class LookupStop:
    position: float = strawberry.field(description="The intensity this stop sits at, in the data's own intensity units -- the same scale as `climMin`/`climMax`, not a normalized fraction")
    value: float = strawberry.field(description="The normalized value that intensity maps to, from 0 to 1. This is what the colormap is indexed with")


@pydantic.type(models.TransferFunctionModel, description="How a single channel's intensities are mapped to color before compositing")
class TransferFunction:
    clim_min: float | None = strawberry.field(default=None, description="Lower contrast limit, in the data's own intensity units -- not a normalized fraction")
    clim_max: float | None = strawberry.field(default=None, description="Upper contrast limit, in the data's own intensity units -- not a normalized fraction")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap (transfer function LUT) applied to the channel")
    color: list[int] | None = strawberry.field(default=None, description="A solid RGBA color to tint the channel with, instead of a colormap")
    gamma: float | None = strawberry.field(default=None, description="Gamma correction applied to the normalized intensities. Not applied when `stops` gives an explicit curve")
    opacity: float | None = strawberry.field(default=None, description="Per-channel opacity within the layer (0..1)")
    invert: bool | None = strawberry.field(default=None, description="Whether the contrast mapping is inverted")
    stops: list[LookupStop] | None = strawberry.field(default=None, description="An explicit intensity transfer curve, ordered by position. When present, the curve is the transfer and supersedes `gamma` and the `climMin`/`climMax` window -- which are the one-parameter and two-point special cases of it. Null means there is no curve, and those apply as they always did")


_JOIN_PATH_DESCRIPTION = (
    "How this column is reached when it lives further than one table away: the chain of `references` hops from the table the source's ids land in to the table `table` names. Empty -- the common "
    "case -- means the ids key that table directly. The renderer performs one lookup per hop"
)


@strawberry.interface(description="A node in a layer's internal render graph")
class LayerRenderNode:
    kind: str = strawberry.field(description="The discriminator of the node: 'channel', 'phasor', 'blend' or 'projection'")
    label: str | None = strawberry.field(default=None, description="An optional human-readable label for the node")


@pydantic.type(models.ChannelSourceModel, description="A single intensity channel of the layer's lens, with its own transfer function")
class ChannelSourceNode(LayerRenderNode):
    kind: str = strawberry.field(description="Always 'channel'")
    intensity_axis: str | None = strawberry.field(default=None, description="The lens axis carrying the intensity channels, or null when the pixel value itself is the intensity (e.g. a single-valued volume or label map)")
    intensity_index: int = strawberry.field(description="The index along the intensity axis to render")
    visible: bool = strawberry.field(description="Whether this channel participates in the layer's composite")
    transfer: TransferFunction = strawberry.field(description="The transfer function mapping this channel to color")


@pydantic.type(models.BlendNodeModel, description="Composites its children using an in-layer blend mode")
class BlendNode(LayerRenderNode):
    kind: str = strawberry.field(description="Always 'blend'")
    blending: enums.Blending = strawberry.field(description="The blend mode used to composite the children")
    children: list[LayerRenderNode] = strawberry.field(description="The child nodes composited by this node")


@pydantic.type(models.ProjectionNodeModel, description="Projects the composite of its children through the z-axis using a 3D rendering mode")
class ProjectionNode(LayerRenderNode):
    kind: str = strawberry.field(description="Always 'projection'")
    mode: enums.ProjectionMode = strawberry.field(description="The 3D projection / rendering mode applied over the z-axis")
    children: list[LayerRenderNode] = strawberry.field(description="The child nodes whose composite is projected")


@pydantic.type(models.PhasorCursorModel, description="A region of phasor space, and the color the pixels falling inside it are painted")
class PhasorCursor:
    kind: enums.PhasorCursorKind = strawberry.field(description="The shape of the region")
    g: float | None = strawberry.field(default=None, description="(circle) The g coordinate of the centre")
    s: float | None = strawberry.field(default=None, description="(circle) The s coordinate of the centre")
    radius: float | None = strawberry.field(default=None, description="(circle) The radius of the disc, in phasor units")
    points: list[list[float]] | None = strawberry.field(default=None, description="(polygon) The (g, s) vertices of the region, at least three")
    color: list[int] | None = strawberry.field(default=None, description="The RGBA color the pixels inside this region take, overriding the colormap")
    label: str | None = strawberry.field(default=None, description="An optional human-readable label, e.g. the species this region selects")
    visible: bool = strawberry.field(description="Whether this cursor colors the image")


@pydantic.type(models.PhasorTransferModel, description="How a phasor becomes the pixel's color. The transfer function of a phasor source: it maps the reduction's output -- a (g, s) pair plus a photon count -- rather than a sampled scalar, which is why it is not a TransferFunction")
class PhasorTransfer:
    mode: enums.PhasorColorMode = strawberry.field(description="What the hue is derived from: the phasor's phase, its modulus, or the mean of both")
    min: kanne_scalars.GenericQuantity | None = strawberry.field(default=None, description="The lower bound of the derived value, in its own dimension: a duration ('0.5 ns') over a microtime axis, a wavelength ('480 nm') over a spectrum axis")
    max: kanne_scalars.GenericQuantity | None = strawberry.field(default=None, description="The upper bound of the derived value, in its own dimension")
    colormap: enums.ColorMap = strawberry.field(description="The colormap the derived value is mapped through")
    weight_by_intensity: bool = strawberry.field(description="Whether the photon count modulates the brightness, so that hue carries the phasor and brightness the signal")
    intensity: TransferFunction = strawberry.field(description="The transfer function applied to that photon count: contrast limits, gamma, opacity")
    cursors: list[PhasorCursor] = strawberry.field(description="Regions of phasor space whose pixels take a fixed color, overriding the colormap")


@pydantic.type(
    models.PhasorNodeModel,
    description="Reduces one axis of the lens to a phasor -- the DFT of each pixel's profile along it, at a harmonic -- and colors the pixel by the result. Over a microtime axis the phase reads as a fluorescence lifetime; over a spectrum axis, as a spectral centre of mass. Its output is a raster that composites into the scene like any other leaf, not a scatter plot",
)
class PhasorNode(LayerRenderNode):
    kind: str = strawberry.field(description="Always 'phasor'")
    visible: bool = strawberry.field(description="Whether this node participates in the layer's composite")
    phasor_axis: str = strawberry.field(description="The lens axis the phasor is taken over. Must be a MICROTIME or SPECTRUM axis -- the continuous ones a DFT means anything over")
    intensity_axis: str | None = strawberry.field(default=None, description="The lens axis carrying the detection channels, or null when the cube has none")
    intensity_index: int = strawberry.field(description="The index along the intensity axis to reduce")
    harmonic: int = strawberry.field(description="The harmonic of the transform. 1 is the fundamental; 2 resolves multi-exponential decays a first harmonic cannot separate")
    transfer: PhasorTransfer = strawberry.field(description="How the resulting phasor becomes the pixel's color")


@pydantic.type(
    models.PhasorRenderModel,
    description="How a phasor layer draws: which axis is reduced to a phasor, at which harmonic, and how the resulting (g, s) becomes color. The whole recipe of a PHASOR layer, as `labelRender` is the whole recipe of a LABEL one -- for a phasor composited *with* other channels, an IMAGE layer's graph carries a `PhasorNode` instead",
)
class PhasorRender:
    phasor_axis: str = strawberry.field(description="The lens axis the phasor is taken over. A MICROTIME or SPECTRUM axis -- the continuous ones a DFT means anything over")
    intensity_axis: str | None = strawberry.field(default=None, description="The lens axis carrying the detection channels, or null when the cube has none")
    intensity_index: int = strawberry.field(description="The index along the intensity axis to reduce")
    harmonic: int = strawberry.field(description="The harmonic of the transform. 1 is the fundamental; 2 resolves multi-exponential decays a first harmonic cannot separate")
    transfer: PhasorTransfer = strawberry.field(description="How the resulting phasor becomes the pixel's color")


@pydantic.type(models.LayerRenderGraphModel, description="The composable render recipe inside a single layer, rooted at a blend node")
class LayerRenderGraph:
    root: BlendNode = strawberry.field(description="The root blend node of the layer's render graph")


@pydantic.type(
    joins.JoinStepModel,
    description="One hop of a join path: the column whose values identify rows of the next table. The target is not named here -- the next step names it, and which of its columns holds row identity is already declared on it",
)
class JoinStep:
    table: strawberry.ID = strawberry.field(description="The table this hop stands in")
    column: str = strawberry.field(description="A column of that table whose `references` identifies rows of the next table")


@pydantic.type(
    label_models.LabelColorByModel,
    description="One entry of a label layer's colour picker: colour the mask's objects by a column of the table its FIELD edge keys into, instead of by hashing their id. The table is named, never the join: which of its columns holds row identity is already declared there, and the edge that makes the lookup possible is already in the coordinate graph",
)
class LabelColorBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source the value is read from. COLUMN for a column of a table, SPARSE for one slice of a matrix -- and the fields of the other member are null")
    table: strawberry.ID | None = strawberry.field(default=None, description="The table dataset holding one row per object. Must be reachable from the layer's lens by a FIELD edge -- the same edge `attributePlans` discovers")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value colors each object")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The sparse dataset one slice of which colors the objects")
    at: list["AxisPosition"] = strawberry.field(default_factory=list, description="(SPARSE) Which slice is read: a position along each axis the source's ids do not index")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap the column's value is mapped through. Which *sort* follows from the column's role, never from a choice here: a measure column (COORDINATE, ATTRIBUTE) takes a continuous one over its range with an optional `min`/`max` window, a categorical one (ID, TRACK_ID, LABEL, COLOR) takes a qualitative one -- a colour per distinct value, no order implied and no window to set")
    min: float | None = strawberry.field(default=None, description="The value mapped to the bottom of the colormap, in the column's own declared `unit`. Null leaves the viewer to stretch the map from the smallest value it reads")
    max: float | None = strawberry.field(default=None, description="The value mapped to the top of the colormap, in the column's own declared `unit`. Null leaves the viewer to stretch the map to the largest value it reads")
    label: str | None = strawberry.field(default=None, description="What to call this colouring in a picker. A caption only: two entries that render identically are refused however they are labelled")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    filter_by_models.LabelFilterByModel,
    description="One entry of a label layer's filter picker: draw only the objects whose row in the keyed table satisfies this rule. The sibling of `LabelColorBy` over the same FIELD edge -- same table, same column check -- deciding whether an object is drawn rather than what colour it takes",
)
class LabelFilterBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source this rule tests: a column of a table the ids key into, or one slice of a sparse matrix they index")
    table: strawberry.ID | None = strawberry.field(default=None, description="(COLUMN) The table dataset holding one row per object. Reachable from the layer's lens by a FIELD edge -- the edge `createTableDataset(keyedBy:)` authors and `attributePlans` discovers")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value decides whether an object is drawn")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The matrix one slice of which is tested, instead of a table column")
    at: list[AxisPosition] = strawberry.field(default_factory=list, description="(SPARSE) The position along each axis the matrix identifies itself by -- one slice, which is a value per object")
    min: float | None = strawberry.field(default=None, description="Lower bound, inclusive, in the column's own declared `unit`. Applies to a measure column (role COORDINATE or ATTRIBUTE). Null is an open lower end")
    max: float | None = strawberry.field(default=None, description="Upper bound, inclusive, in the column's own declared `unit`. Applies to a measure column (role COORDINATE or ATTRIBUTE). Null is an open upper end")
    values: list[str] | None = strawberry.field(default=None, description="The values that match, as strings. Applies to a categorical column (role ID, LABEL, TRACK_ID or COLOR), where a bound would impose an order the values do not have")
    exclude: bool = strawberry.field(description="Whether the rule removes what it matches rather than keeping it. Inverts the whole rule, bounds and values alike")
    label: str | None = strawberry.field(default=None, description="What to call this filter in a picker. Two entries may share a column -- two ranges over one measure are two different rules -- and this is what tells them apart")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    color_by_models.MeshColorByModel,
    description="One entry of a mesh layer's picker: color the collection's objects by a column of the table its FIELD edge keys into, instead of by the layer's flat material color. The same shape `LabelColorBy` carries, and the same relation -- a collection's ids reach a table exactly as a mask's pixel values do -- plus the caption a picker needs",
)
class MeshColorBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source the value is read from. COLUMN for a column of a table, SPARSE for one slice of a matrix -- and the fields of the other member are null")
    table: strawberry.ID | None = strawberry.field(default=None, description="The table dataset holding one row per object. Must be reachable from this layer's collection by a FIELD edge -- the edge `createTableDataset(keyedBy:)` authors and `attributePlans` discovers")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value colors each object")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The sparse dataset one slice of which colors the objects")
    at: list["AxisPosition"] = strawberry.field(default_factory=list, description="(SPARSE) Which slice is read: a position along each axis the source's ids do not index")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap the column's value is mapped through. Which *sort* follows from the column's role, never from a choice here: a measure column (COORDINATE, ATTRIBUTE) takes a continuous one over its range with an optional `min`/`max` window, a categorical one (ID, TRACK_ID, LABEL, COLOR) takes a qualitative one -- a colour per distinct value, no order implied and no window to set")
    min: float | None = strawberry.field(default=None, description="The value mapped to the bottom of the colormap, in the column's own declared `unit`. Null leaves the viewer to stretch the map from the smallest value it reads")
    max: float | None = strawberry.field(default=None, description="The value mapped to the top of the colormap, in the column's own declared `unit`. Null leaves the viewer to stretch the map to the largest value it reads")
    label: str | None = strawberry.field(default=None, description="What to call this colouring in a picker. A caption only: two entries that render identically are refused however they are labelled")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    filter_by_models.MeshFilterByModel,
    description="One entry of a mesh layer's filter picker: draw only the objects whose row in the keyed table satisfies this rule. The sibling of `MeshColorBy` over the same FIELD edge -- same table, same column check -- deciding whether an object is drawn rather than what colour it takes",
)
class MeshFilterBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source this rule tests: a column of a table the ids key into, or one slice of a sparse matrix they index")
    table: strawberry.ID | None = strawberry.field(default=None, description="(COLUMN) The table dataset holding one row per object. Reachable from this layer's collection by a FIELD edge -- the edge `createTableDataset(keyedBy:)` authors and `attributePlans` discovers")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value decides whether an object is drawn")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The matrix one slice of which is tested, instead of a table column")
    at: list[AxisPosition] = strawberry.field(default_factory=list, description="(SPARSE) The position along each axis the matrix identifies itself by -- one slice, which is a value per object")
    min: float | None = strawberry.field(default=None, description="Lower bound, inclusive, in the column's own declared `unit`. Applies to a measure column (role COORDINATE or ATTRIBUTE). Null is an open lower end")
    max: float | None = strawberry.field(default=None, description="Upper bound, inclusive, in the column's own declared `unit`. Applies to a measure column (role COORDINATE or ATTRIBUTE). Null is an open upper end")
    values: list[str] | None = strawberry.field(default=None, description="The values that match, as strings. Applies to a categorical column (role ID, LABEL, TRACK_ID or COLOR), where a bound would impose an order the values do not have")
    exclude: bool = strawberry.field(description="Whether the rule removes what it matches rather than keeping it. Inverts the whole rule, bounds and values alike")
    label: str | None = strawberry.field(default=None, description="What to call this filter in a picker. Two entries may share a column -- two ranges over one measure are two different rules -- and this is what tells them apart")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    color_by_models.NetworkColorByModel,
    description="One entry of a network layer's colour picker. Three kinds where the other pickers have two: COLUMN and SPARSE colour whole objects exactly as a mesh's do, and GRAPH -- this layer kind's own -- colours per node, by a value the collection itself carries (Strahler order, degree, depth, component, a stored radius, a writer's own column). Computed once on the full level-0 graph and only ever subset, so the value is the same at every level a node survives to",
)
class NetworkColorBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source the value is read from. COLUMN for a column of a table, SPARSE for one slice of a matrix, GRAPH for a per-node value the collection carries -- and the fields of the other members are null")
    table: strawberry.ID | None = strawberry.field(default=None, description="The table dataset holding one row per object. Must be reachable from this layer's collection by a FIELD edge -- the edge `createTableDataset(keyedBy: {kind: NETWORK_COLLECTION})` authors")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value colors each object")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The sparse dataset one slice of which colors the objects")
    at: list["AxisPosition"] = strawberry.field(default_factory=list, description="(SPARSE) Which slice is read: a position along each axis the source's ids do not index")
    attribute: str | None = strawberry.field(default=None, description="(GRAPH) The per-node value read: a name the collection's manifest declares, or `radius` when its encoding carries one. Checked against the collection at write, exactly as a column is against its table")
    target: enums.GraphTarget | None = strawberry.field(default=None, description="(GRAPH) Which of the network's two row sets the colouring paints. An edge takes its start node's value either way; EDGE leaves the node glyphs at the layer's base colour")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap the value is mapped through. A COLUMN entry's sort follows from the column's role; a SPARSE or GRAPH entry is always measured and takes a continuous one over its range")
    min: float | None = strawberry.field(default=None, description="The value mapped to the bottom of the colormap. Null leaves the viewer to stretch the map from the smallest value it reads")
    max: float | None = strawberry.field(default=None, description="The value mapped to the top of the colormap. Null leaves the viewer to stretch the map to the largest value it reads")
    label: str | None = strawberry.field(default=None, description="What to call this colouring in a picker. A caption only: two entries that render identically are refused however they are labelled")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    filter_by_models.NetworkFilterByModel,
    description="One entry of a network layer's filter picker. A COLUMN or SPARSE rule keeps or drops whole objects, exactly as a mesh's does; a GRAPH rule -- always `min`/`max` bounds over a per-node value the collection carries -- hides individual nodes and segments, which is what 'trunk only' means on an arbor",
)
class NetworkFilterBy:
    kind: enums.ColorSourceKind = strawberry.field(description="Which sort of source this rule tests: a column of a table, one slice of a sparse matrix, or (GRAPH) a per-node value the collection itself carries")
    table: strawberry.ID | None = strawberry.field(default=None, description="(COLUMN) The table dataset holding one row per object, reachable from this layer's collection by a FIELD edge")
    column: str | None = strawberry.field(default=None, description="(COLUMN) The column of that table whose value decides whether an object is drawn")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(SPARSE) The matrix one slice of which is tested, instead of a table column")
    at: list[AxisPosition] = strawberry.field(default_factory=list, description="(SPARSE) The position along each axis the matrix identifies itself by -- one slice, which is a value per object")
    attribute: str | None = strawberry.field(default=None, description="(GRAPH) The per-node value tested: a name the collection's manifest declares, or `radius` when its encoding carries one. Always measured, so the rule is bounds")
    target: enums.GraphTarget | None = strawberry.field(default=None, description="(GRAPH) Which row set the rule hides. A hidden node takes its glyphs and outgoing segments with it; an EDGE rule hides segments only, each tested by its start node's value")
    min: float | None = strawberry.field(default=None, description="Lower bound, inclusive. Null is an open lower end")
    max: float | None = strawberry.field(default=None, description="Upper bound, inclusive. Null is an open upper end")
    values: list[str] | None = strawberry.field(default=None, description="The values that match, as strings. A categorical COLUMN only -- a sparse slice and a graph attribute are measured")
    exclude: bool = strawberry.field(description="Whether the rule removes what it matches rather than keeping it. Inverts the whole rule, bounds and values alike")
    label: str | None = strawberry.field(default=None, description="What to call this filter in a picker. Two entries may share an attribute -- two ranges over one Strahler order are two different rules -- and this is what tells them apart")
    join_path: list[JoinStep] = strawberry.field(default_factory=list, description=_JOIN_PATH_DESCRIPTION)


@pydantic.type(
    label_models.LabelRenderModel,
    description="How a label layer's discrete object ids become color. Not a transfer function and not a node graph: a label map has one source, no compositing tree, and none of an intensity image's vocabulary -- contrast limits, gamma and colormaps are all meaningless over ids",
)
class LabelRender:
    intensity_axis: str | None = strawberry.field(default=None, description="The lens axis to index, or null when the pixel value itself is the id (the common case for masks)")
    intensity_index: int = strawberry.field(description="The index along that axis to render")
    seed: int = strawberry.field(description="The seed of the hash mapping an id to its color. Changing it repaints every object, which is how two touching objects that happened to hash alike are separated")
    background: int = strawberry.field(description="The id drawn fully transparent -- the 'not an object' value, conventionally 0")
    opacity: float | None = strawberry.field(default=None, description="Opacity applied to the colored ids within the layer (0..1)")
    contour: bool = strawberry.field(description="Whether objects are drawn as outlines rather than filled, so the data underneath stays visible")
    contour_width: float | None = strawberry.field(default=None, description="The width of that outline, in pixels of the mask")
    selected: list[int] = strawberry.field(description="The ids singled out for emphasis. Empty means nothing is selected, which is not the same as everything")
    selection_color: list[int] | None = strawberry.field(default=None, description="The RGBA the selected ids take, overriding their hashed color")
    show_unselected: bool = strawberry.field(description="Whether ids outside the selection still render. False isolates the selection")
    color_bys: list[LabelColorBy] = strawberry.field(
        description="The colourings this layer offers, in the order a picker should show them. Each is a column of a table this mask's FIELD edge keys into, already checked to be reachable and to exist -- the distinction between an instance map and a semantic one, expressed where it belongs. Empty means there is nothing to pick and each id is hashed to a colour"
    )
    active_color_by: int | None = strawberry.field(
        default=None, description="Which entry of `colorBys` is drawn, as an index into it. Null hashes each id to a colour -- what having no colouring has always meant"
    )
    filter_bys: list[LabelFilterBy] = strawberry.field(
        description="The filters this layer offers, in the order a picker should show them. Each keeps or drops objects by a column of a table this mask's FIELD edge keys into, already checked to be reachable and to exist. Empty means nothing is offered and every object draws"
    )
    active_filter_bys: list[int] = strawberry.field(
        description="Which entries of `filterBys` are applied, as indices into it. They combine with AND: an object is drawn when every active rule keeps it. Empty applies none of them, so everything draws"
    )

    @strawberry.field(
        description="The colouring currently drawn: `colorBys[activeColorBy]`, or null when each id is hashed to a colour. Derived, never stored -- there is one copy of the choice, and it is the index",
        deprecation_reason="Read `colorBys` and `activeColorBy` instead: a label layer now publishes a picker rather than a single colouring, and this field can only ever show one of its entries.",
    )
    def color_by(self) -> LabelColorBy | None:
        """The active entry of the picker, or null when the ids are hashed."""
        entries = self.color_bys or []
        index = self.active_color_by
        if index is None or index >= len(entries):
            return None
        return entries[index]

"""Pydantic node graph describing the composable rendering *inside* a single layer.

A layer is the unit that gets alpha-blended into a scene. Its internal render
recipe is a small tagged-union graph: ``channel`` leaf nodes each carry one
intensity channel of the layer's lens together with its own transfer function,
and a ``blend`` node composites its children with an in-layer blend mode. This
mirrors the tagged-union pattern in ``core/render/objects/models.py`` and lets a
single layer combine multiple channels (neuroglancer-style) while the layer as a
whole is still alpha-composited over the layers beneath it.

The union is structurally a tree (a ``blend`` may contain another ``blend``) so
that future nesting is non-breaking, but the canonical graph is one blend level
deep: a single ``blend`` root whose children are ``channel`` sources.
"""

from pydantic import BaseModel, Field
from typing import Literal, Union

from kanne_server import quantities

from core import enums


class LookupStopModel(BaseModel):
    """One control point of an intensity transfer curve: a raw intensity, and the normalized value it maps to."""

    position: float
    value: float


class TransferFunctionModel(BaseModel):
    """How a single channel's intensities are mapped to color before compositing."""

    clim_min: float | None = None
    clim_max: float | None = None
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    gamma: float | None = 1.0
    opacity: float | None = 1.0
    invert: bool | None = False
    # No `categorical` flag: an array whose values are ids is a label layer, whose whole
    # recipe lives in `core.render.layer.label`. A boolean here would be a second way to
    # say what the layer's kind says, free to disagree with it -- and it would keep
    # letting a label source sit as an additive sibling of a fluorescence channel.

    # `stops` is the general case of what `clim_min`/`clim_max` and `gamma` already say: the
    # window is the two-stop curve [(clim_min, 0), (clim_max, 1)], and gamma is the
    # one-parameter family of bends on it. So the three do not compose -- a power law on top
    # of a hand-authored curve is a second answer to the question the curve already settled --
    # and a curve, when given, is the whole transfer. None means there is no curve, not an
    # empty one: the window and gamma apply exactly as they did before this field existed.
    stops: list[LookupStopModel] | None = None


class ChannelSourceModel(BaseModel):
    """A single intensity channel of the layer's lens, with its own transfer function."""

    kind: Literal["channel"] = "channel"
    intensity_axis: str | None = None
    intensity_index: int = 0
    label: str | None = None
    visible: bool = True
    transfer: TransferFunctionModel = Field(default_factory=TransferFunctionModel)


class BlendNodeModel(BaseModel):
    """Composites its children using an in-layer blend mode."""

    kind: Literal["blend"] = "blend"
    blending: enums.Blending = enums.Blending.ADDITIVE
    children: list["LayerNodeUnion"]
    label: str | None = None


class ProjectionNodeModel(BaseModel):
    """Projects the composite of its children through the z-axis using a 3D rendering mode."""

    kind: Literal["projection"] = "projection"
    mode: enums.ProjectionMode = enums.ProjectionMode.MIP
    children: list["LayerNodeUnion"]
    label: str | None = None


class PhasorCursorModel(BaseModel):
    """A region of phasor space, and the color the pixels falling inside it are painted.

    Not a plot widget: a *color rule*. Pixels whose (g, s) lands in this region take this
    color in the rendered overlay, overriding the colormap -- the standard way of picking
    out one species (bound vs free NADH, one FRET state) in the image itself.
    """

    kind: enums.PhasorCursorKind = enums.PhasorCursorKind.CIRCLE
    g: float | None = None
    s: float | None = None
    radius: float | None = None
    points: list[list[float]] | None = None
    color: list[int] | None = None
    label: str | None = None
    visible: bool = True


class PhasorTransferModel(BaseModel):
    """How a phasor becomes the pixel's color.

    Its own type, and not :class:`TransferFunctionModel`, because it maps the *reduction's*
    output -- a (g, s) 2-vector plus a photon count -- rather than a sampled scalar. Folding
    cursors and a phase mapping into the shared pointwise transfer would hang null phasor
    fields off every ordinary channel source.

    ``min``/``max`` bound the value ``mode`` derives, in whatever dimension that value has:
    a duration ("0.5 ns") over a microtime axis, a wavelength ("480 nm") over a spectrum one.
    A dimension-agnostic quantity is what lets one field carry both.
    """

    mode: enums.PhasorColorMode = enums.PhasorColorMode.PHASE
    min: quantities.GenericQuantity | None = None
    max: quantities.GenericQuantity | None = None
    colormap: enums.ColorMap = enums.ColorMap.RAINBOW
    weight_by_intensity: bool = True
    intensity: TransferFunctionModel = Field(default_factory=TransferFunctionModel)
    cursors: list[PhasorCursorModel] = Field(default_factory=list)


class PhasorNodeModel(BaseModel):
    """A leaf that reduces one axis of the lens to a phasor and colors the pixel by it.

    The node is the *reduction spec* -- which axis, which harmonic, which detection channel
    -- exactly as :class:`ChannelSourceModel` is a sampling spec; how the reduction's output
    becomes a color is its ``transfer``. It is a node rather than a transfer function on a
    channel source because it **consumes an axis**: a transfer function is pointwise, one
    scalar in and one color out, while a phasor takes the whole profile along ``phasor_axis``
    (N bins) and reduces it to a single (g, s). ``ProjectionNodeModel`` is a node for the
    same reason.

    Its rendered output is a raster like any other leaf's, so it composites into the scene
    through the layer that owns it. Nothing here draws a scatter plot.

    Known limitation: a lens that *slices* its phasor axis narrows the window the transform
    is taken over, which is physically a different transform than the full-period one. The
    phasor still renders; it is just not comparable to one taken over the full axis.
    """

    kind: Literal["phasor"] = "phasor"
    label: str | None = None
    visible: bool = True
    phasor_axis: str
    intensity_axis: str | None = None
    intensity_index: int = 0
    harmonic: int = 1
    transfer: PhasorTransferModel = Field(default_factory=PhasorTransferModel)


class PhasorRenderModel(BaseModel):
    """The full render recipe of a phasor layer: the same reduction, standing on its own.

    A phasor is expressible twice, and deliberately. As :class:`PhasorNodeModel` it is a leaf
    *inside* a graph, which is what composites a lifetime map with an ordinary intensity
    channel in one layer. As this, it is the whole of a PHASOR layer -- the ordinary case,
    where the phasor is simply what the layer draws. Two representations of one reduction is
    the shape :class:`~core.render.layer.label.LabelRenderModel` already has against a label
    source, and it is safe for the same reason: they sit on different ``kind`` values, so a
    row carries one or the other and never both, and neither is free to disagree with the
    other about a layer.

    What it drops from the node form is what the layer itself now says: a node's ``label`` is
    the layer's ``name``, and a node's ``visible`` is the layer's ``visible``. Keeping either
    here would be a second switch for one decision.
    """

    phasor_axis: str
    intensity_axis: str | None = None
    intensity_index: int = 0
    harmonic: int = 1
    transfer: PhasorTransferModel = Field(default_factory=PhasorTransferModel)


LayerNodeUnion = Union[ChannelSourceModel, BlendNodeModel, ProjectionNodeModel, PhasorNodeModel]


class LayerRenderGraphModel(BaseModel):
    """The full in-layer render recipe, rooted at a single blend node."""

    root: BlendNodeModel


BlendNodeModel.update_forward_refs()
ProjectionNodeModel.update_forward_refs()
LayerRenderGraphModel.update_forward_refs()

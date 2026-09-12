"""The vector layer: a vector-valued lens drawn as glyphs.

Its own module in the way ``network_layer.py`` and ``mesh_layer.py`` are, but its
source is a *lens* -- the sixth member of :data:`core.enums.LENS_BACKED_KINDS`.
What earns it a kind rather than a mode on INTENSITY is the value domain: a
DISPLACEMENT axis's positions are the components of one per-point offset, so the
axis is read as geometry, and none of the intensity vocabulary (a channel index,
a gamma, a projection) survives that change -- exactly LABEL's argument.
"""

from kante.types import Info
import strawberry

from core import types, models, enums
import kante
from pydantic import BaseModel, field_validator, model_validator

from core.logic import coords as coords_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha, assert_contrast_limits, assert_rgba
from core.scoping import get_for_org
from core.mutations.layer import (
    _create_flat_layer,
    _patch_layer_compositing,
    assert_kind,
    assert_renderable,
)


def assert_vector_axis(lens) -> str:
    """The DISPLACEMENT axis a vector layer draws, checked to actually describe vectors.

    The `assert_phasor_axis` of this kind, but resolving rather than validating a choice:
    a lens has at most one DISPLACEMENT axis worth of components, so the axis is derived
    and never taken as input -- a per-layer copy could disagree with the axes themselves.

    Three refusals, each a real wrong picture rather than tidiness. No DISPLACEMENT axis
    means there are no components to read and every drawn arrow would be invented. A
    component count outside 2..3 is not a vector field a glyph can draw: one component is
    a scalar wearing a value axis, and four or more is a jacobian or a feature vector,
    neither of which is an offset. More components than spatial axes -- a 3-vector on a
    flat plane -- has no third direction to draw the third component along, so it would
    either be dropped silently or drawn as a length it is not.
    """
    render = coords_logic.resolve_render_axes(lens.axis_specs)
    if render.vector is None:
        raise ValueError(
            f"This lens has no DISPLACEMENT axis, so there are no vector components to draw ({[spec.name for spec in lens.axis_specs]}). "
            "A vector layer reads its components from a DISPLACEMENT value axis -- an axis whose positions enumerate the components of a per-point offset, stated when the dataset's axes were authored. "
            "For scalar data use createIntensityLayer."
        )

    components = lens.get_size_of_axis(render.vector)
    if components < 2 or components > 3:
        what = "a scalar wearing a value axis -- use createIntensityLayer" if components < 2 else "a per-point matrix or feature vector, not an offset a glyph can draw"
        raise ValueError(f"DISPLACEMENT axis '{render.vector}' has {components} positions, but a drawable vector has 2 or 3 components. {components} components is {what}.")

    spatial = 2 if render.z is None else 3
    if components > spatial:
        raise ValueError(
            f"DISPLACEMENT axis '{render.vector}' carries {components} components over {spatial} spatial axes. "
            "A glyph has no direction to draw the extra component along, so it would be silently dropped or silently wrong -- slice the component axis to the in-plane pair, or draw the field over its volume."
        )

    return render.vector


def _assert_glyph_stride(stride: int | None) -> None:
    """A stride of 0 samples nothing and a negative one is not a stride. Null stays the renderer's budget."""
    if stride is not None and stride < 1:
        raise ValueError(f"glyphStride must be at least 1 (sample every voxel), got {stride}. Omit it to let the renderer pick a stride from its glyph budget.")


def _assert_glyph_scale(scale: float | None) -> None:
    """A zero or negative scale draws nothing or draws every vector reversed -- neither is a scale."""
    if scale is not None and scale <= 0:
        raise ValueError(f"glyphScale must be positive scene-units-per-magnitude-unit, got {scale}. Omit it to auto-normalize against the sampled maximum.")


class CreateVectorLayerInputModel(BaseModel):
    lens: str
    scene: str
    glyph: enums.VectorGlyph | None = None
    glyph_stride: int | None = None
    glyph_scale: float | None = None
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    clim_min: float | None = None
    clim_max: float | None = None
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
    def _contrast_limits_are_a_range(self) -> "CreateVectorLayerInputModel":
        assert_contrast_limits(self.clim_min, self.clim_max)
        return self


@prose_errors
@kante.pydantic_input(CreateVectorLayerInputModel, description="Create a vector layer drawing a vector-valued lens as glyphs")
class CreateVectorLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The ID of the lens providing the data. It must carry a DISPLACEMENT value axis of 2 or 3 positions -- which axis that is is derived from the axes, never chosen here")
    glyph: enums.VectorGlyph | None = strawberry.field(default=None, description="How one sampled vector is drawn (default 'arrow')")
    glyph_stride: int | None = strawberry.field(default=None, description="Sample every Nth voxel per spatial axis. Omit to let the renderer pick a stride from its own glyph budget")
    glyph_scale: float | None = strawberry.field(default=None, description="Scene units drawn per unit of magnitude, in the component axis's unit. Omit to auto-normalize against the sampled maximum")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap applied over glyph magnitude (default 'viridis')")
    color: list[int] | None = strawberry.field(default=None, description="A flat RGBA glyph colour instead of a magnitude colormap: four components, each 0..255. Overrides `colormap` where both are given")
    clim_min: float | None = strawberry.field(default=None, description="Lower magnitude limit for the colormap window, as a vector LENGTH in the component axis's unit -- not a per-component value and not a normalized fraction")
    clim_max: float | None = strawberry.field(default=None, description="Upper magnitude limit for the colormap window, as a vector length in the component axis's unit")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal': glyphs are geometry sitting over what is beneath them, not light to sum)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_vector_layer(info: Info, input: CreateVectorLayerInput) -> types.VectorLayer:
    model = input.to_pydantic()
    lens = get_for_org(models.Lens, info, id=model.lens)
    assert_renderable(lens)
    assert_vector_axis(lens)
    _assert_glyph_stride(model.glyph_stride)
    _assert_glyph_scale(model.glyph_scale)

    # NORMAL, not ADDITIVE, for the RGB layer's reason: glyphs are opaque geometry sitting
    # over what is beneath them, and summing an arrow with the flow field under it draws a
    # brighter arrow where the data is bright, which reads as a fact and is not one.
    return _create_flat_layer(
        info,
        lens_id=model.lens,
        scene_id=model.scene,
        kind=enums.LayerKind.VECTOR,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity,
        visible=model.visible,
        order=model.order,
        glyph=model.glyph or enums.VectorGlyph.ARROW,
        glyph_stride=model.glyph_stride,
        glyph_scale=model.glyph_scale,
        colormap=model.colormap or enums.ColorMap.VIRIDIS,
        color=model.color,
        clim_min=model.clim_min,
        clim_max=model.clim_max,
    )


class UpdateVectorLayerInputModel(BaseModel):
    id: str
    name: str | None = None
    glyph: enums.VectorGlyph | None = None
    glyph_stride: int | None = None
    glyph_scale: float | None = None
    colormap: enums.ColorMap | None = None
    color: list[int] | None = None
    clim_min: float | None = None
    clim_max: float | None = None
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
@kante.pydantic_input(UpdateVectorLayerInputModel, description="Update a vector layer's render settings. Every field is a patch: what is not sent keeps its current value")
class UpdateVectorLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the vector layer to update")
    name: str | None = strawberry.field(default=None, description="A human-readable name for the layer")
    glyph: enums.VectorGlyph | None = strawberry.field(default=None, description="How one sampled vector is drawn")
    glyph_stride: int | None = strawberry.field(default=None, description="Sample every Nth voxel per spatial axis. Omitting this keeps the current stride -- there is no spelling here for 'back to the renderer's budget', because null already means 'unchanged'")
    glyph_scale: float | None = strawberry.field(default=None, description="Scene units drawn per unit of magnitude. Omitting this keeps the current scale -- there is no spelling here for 'back to auto-normalize', because null already means 'unchanged'")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap applied over glyph magnitude")
    color: list[int] | None = strawberry.field(default=None, description="A flat RGBA glyph colour, overriding the colormap: four components, each 0..255")
    clim_min: float | None = strawberry.field(default=None, description="Lower magnitude limit for the colormap window, as a vector length in the component axis's unit")
    clim_max: float | None = strawberry.field(default=None, description="Upper magnitude limit for the colormap window, as a vector length in the component axis's unit")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_vector_layer(info: Info, input: UpdateVectorLayerInput) -> types.VectorLayer:
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.VECTOR, mutation="updateVectorLayer")

    _assert_glyph_stride(model.glyph_stride)
    _assert_glyph_scale(model.glyph_scale)

    # Resolved against what the row will hold *after* the patch, for the pair-check reason
    # `updateIntensityLayer` states: a min sent alone must be checked against the max it
    # will actually sit beside.
    clim_min = model.clim_min if model.clim_min is not None else layer.clim_min
    clim_max = model.clim_max if model.clim_max is not None else layer.clim_max
    assert_contrast_limits(clim_min, clim_max)

    layer.clim_min = clim_min
    layer.clim_max = clim_max
    if model.glyph is not None:
        layer.glyph = model.glyph
    if model.glyph_stride is not None:
        layer.glyph_stride = model.glyph_stride
    if model.glyph_scale is not None:
        layer.glyph_scale = model.glyph_scale
    if model.colormap is not None:
        layer.colormap = model.colormap
    if model.color is not None:
        layer.color = model.color
    _patch_layer_compositing(layer, model)
    layer.save()
    return layer

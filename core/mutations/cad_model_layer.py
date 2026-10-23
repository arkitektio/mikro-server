"""Mutations for CAD model layers.

Its own module in the way ``mesh_layer.py`` is, and much the smaller: a CAD model carries no
data, so a layer over one publishes no pickers and caps no octree. What it has is the mesh
layer's two surface settings, read differently -- a null ``materialColor`` keeps the model's
own materials rather than meaning white.
"""

from kante.types import Info
import strawberry
from pydantic import BaseModel

from core import types, models, enums
import kante
from core.logic import graph as graph_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.mutations._sources import lens_of_kind
from core.mutations.layer import assert_kind
from core.scoping import get_for_org

_MATERIAL_DESCRIPTION = "One RGBA colour replacing every material of the model, each component 0..255. Null draws the model's own materials as authored"


class CreateCadModelLayerInputModel(BaseModel):
    scene: str
    lens: str
    material_color: list[int] | None = None
    wireframe: bool | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None
    name: str | None = None


@prose_errors
@kante.pydantic_input(CreateCadModelLayerInputModel, description="Create a layer that renders a CAD model in a scene. The model's own coordinate system is the layer's space, so it must already have a path to the scene's world")
class CreateCadModelLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    lens: strawberry.ID = strawberry.field(description="The CAD model lens to draw through. A model's whole lens is its `fullLens`")
    material_color: list[int] | None = strawberry.field(default=None, description=f"{_MATERIAL_DESCRIPTION} (the default)")
    wireframe: bool | None = strawberry.field(default=None, description="Whether to draw the model as a wireframe (default false)")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")
    name: str | None = strawberry.field(default=None, description="What to call the layer. Null leaves it to the model's label")


def create_cad_model_layer(info: Info, input: CreateCadModelLayerInput) -> types.CadModelLayer:
    """Place a CAD model in a scene, refusing one with no path into its world."""
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    lens = lens_of_kind(info, kind=models.CadModelLens, lens=model.lens)
    graph_logic.assert_placeable_in(scene.world, getattr(lens.cad_model, "coordinate_system", None), destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.CAD_MODEL,
        scene=scene,
        lens=lens,
        name=model.name,
        material_color=model.material_color,
        wireframe=model.wireframe if model.wireframe is not None else False,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )


class UpdateCadModelLayerInputModel(BaseModel):
    id: str
    material_color: list[int] | None = None
    wireframe: bool | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@strawberry.input(
    description="Retune how a CAD model layer is drawn. A patch: an omitted field keeps its value, and an explicit `null` for `materialColor` returns to the model's own materials. The model and the scene are not editable",
)
class UpdateCadModelLayerInput:
    """Plain `@strawberry.input`, for `UpdateMeshLayerInput`'s reason: an `UNSET` must survive to tell an omitted colour from a cleared one."""

    id: strawberry.ID = strawberry.field(description="The ID of the CAD model layer to update")
    material_color: list[int] | None = strawberry.field(default=strawberry.UNSET, description=f"{_MATERIAL_DESCRIPTION}. Omit to keep the current one")
    wireframe: bool | None = strawberry.field(default=None, description="Whether to draw the model as a wireframe")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")

    def to_pydantic(self) -> UpdateCadModelLayerInputModel:
        """Drop what the caller did not name, so `model_fields_set` records what it did."""
        supplied = {
            "id": self.id,
            "material_color": self.material_color,
            "wireframe": self.wireframe,
            "blending": self.blending,
            "opacity": self.opacity,
            "visible": self.visible,
            "order": self.order,
        }
        return UpdateCadModelLayerInputModel(**{name: value for name, value in supplied.items() if value is not strawberry.UNSET})


def update_cad_model_layer(info: Info, input: UpdateCadModelLayerInput) -> types.CadModelLayer:
    """Patch a CAD model layer's settings, leaving the ones not named alone."""
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    assert_kind(layer, enums.LayerKind.CAD_MODEL, mutation="updateCadModelLayer")

    if "material_color" in model.model_fields_set:
        layer.material_color = model.material_color
    if model.wireframe is not None:
        layer.wireframe = model.wireframe
    if model.blending is not None:
        layer.blending = model.blending
    if model.opacity is not None:
        layer.opacity = model.opacity
    if model.visible is not None:
        layer.visible = model.visible
    if model.order is not None:
        layer.order = model.order
    layer.save()
    return layer

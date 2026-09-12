from kante.types import Info
import strawberry

from core import types, models, enums
import kante
from pydantic import BaseModel
from core.logic import graph as graph_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.scoping import get_for_org


class CreateAnnotationLayerInputModel(BaseModel):
    scene: str
    annotation_collection: str
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(
    CreateAnnotationLayerInputModel,
    description="Create a layer that renders an annotation collection's drawn shapes in a scene. The collection's own coordinate system is the layer's space, so it must already have a path to the scene's world",
)
class CreateAnnotationLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    annotation_collection: strawberry.ID = strawberry.field(description="The ID of the annotation collection whose shapes this layer renders. Its own coordinate system is the layer's space")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_annotation_layer(info: Info, input: CreateAnnotationLayerInput) -> types.AnnotationLayer:
    """Place an existing annotation collection in a scene it is already placeable in.

    The explicit path: a scene-minted collection got its layer for free, and this is how
    that same collection is rendered in a *second* scene -- after its system has been
    registered into that scene's world with ``createTransformation``. Per-shape styling
    lives on the annotations; the layer carries compositing only.
    """
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    collection = get_for_org(models.AnnotationCollection, info, id=model.annotation_collection)

    graph_logic.assert_placeable_in(scene.world, collection.coordinate_system_or_none, destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.ANNOTATION,
        scene=scene,
        annotation_collection=collection,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )

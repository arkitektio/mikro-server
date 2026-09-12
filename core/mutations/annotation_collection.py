"""Mutations for annotation collections.

An annotation collection is the human-drawn counterpart of a mesh collection: it
owns the coordinate system its shapes are drawn in, and an edge relates that
system to whatever the shapes were drawn over. The explicit create here is for
freestanding or dataset-derived collections; the common path -- drawing on a
scene -- goes through ``createAnnotation``, which mints the scene's collection
on first use.
"""

from django.db import transaction
from kante.types import Info
import strawberry
from pydantic import BaseModel, Field

import kante
from core import models, types
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.inputs.file_link import SourceFileInput, SourceFileInputModel
from core.inputs.coords import AxisInput, AxisInputModel, DerivedFromInput, DerivedFromSpec
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import file_link as file_link_logic
from core.logic import folder as folder_logic
from core.logic import graph as graph_logic
from core.mutations._generic import make_delete, self_owner


class CreateAnnotationCollectionInputModel(BaseModel):
    name: str
    description: str | None = None
    axes: list[AxisInputModel]
    folder: str | None = None
    derived_from: list[DerivedFromSpec] | None = None
    source_files: list[SourceFileInputModel] | None = None



@prose_errors
@kante.pydantic_input(
    CreateAnnotationCollectionInputModel,
    description="Input for creating an annotation collection. The collection gets a coordinate system of its own, and an edge relates it to the space the shapes are drawn over",
)
class CreateAnnotationCollectionInput:
    """Input for creating an annotation collection."""

    name: str = strawberry.field(description="The name of the annotation collection")
    description: str | None = strawberry.field(default=None, description="A free-form description of the collection")
    axes: list[AxisInput] = strawberry.field(description="The axes of the collection's own coordinate system, in order. Required: the collection owns its space, and a derivation no longer implies an identity to copy axes across")
    folder: strawberry.ID | None = strawberry.field(
        default=None,
        description="The folder to file this annotation collection in. Organisational only -- distinct from `scene`, and it says nothing about the space the shapes are drawn in. Defaults to the user's default folder",
    )
    derived_from: list[DerivedFromInput] | None = strawberry.field(
        default=None,
        description="What this annotation collection was computed from. One entry per source; the first is the primary parent. Each names its source and how this collection's own space relates to that source's: **omit the transform and the edge is UNMAPPABLE**, recording the lineage and claiming no geometry. State IDENTITY when the geometry is expressed directly in the source's grid, SCALE when it was extracted from a downsampled one",
    )
    source_files: list[SourceFileInput] | None = strawberry.field(
        default=None,
        description="Optional statement of which files these annotations were loaded from -- the GeoJSON or ROI set a converter read. **Not a `derivedFrom` entry, deliberately**: a derivation is an edge of the coordinate graph and relates two spaces, while a file has no space. This records lineage between bytes and data and leaves the graph untouched",
    )


def create_annotation_collection(info: Info, input: CreateAnnotationCollectionInput) -> types.AnnotationCollection:
    """Create an annotation collection, in a coordinate system of its own.

    The same shape as ``create_mesh_collection``: the collection owns its system, and an
    optional edge relates that system to the one the shapes are drawn over. A collection
    created here has no scene -- the scene-minted collection is ``createAnnotation``'s
    business -- so placing it in a scene is an explicit registration plus an
    ``createAnnotationLayer``.
    """
    model = input.to_pydantic()

    ctx = CreationContext.from_info(info)

    # Atomic, because the collection row is written before its axes are checked and before
    # its edges are: without this, an axis ordering the space refuses -- or a rank an edge
    # refuses -- leaves an orphan collection behind and returns an error. The same
    # guarantee `create_coordinate_system` keeps for a space and its registrations.
    with transaction.atomic():
        collection = models.AnnotationCollection.objects.create(
            name=model.name,
            description=model.description,
            folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
            creator=ctx.user,
            organization=ctx.organization,
            **ctx.provenance_kwargs(),
        )

        system = graph_logic.create_collection_system(
            name=f"{collection.name}/drawing",
            axes=model.axes,
            owner=collection,
            ctx=ctx,
        )

        # Optional on purpose: a collection in some absolute space is drawn over nothing.
        coordinate_system_logic.write_derivation_edges(info, name=collection.name, own_system=system, derived_from=model.derived_from or [], ctx=ctx)
        file_link_logic.write_file_links(info, container=collection, source_files=model.source_files or [], ctx=ctx)
        # Once the derivation exists, because it is what decides the answer: a composable one
        # means the boxes are stored in the source's grid, and this is where the collection
        # says so. Inside the transaction with it -- a frame naming an edge that rolled back
        # would outlive the reason it was chosen.
        graph_logic.record_bbox_frame(collection, system)

    return collection


class DeleteAnnotationCollectionInputModel(BaseModel):
    id: str = Field(description="The ID of the annotation collection to delete")


@kante.pydantic_input(DeleteAnnotationCollectionInputModel, description="Input for deleting an annotation collection by ID")
class DeleteAnnotationCollectionInput:
    """Input for deleting an annotation collection by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the annotation collection to delete. Its coordinate system, its annotations and its layers cascade with it")


delete_annotation_collection = make_delete(models.AnnotationCollection, DeleteAnnotationCollectionInput, owner=self_owner)

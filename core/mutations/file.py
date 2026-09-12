from kante.types import Info
import kante
import strawberry
from pydantic import BaseModel, Field

from core import types, models, scalars
from datalayer.datalayer import get_current_datalayer
from core.creation import CreationContext
from core.inputs.file_link import ExportOfInput, ExportOfSpec
from core.logic import file_link as file_link_logic
from core.scoping import get_for_org
from core.mutations._generic import make_delete, self_owner


class FromFileLikeModel(BaseModel):
    file: str = Field(description="The uploaded big-file store to create the file from")
    file_name: str = Field(description="The name of the file")
    folder: str | None = Field(default=None, description="The ID of the folder to put the file in (defaults to the current default folder)")
    export_of: list[ExportOfSpec] | None = Field(default=None, description="The containers this file was written from")


@kante.pydantic_input(FromFileLikeModel, description="Input for creating a file record from an uploaded big-file store")
class FromFileLike:
    """Input for creating a file record from an uploaded big-file store"""

    file: scalars.FileLike = strawberry.field(description="The uploaded big-file store to create the file from")
    file_name: str = strawberry.field(description="The name of the file")
    folder: strawberry.ID | None = strawberry.field(default=None, description="The ID of the folder to put the file in (defaults to the current default folder)")
    export_of: list[ExportOfInput] | None = strawberry.field(
        default=None,
        description=(
            "Optional statement of what this file was written from: the dataset exported to OME-TIFF, the mesh collection written to STL. Recorded as a link between data and "
            "bytes, deliberately not as a coordinate-graph edge -- a file has no space, so there is no map to state. The mirror of `sourceFiles` on a container's create mutation; "
            "use `linkFile` to record an export against a file that already exists"
        ),
    )


def from_file_like(
    info: Info,
    input: FromFileLike,
) -> types.File:
    parsed = input.to_pydantic()
    store = get_for_org(models.BigFileStore, info, id=parsed.file)
    store.fill_info()

    dl = get_current_datalayer()

    ctx = CreationContext.from_info(info)
    folder = get_for_org(models.Folder, info, id=parsed.folder) if parsed.folder else models.Folder.objects.get_current_default(ctx)

    file = models.File.objects.create(
        folder=folder,
        creator=ctx.user,
        organization=ctx.organization,
        membership=ctx.membership,
        # The supplied name, not the store's. `fileName` was required and then ignored, so a
        # client that passed "cells.czi" got whatever name the upload grant happened to
        # record -- the store's is the fallback, not the answer.
        name=parsed.file_name or store.original_file_name,
        size=dl.get_object_size(store.bucket, store.key),
        content_type=store.content_type,
        store=store,
        **ctx.provenance_kwargs(),
    )

    file_link_logic.write_export_links(info, file=file, export_of=parsed.export_of or [], ctx=ctx)

    return strawberry.cast(types.File, file)


class DeleteFileInputModel(BaseModel):
    id: str = Field(description="The ID of the file to delete")


@kante.pydantic_input(DeleteFileInputModel, description="Input for deleting a file by ID")
class DeleteFileInput:
    """Input for deleting a file by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the file to delete")


delete_file = make_delete(models.File, DeleteFileInput, owner=self_owner)

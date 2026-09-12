"""Recording a file link against data that already exists.

``sourceFiles`` and ``exportOf`` cover the case where the link is known at creation time.
This covers the rest: a dataset exported to OME-TIFF months later, a source file identified
after the fact. Same rows, same writer, so a link made here is indistinguishable from one made
at ingest -- which is the point, since nothing downstream should care when it was recorded.
"""

import kante
import strawberry
from kante.types import Info
from pydantic import BaseModel, Field

from core import models, types
from core.creation import CreationContext
from core.inputs.file_link import ExportOfInput, ExportOfSpec, SourceFileInput, SourceFileInputModel
from core.logic import file_link as file_link_logic
from core.mutations._generic import make_delete, self_owner
from core.scoping import get_for_org


class LinkFileModel(BaseModel):
    """A file link stated from whichever end the caller has."""

    source_of: list[ExportOfSpec] | None = Field(default=None, description="The containers this file was written from")
    file: str | None = Field(default=None, description="The file the containers were written to")
    source_files: list[SourceFileInputModel] | None = Field(default=None, description="The files a container was produced from")
    dataset: str | None = Field(default=None, description="The array dataset the files were read into")
    table_dataset: str | None = Field(default=None, description="The table dataset the files were read into")
    mesh_collection: str | None = Field(default=None, description="The mesh collection the files were read into")
    annotation_collection: str | None = Field(default=None, description="The annotation collection the files were read into")


@kante.pydantic_input(
    LinkFileModel,
    description=(
        "Record a link between a file and the data it encodes, after both already exist. Two shapes, one per direction: name a `file` plus `sourceOf` to record what that file was "
        "written from, or name one container plus `sourceFiles` to record which files it was produced from"
    ),
)
class LinkFileInput:
    """Record a file link against data that already exists."""

    file: strawberry.ID | None = strawberry.field(default=None, description="(export) The file that was written. Pair it with `sourceOf`")
    source_of: list[ExportOfInput] | None = strawberry.field(default=None, description="(export) The containers `file` was written from")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(ingest) The array dataset that was produced. Pair it with `sourceFiles`")
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="(ingest) The table dataset that was produced. Pair it with `sourceFiles`")
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="(ingest) The mesh collection that was produced. Pair it with `sourceFiles`")
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="(ingest) The annotation collection that was produced. Pair it with `sourceFiles`")
    source_files: list[SourceFileInput] | None = strawberry.field(default=None, description="(ingest) The files the named container was produced from")


#: The container arguments, and the model each names. Ordered as the SDL declares them.
_CONTAINER_ARGS: tuple[tuple[str, type], ...] = (
    ("dataset", models.ArrayDataset),
    ("table_dataset", models.TableDataset),
    ("mesh_collection", models.MeshCollection),
    ("annotation_collection", models.AnnotationCollection),
)


def link_file(info: Info, input: LinkFileInput) -> list[types.FileLink]:
    """Record file links in whichever direction the input names."""
    parsed = input.to_pydantic()
    ctx = CreationContext.from_info(info)

    named_containers = [(argument, model) for argument, model in _CONTAINER_ARGS if getattr(parsed, argument)]

    if len(named_containers) > 1:
        raise ValueError(f"Name one container, but {', '.join(argument for argument, _ in named_containers)} were all given. A link relates one file to one container.")

    if parsed.file and named_containers:
        raise ValueError("Name a `file` with `sourceOf`, or a container with `sourceFiles` -- not both. The two say which direction the link runs, and naming both leaves it undecided.")

    if named_containers:
        if not parsed.source_files:
            raise ValueError("Naming a container records which files it was produced from, so `sourceFiles` is required. To record the other direction, name a `file` and its `sourceOf` instead.")
        argument, model = named_containers[0]
        container = get_for_org(model, info, id=getattr(parsed, argument))
        return file_link_logic.write_file_links(info, container=container, source_files=parsed.source_files, ctx=ctx)

    if not parsed.file:
        raise ValueError("Name either a `file` (with `sourceOf`) or one container (with `sourceFiles`). A link needs both of its ends.")

    if not parsed.source_of:
        raise ValueError("Naming a `file` records what it was written from, so `sourceOf` is required. To record the other direction, name a container and its `sourceFiles` instead.")

    file = get_for_org(models.File, info, id=parsed.file)
    return file_link_logic.write_export_links(info, file=file, export_of=parsed.source_of, ctx=ctx)


class UnlinkFileModel(BaseModel):
    """The link to delete."""

    id: str = Field(description="The ID of the file link to delete")


@kante.pydantic_input(UnlinkFileModel, description="Input for deleting a file link by ID")
class UnlinkFileInput:
    """Input for deleting a file link by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the file link to delete")


# `self_owner` reads `created_through_by_id`, which is why `FileLink` carries that column
# explicitly rather than relying on `ProvenanceField`.
unlink_file = make_delete(models.FileLink, UnlinkFileInput, owner=self_owner)

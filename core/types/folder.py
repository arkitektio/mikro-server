"""The two organisational types: a raw `File`, and the `Folder` things are filed in.

Split out of the former `core/types/image.py`, which held these alongside `Image`, `Table`,
`ROI` and the twenty-odd `View` types. Those are gone; these two are all that survived, and
they were never image-specific -- a folder files array datasets, table datasets, mesh
collections and annotation collections just as readily.
"""

import datetime
import logging
from typing import TYPE_CHECKING, Annotated, List, Optional, cast

import kante
import strawberry
from kante.types import Info
from strawberry import auto

from core import enums, filters, models, order
from core.types._shared import apply_link_filters, build_prescoped_queryset
from core.types.auth import Organization, ProvenanceEntry, Task, User
from datalayer.types import BigFileStore

if TYPE_CHECKING:
    # Runtime imports here would cycle: each of these modules imports this one back.
    from core.types.array_dataset import AnnotationCollection, ArrayDataset
    from core.types.coords import MeshCollection
    from core.types.file_link import FileLink
    from core.types.table_dataset import TableDataset

logger = logging.getLogger(__name__)


@kante.django_type(
    models.File,
    filters=filters.FileFilter,
    pagination=True,
    federated=True,
    ordering=order.FileOrder,
    description="A file in its original format (e.g. a microscopy vendor file), stored in a BigFileStore. Files are the raw bytes that array datasets, table datasets and mesh collections are converted from.",
)
class File:
    id: auto
    name: auto
    store: BigFileStore

    @kante.django_field(
        description=(
            "The containers converted out of this file: the datasets a converter wrote from it, one per series. **Not a derivation** -- a file has no coordinate system, so these "
            "links claim no geometry and place nothing; they say only that this file's bytes and that data are the same thing"
        ),
        prefetch_related=["links__file"],
    )
    def derived_containers(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming this file as a source."""
        return apply_link_filters(self.links.filter(direction=enums.FileLinkDirectionChoices.SOURCE.value).order_by("pk"), filters, info)

    @kante.django_field(
        description="The containers this file was written from: the dataset exported to OME-TIFF, the mesh collection written to STL. The mirror of `derivedContainers`",
        prefetch_related=["links__file"],
    )
    def exported_from(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming this file as a rendition."""
        return apply_link_filters(self.links.filter(direction=enums.FileLinkDirectionChoices.RENDITION.value).order_by("pk"), filters, info)

    provenance_entries: List["ProvenanceEntry"] = kante.django_field(description="Provenance entries for this file")
    creator: User = kante.django_field(description="The user who created this file")
    created_through: Optional[Task] = kante.django_field(description="The task this file was created through, if any")
    created_through_by: Optional[User] = kante.django_field(description="The assigner of the creating task, if any")
    organization: Organization = kante.django_field(description="The organization this file belongs to")
    size: float | None = kante.django_field(description="The size of the file in bytes")
    content_type: str | None = kante.django_field(description="The content type of the file")

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@kante.django_type(
    models.Folder,
    filters=filters.FolderFilter,
    ordering=order.FolderOrder,
    pagination=True,
    description="A folder is a collection of the things mikro stores. It mimics a folder in a file system and is the top-level container for organising data.",
)
class Folder:
    id: auto
    files: List["File"]
    # The four containers `FileLink` calls "a thing holding data". Being in a folder says
    # nothing about where any of them sit in space.
    array_datasets: List[Annotated["ArrayDataset", strawberry.lazy("core.types.array_dataset")]] = kante.django_field(description="The array datasets filed in this folder")
    table_datasets: List[Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")]] = kante.django_field(description="The table datasets filed in this folder")
    mesh_collections: List[Annotated["MeshCollection", strawberry.lazy("core.types.coords")]] = kante.django_field(description="The mesh collections filed in this folder")
    annotation_collections: List[Annotated["AnnotationCollection", strawberry.lazy("core.types.array_dataset")]] = kante.django_field(description="The annotation collections filed in this folder")
    parent: Optional["Folder"]
    children: List["Folder"]
    description: str | None
    name: str
    provenance_entries: List["ProvenanceEntry"] = kante.django_field(description="Provenance entries for this folder")
    is_default: bool
    created_at: datetime.datetime
    creator: User | None
    created_through: Optional[Task] = kante.django_field(description="The task this folder was created through, if any")
    created_through_by: Optional[User] = kante.django_field(description="The assigner of the creating task, if any")

    @kante.django_field()
    def pinned(self, info: Info) -> bool:
        return cast(models.Folder, self).pinned_by.filter(id=info.context.request.user.id).exists()

    @kante.django_field()
    def tags(self, info: Info) -> list[str]:
        # Was `cast(models.Image, self)` before the split -- a copy-paste that happened to
        # work because the cast is a no-op at runtime.
        return cast(models.Folder, self).tags.slugs()

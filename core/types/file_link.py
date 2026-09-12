"""GraphQL types for file links: the bytes a container came from, and the files written from it.

Read alongside ``derivedFrom``, never instead of it. A derivation relates two *spaces* and is
an edge of the coordinate graph; a file link relates *bytes* to data and is not, because a file
has no space. Asking a dataset where its pixels came from can therefore have two answers that
are both complete and about different things: the image it was deconvolved from, and the CZI it
was converted out of.
"""

import datetime
from typing import TYPE_CHECKING, Annotated, List, Optional, Union

import strawberry
from strawberry import auto

import kante
from kante.types import Info

from core import enums, filters, models, order
from core.types.auth import Organization, ProvenanceEntry, Task, User

if TYPE_CHECKING:
    # Only for the lazy annotations below. Importing them at runtime would be a cycle:
    # `core.types.folder` and the container modules all reference `FileLink` in return.
    from core.types.array_dataset import ArrayDataset, AnnotationCollection
    from core.types.coords import MeshCollection
    from core.types.folder import File
    from core.types.table_dataset import TableDataset


#: The containers a file can be read into or written from. Deliberately narrower than
#: ``Resident``: a lens is a selection over a dataset rather than a thing with its own bytes,
#: and a coordinate system is a space, which no file encodes.
FileLinkContainer = Annotated[
    Union[
        Annotated["ArrayDataset", strawberry.lazy("core.types.array_dataset")],
        Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")],
        Annotated["MeshCollection", strawberry.lazy("core.types.coords")],
        Annotated["AnnotationCollection", strawberry.lazy("core.types.array_dataset")],
    ],
    strawberry.union("FileLinkContainer", description="The data side of a file link: a container whose contents a file encodes"),
]


@kante.django_type(
    models.FileLink,
    filters=filters.FileLinkFilter,
    ordering=order.FileLinkOrder,
    pagination=True,
    description=(
        "A file and a container holding the same data, and which of the two was made from the other. **Not a derivation**: `derivedFrom` states how one space maps into another, "
        "and a file has no space, so this claims no geometry and no placement -- only that these bytes and this data are the same thing"
    ),
)
class FileLink:
    """A file and a container holding the same data."""

    id: auto
    file: Annotated["File", strawberry.lazy("core.types.folder")] = kante.django_field(description="The file side of the link")
    direction: enums.FileLinkDirection = kante.django_field(
        description="Which side was made from the other: SOURCE when the container was produced from the file (an ingest), RENDITION when the file was written from the container (an export)"
    )
    series_identifier: str | None = kante.django_field(
        description="Which part of the file this link concerns -- the series of a multi-series LIF or CZI. Empty when the file holds one thing. Part of the link's identity, so a dataset fused from two series of one file has two links"
    )
    value_relation: enums.ValueRelation | None = kante.django_field(
        description="What the conversion did to the values: IDENTICAL for a lossless transcode, TRANSFORMED for a projection or a contrast-stretched export. Null when unstated"
    )
    provenance_entries: List[ProvenanceEntry] = kante.django_field(description="Provenance entries for this link")
    created_at: datetime.datetime
    # Nullable: the creator FK is SET_NULL, so a link outlives the user who made it.
    creator: Optional[User] = kante.django_field(description="The user who recorded this link")
    created_through: Optional[Task] = kante.django_field(description="The task this link was created through, if any")
    created_through_by: Optional[User] = kante.django_field(description="The assigner of the creating task, if any")
    organization: Organization = kante.django_field(description="The organization this link belongs to")

    @kante.django_field(
        description="The data side of the link. Exactly one container is set on a link, and this is it",
        select_related=["dataset", "table_dataset", "mesh_collection", "annotation_collection"],
    )
    def container(self, info: Info) -> FileLinkContainer:
        """Whichever of the four container FKs is set."""
        container = self.dataset or self.table_dataset or self.mesh_collection or self.annotation_collection
        if container is None:
            # Unreachable through the writer, which refuses a link naming no container. Loud
            # rather than a null, because a link with no data side is a corrupt row, not an
            # absent value -- the field is non-null in the SDL precisely to say so.
            raise ValueError(f"File link {self.pk} names no container. Exactly one of dataset, table dataset, mesh collection or annotation collection must be set.")
        # Returned as the Django model, exactly as `CoordinateSystem.residents` returns its
        # union members: strawberry_django maps model to type through its own registry, and a
        # `strawberry.cast` here stops it doing so.
        return container

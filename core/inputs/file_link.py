"""Input types for file links: the bytes a container came from, and the files written from it.

Deliberately separate from ``core.inputs.coords``. A ``DerivedFromInput`` entry resolves to a
``CoordinateSystem``, because a derivation is an edge of the coordinate graph -- it states how
one space maps into another. A file has no space, so it is not a derivation source and gets no
member of that union; see :class:`core.models.FileLink` for the full argument.

Two directions, two inputs. Ingest names a file from a container's create mutation, so it is a
plain input. Export names a container from ``fromFileLike``, and there are four kinds of
container, so it follows the flat-discriminated-union convention of ``core.input_unions``
exactly as ``DerivedFromInput`` does.
"""

from typing import Annotated, ClassVar, Literal

import strawberry
from pydantic import BaseModel, ConfigDict, Field

import kante

from core import enums
from core.input_unions import parse_union_member, prose_errors, union_memberships


_SERIES_DESCRIPTION = (
    "Which part of the file this concerns -- the series of a multi-series LIF or CZI, the sheet of a workbook. Omit when the file holds one thing. It is part of the link's "
    "identity, not a label on it: one dataset fused from two series of one file is two links, and two links naming the same file and the same series are refused"
)

_VALUE_RELATION_DESCRIPTION = (
    "What the conversion did to the *values*: IDENTICAL for a lossless transcode (a CZI to a Zarr, a dataset to an OME-TIFF), TRANSFORMED for a projection or a contrast-stretched "
    "PNG, CATEGORIZED when the values became labels. Omit when unstated; the converter and its parameters belong to task provenance"
)


class SourceFileInputModel(BaseModel):
    """One file a container was produced from."""

    model_config = ConfigDict(extra="forbid")

    file: str
    series_identifier: str | None = None
    value_relation: enums.ValueRelation | None = None


@kante.pydantic_input(
    SourceFileInputModel,
    description=(
        "One file this container was produced from -- the CZI a converter read to write these arrays, the CSV this table was loaded from. Recorded as a link between bytes and "
        "data, deliberately not as a coordinate-graph edge: a file has no space, so there is no map to state and `derivedFrom` is the wrong mechanism"
    ),
)
class SourceFileInput:
    """One file this container was produced from."""

    file: strawberry.ID = strawberry.field(description="The file this container's data was read out of")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


class ExportOfInputBase(BaseModel):
    """The fields every export link carries, whichever kind of container it names."""

    model_config = ConfigDict(extra="forbid")

    kind: enums.FileLinkContainerKind
    series_identifier: str | None = None
    value_relation: enums.ValueRelation | None = None

    #: The member's own id field, so the resolver needs no per-member branch. A ClassVar, so
    #: pydantic treats it as neither a field nor a private attribute.
    CONTAINER_FIELD: ClassVar[str] = "container"

    @property
    def container_id(self) -> str:
        """The id of the container this file was written from."""
        return getattr(self, type(self).CONTAINER_FIELD)


class DatasetExportOfInputModel(ExportOfInputBase):
    """Written from an array dataset."""

    kind: Literal[enums.FileLinkContainerKind.DATASET] = enums.FileLinkContainerKind.DATASET
    dataset: str
    CONTAINER_FIELD: ClassVar[str] = "dataset"


class TableDatasetExportOfInputModel(ExportOfInputBase):
    """Written from a table dataset."""

    kind: Literal[enums.FileLinkContainerKind.TABLE_DATASET] = enums.FileLinkContainerKind.TABLE_DATASET
    table_dataset: str
    CONTAINER_FIELD: ClassVar[str] = "table_dataset"


class MeshCollectionExportOfInputModel(ExportOfInputBase):
    """Written from a mesh collection."""

    kind: Literal[enums.FileLinkContainerKind.MESH_COLLECTION] = enums.FileLinkContainerKind.MESH_COLLECTION
    mesh_collection: str
    CONTAINER_FIELD: ClassVar[str] = "mesh_collection"


class AnnotationCollectionExportOfInputModel(ExportOfInputBase):
    """Written from an annotation collection."""

    kind: Literal[enums.FileLinkContainerKind.ANNOTATION_COLLECTION] = enums.FileLinkContainerKind.ANNOTATION_COLLECTION
    annotation_collection: str
    CONTAINER_FIELD: ClassVar[str] = "annotation_collection"


#: Every container kind, keyed by discriminator value.
EXPORT_OF_MEMBERS: dict[str, type[BaseModel]] = {
    enums.FileLinkContainerKind.DATASET.value: DatasetExportOfInputModel,
    enums.FileLinkContainerKind.TABLE_DATASET.value: TableDatasetExportOfInputModel,
    enums.FileLinkContainerKind.MESH_COLLECTION.value: MeshCollectionExportOfInputModel,
    enums.FileLinkContainerKind.ANNOTATION_COLLECTION.value: AnnotationCollectionExportOfInputModel,
}

#: The union the pydantic side carries, so a resolver never sees the flat wire shape.
ExportOfSpec = Annotated[
    DatasetExportOfInputModel | TableDatasetExportOfInputModel | MeshCollectionExportOfInputModel | AnnotationCollectionExportOfInputModel,
    Field(discriminator="kind"),
]

#: The wire fields carrying a container id, one per member.
_EXPORT_OF_CONTAINER_FIELDS = ("dataset", "table_dataset", "mesh_collection", "annotation_collection")


@prose_errors
@strawberry.input(
    description=(
        "The container this file was written from, as a discriminated union: `kind` selects which sort of container is being named, and only that member's id field is read -- any "
        'other is rejected. The member inputs annotated `@unionElementOf(union: "ExportOfInput")` say which field each kind reads. Direction is always this file -> the data it was written from'
    ),
)
class ExportOfInput:
    """One container this file was written from, discriminated by `kind`.

    Deliberately not pydantic-backed: the wire type is flat because GraphQL has no input
    unions, and ``to_pydantic`` is where that flatness is corrected into the strict member.
    """

    kind: enums.FileLinkContainerKind = strawberry.field(description="Which sort of thing the container is. It fixes which id field below is read; any other is rejected")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(DATASET) The array dataset this file was written from")
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="(TABLE_DATASET) The table dataset this file was written from")
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="(MESH_COLLECTION) The mesh collection this file was written from")
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="(ANNOTATION_COLLECTION) The annotation collection this file was written from")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)

    def to_pydantic(self) -> BaseModel:
        """Match the flat wire fields to the member model `kind` selects, strictly."""
        supplied = {name: getattr(self, name) for name in ("kind", "series_identifier", "value_relation", *_EXPORT_OF_CONTAINER_FIELDS)}
        data = {name: value for name, value in supplied.items() if value is not None}
        return parse_union_member(EXPORT_OF_MEMBERS, data, noun="export link")


def _export_of_member(model: type, key: "enums.FileLinkContainerKind", description: str):  # noqa: ANN202 - a decorator factory
    """Publish one member input of the ExportOfInput union."""
    return kante.pydantic_input(
        model,
        directives=union_memberships("ExportOfInput", key=key.value),
        description=f"{description}. Published for codegen; the wire type is the flat ExportOfInput",
    )


@_export_of_member(DatasetExportOfInputModel, enums.FileLinkContainerKind.DATASET, "The fields a DATASET export link reads")
class DatasetExportOfInput:
    """The DATASET member of the export container union."""

    kind: enums.FileLinkContainerKind = strawberry.field(description="The discriminator: which member of ExportOfInput this is")
    dataset: strawberry.ID = strawberry.field(description="The array dataset this file was written from")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_export_of_member(TableDatasetExportOfInputModel, enums.FileLinkContainerKind.TABLE_DATASET, "The fields a TABLE_DATASET export link reads")
class TableDatasetExportOfInput:
    """The TABLE_DATASET member of the export container union."""

    kind: enums.FileLinkContainerKind = strawberry.field(description="The discriminator: which member of ExportOfInput this is")
    table_dataset: strawberry.ID = strawberry.field(description="The table dataset this file was written from")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_export_of_member(MeshCollectionExportOfInputModel, enums.FileLinkContainerKind.MESH_COLLECTION, "The fields a MESH_COLLECTION export link reads")
class MeshCollectionExportOfInput:
    """The MESH_COLLECTION member of the export container union."""

    kind: enums.FileLinkContainerKind = strawberry.field(description="The discriminator: which member of ExportOfInput this is")
    mesh_collection: strawberry.ID = strawberry.field(description="The mesh collection this file was written from")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_export_of_member(AnnotationCollectionExportOfInputModel, enums.FileLinkContainerKind.ANNOTATION_COLLECTION, "The fields an ANNOTATION_COLLECTION export link reads")
class AnnotationCollectionExportOfInput:
    """The ANNOTATION_COLLECTION member of the export container union."""

    kind: enums.FileLinkContainerKind = strawberry.field(description="The discriminator: which member of ExportOfInput this is")
    annotation_collection: strawberry.ID = strawberry.field(description="The annotation collection this file was written from")
    series_identifier: str | None = strawberry.field(default=None, description=_SERIES_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


#: The member inputs published to the SDL, for the schema's ``types=[...]``. Dropping one
#: erases it from the SDL silently -- they are referenced by no field.
file_link_union_types: list[type] = [
    DatasetExportOfInput,
    TableDatasetExportOfInput,
    MeshCollectionExportOfInput,
    AnnotationCollectionExportOfInput,
]

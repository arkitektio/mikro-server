from kante.types import Info
import kante
import strawberry
from pydantic import BaseModel, Field
from core import types, models, scalars


class UnstructuredMetaInputModel(BaseModel):
    name: str = Field(description="The name of the metadata entry")
    meta: object = Field(description="The free-form JSON metadata to attach")
    file: str = Field(description="The ID of the file to attach the metadata to")
    schema: str | None = Field(default=None, description="The ID of the schema describing the metadata structure")


@kante.pydantic_input(UnstructuredMetaInputModel, description="Input for attaching free-form JSON metadata to a file")
class UnstructuredMetaInput:
    """Input for attaching free-form JSON metadata to a file"""

    name: str = strawberry.field(description="The name of the metadata entry")
    meta: scalars.Any = strawberry.field(description="The free-form JSON metadata to attach")
    file: strawberry.ID = strawberry.field(description="The ID of the file to attach the metadata to")
    schema: strawberry.ID | None = strawberry.field(default=None, description="The ID of the schema describing the metadata structure")


def attach_unstructured_meta(
    info: Info,
    input: UnstructuredMetaInput,
) -> types.UnstructuredMeta:
    parsed = input.to_pydantic()
    view = models.UnstructuredMeta.objects.create(
        file_id=parsed.file,
        name=parsed.name,
        meta=parsed.meta,
        schema_id=parsed.schema,
    )
    return view

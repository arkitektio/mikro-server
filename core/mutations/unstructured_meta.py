from kante.types import Info
import kante
import strawberry
from pydantic import BaseModel, Field
from core import types, models, scalars
from core.scoping import get_for_org


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
    # Both looked up in the request's organization: raw ids attached metadata to any org's
    # file and handed back any org's schema.
    view = models.UnstructuredMeta.objects.create(
        file=get_for_org(models.File, info, id=parsed.file),
        name=parsed.name,
        meta=parsed.meta,
        schema=get_for_org(models.MetaSchema, info, id=parsed.schema) if parsed.schema else None,
    )
    return view

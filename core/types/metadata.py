import strawberry
import strawberry_django

from core import models, scalars


@strawberry_django.type(
    models.MetaSchema,
    description="Schema for unstructured metadata attached to a file.",
)
class MetaSchema:
    """Schema for unstructured metadata attached to a file."""

    id: strawberry.ID
    name: str
    schema: scalars.Any


@strawberry_django.type(
    models.UnstructuredMeta, description="Unstructured metadata attached to a file."
)
class UnstructuredMeta:
    """Unstructured metadata attached to a file."""

    id: strawberry.ID
    name: str
    meta: scalars.Any
    schema: MetaSchema | None

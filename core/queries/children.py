from core import models, types, filters as f, pagination as p
from core.utils import paginate_querysets
import strawberry
from typing import Annotated, Union
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from enum import Enum
from core.scoping import get_for_org
from kante.types import Info


@strawberry.enum
class ChildrenOrderField(str, Enum):
    # No `UPDATED_AT`: no model in this app has an `updated_at` column, so asking for it
    # raised `Cannot resolve keyword 'updated_at' into field` and took the whole query with
    # it. It was an option that could only ever fail, on every folder, for every caller.
    CREATED_AT = "created_at"
    NAME = "name"


@strawberry.enum
class ChildrenOrderDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


@strawberry.input
class ChildrenOrder:
    field: ChildrenOrderField
    direction: ChildrenOrderDirection


#: Everything that can sit in a folder: sub-folders, raw files, and the four containers.
#: Named explicitly -- an anonymous union takes its SDL name from its members concatenated,
#: which for six members is unusable.
FolderChild = Annotated[
    Union[
        types.Folder,
        types.File,
        types.ArrayDataset,
        types.TableDataset,
        types.MeshCollection,
        types.AnnotationCollection,
    ],
    strawberry.union("FolderChild", description="Anything filed in a folder: a sub-folder, a file, or one of the four containers"),
]


#: Everything filed in a folder, in the order the union lists them. `MeshCollection` is the
#: odd one out and the reason this is a table rather than a list of names: it has no `name`
#: column (it is identified by `version`), so both the search vector and the ordering have
#: to ask it a different question than the rest.
_CHILD_SOURCES = [
    ("children", "name", "description"),
    ("files", "name", None),
    ("array_datasets", "name", "description"),
    ("table_datasets", "name", "description"),
    ("mesh_collections", "version", None),
    ("annotation_collections", "name", "description"),
]


def children(
    info: Info,
    parent: strawberry.ID,
    filters: f.FolderChildrenFilter | None = None,
    pagination: p.ChildrenPaginationInput | None = None,
    order: ChildrenOrder | None = None,
) -> list[FolderChild]:
    if filters is None:
        filters = f.FolderChildrenFilter()
    if pagination is None:
        pagination = p.ChildrenPaginationInput()

    folder = get_for_org(models.Folder, info, id=parent)

    querysets = []
    search = filters.search.strip() if filters.search else ""
    search_query = SearchQuery(search) if search else None

    for accessor, name_field, description_field in _CHILD_SOURCES:
        queryset = getattr(folder, accessor).all()

        if search_query is not None:
            fields = [name_field] + ([description_field] if description_field else [])
            search_vector = SearchVector(*fields)
            queryset = queryset.annotate(search=search_vector, rank=SearchRank(search_vector, search_query)).filter(search=search_query).order_by("-rank")

        if order:
            order_prefix = "" if order.direction == ChildrenOrderDirection.ASC else "-"
            # `name` is the only order field a mesh collection cannot answer; it orders by
            # the thing that identifies it instead. `createdAt` and `updatedAt` are shared.
            order_field = name_field if order.field is ChildrenOrderField.NAME else order.field.value
            queryset = queryset.order_by(f"{order_prefix}{order_field}")

        querysets.append(queryset)

    return paginate_querysets(
        *querysets,
        limit=pagination.limit,
        offset=pagination.offset,
    )

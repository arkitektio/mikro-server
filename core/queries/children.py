from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Union

import strawberry
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import BigIntegerField, Expression, F, FloatField, IntegerField, QuerySet, TextField, Value
from django.db.models.functions import Cast, Coalesce
from kante.types import Info

from core import enums, filters as f, models, pagination as p, types
from core.logic import sizes
from core.scoping import get_for_org, scope_queryset


@strawberry.enum
class ChildrenOrderField(str, Enum):
    # No `UPDATED_AT`: no model in this app has an `updated_at` column, so asking for it
    # raised `Cannot resolve keyword 'updated_at' into field` and took the whole query with
    # it. It was an option that could only ever fail, on every folder, for every caller.
    CREATED_AT = "created_at"
    NAME = "name"
    SIZE = "size"
    KIND = "kind"


@strawberry.enum
class ChildrenOrderDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


@strawberry.input
class ChildrenOrder:
    field: ChildrenOrderField
    direction: ChildrenOrderDirection


#: Everything that can sit in a folder: sub-folders, raw files, and every container.
#: Named explicitly -- an anonymous union takes its SDL name from its members concatenated,
#: which for nine members is unusable.
FolderChild = Annotated[
    Union[
        types.Folder,
        types.File,
        types.ArrayDataset,
        types.TableDataset,
        types.SparseDataset,
        types.MeshCollection,
        types.NetworkCollection,
        types.AnnotationCollection,
        types.Fixture,
    ],
    strawberry.union("FolderChild", description="Anything filed in a folder: a sub-folder, a file, or one of the containers"),
]


@dataclass(frozen=True)
class _ChildSource:
    """One kind of thing a folder holds, and how the listing reads it.

    ``label_fallbacks`` is for the kinds whose name is optional: they are labelled by their
    store (its key, or a fixture's file name) when they have none, so search and the name
    order read a ``sort_label`` annotation rather than a column (named apart from the model's
    ``label`` property, which an annotation of that name would try to assign).
    ``is_container`` says whether a `FileLink` can name it, i.e. whether it can be hidden as
    converted from a file.
    """

    kind: enums.FolderChildKind
    accessor: str
    description_field: str | None
    is_container: bool
    label_fallbacks: tuple[str, ...] = ()
    size: str | None = None

    @property
    def name_field(self) -> str:
        return "sort_label" if self.label_fallbacks else "name"


#: Everything filed in a folder, in the order KIND sorts them.
_CHILD_SOURCES: list[_ChildSource] = [
    _ChildSource(enums.FolderChildKind.FOLDER, "children", "description", False),
    _ChildSource(enums.FolderChildKind.FILE, "files", None, False, size="file"),
    _ChildSource(enums.FolderChildKind.ARRAY_DATASET, "array_datasets", "description", True, size="array"),
    _ChildSource(enums.FolderChildKind.TABLE_DATASET, "table_datasets", "description", True, size="store__size_bytes"),
    # Not a container a `FileLink` names, so nothing hides a sparse matrix or a network as converted.
    _ChildSource(enums.FolderChildKind.SPARSE_DATASET, "sparse_datasets", "description", False, size="sparse"),
    _ChildSource(enums.FolderChildKind.MESH_COLLECTION, "mesh_collections", None, True, label_fallbacks=("store__key",), size="store__size_bytes"),
    _ChildSource(enums.FolderChildKind.NETWORK_COLLECTION, "network_collections", None, False, label_fallbacks=("store__key",), size="store__size_bytes"),
    _ChildSource(enums.FolderChildKind.ANNOTATION_COLLECTION, "annotation_collections", "description", True),
    _ChildSource(enums.FolderChildKind.FIXTURE, "fixtures", None, False, label_fallbacks=("store__original_file_name", "store__key"), size="store__size_bytes"),
]

_KIND_INDEX = {source.kind: index for index, source in enumerate(_CHILD_SOURCES)}


def _size_of(source: _ChildSource) -> Expression:
    if source.size is None:
        return Value(None, output_field=BigIntegerField())
    if source.size == "file":
        # A file states its own size when it was registered; its store measures it when it can.
        return Coalesce("size", "store__size_bytes", output_field=BigIntegerField())
    if source.size == "array":
        return sizes.array_dataset_size_bytes()
    if source.size == "sparse":
        return sizes.sparse_dataset_size_bytes()
    return Cast(source.size, BigIntegerField())


def _sources(filters: f.FolderChildrenFilter) -> list[_ChildSource]:
    if not filters.kinds:
        return list(_CHILD_SOURCES)
    wanted = set(filters.kinds)
    return [source for source in _CHILD_SOURCES if source.kind in wanted]


def _filtered(folder: models.Folder, source: _ChildSource, filters: f.FolderChildrenFilter, info: Info, search_query: SearchQuery | None) -> QuerySet:
    # Scoped again per accessor: the folder is ours, but a row filed into it from another
    # organization (possible before `createFolder` scoped its parent) must not ride along.
    queryset = scope_queryset(getattr(folder, source.accessor).all(), info)
    if source.label_fallbacks:
        # `output_field`: the name is text and the key a varchar, and Django will not pick between them.
        queryset = queryset.annotate(sort_label=Coalesce("name", *source.label_fallbacks, output_field=TextField()))

    # A container converted from a file is that file again in another form, and the file is
    # already in the listing. Only a SOURCE link says so: a RENDITION is a file written
    # *out of* the container, and a container is not hidden for having been exported.
    if source.is_container and not filters.show_converted:
        queryset = queryset.exclude(file_links__direction=enums.FileLinkDirectionChoices.SOURCE)

    if search_query is not None:
        fields = [source.name_field] + ([source.description_field] if source.description_field else [])
        vector = SearchVector(*fields)
        queryset = queryset.annotate(search=vector, rank=SearchRank(vector, search_query)).filter(search=search_query)
    else:
        queryset = queryset.annotate(rank=Value(0.0, output_field=FloatField()))
    return queryset


def _search_query(filters: f.FolderChildrenFilter) -> SearchQuery | None:
    search = filters.search.strip() if filters.search else ""
    return SearchQuery(search) if search else None


def _sort_expression(source: _ChildSource, field: ChildrenOrderField) -> Expression:
    if field is ChildrenOrderField.NAME:
        return Cast(source.name_field, TextField())
    if field is ChildrenOrderField.CREATED_AT:
        return F("created_at")
    if field is ChildrenOrderField.SIZE:
        return _size_of(source)
    return Value(_KIND_INDEX[source.kind], output_field=IntegerField())


def children(
    info: Info,
    parent: strawberry.ID,
    filters: f.FolderChildrenFilter | None = None,
    pagination: p.ChildrenPaginationInput | None = None,
    order: ChildrenOrder | None = None,
) -> list[FolderChild]:
    """One page of a folder's contents, ordered across every kind at once.

    Each kind is its own table, so the page is chosen from a UNION of what every kind
    contributes -- its kind, its key and the value it sorts by -- ordered and sliced in the
    database, and only the rows on the page are then loaded, kind by kind. Concatenating the
    kinds instead sorted only within each: every folder first, then every file, whatever
    `order` asked for.
    """
    if filters is None:
        filters = f.FolderChildrenFilter()
    if pagination is None:
        pagination = p.ChildrenPaginationInput()
    limit = pagination.limit if pagination.limit is not None else 200
    offset = pagination.offset or 0

    folder = get_for_org(models.Folder, info, id=parent)
    search_query = _search_query(filters)
    sources = _sources(filters)
    if not sources:
        return []

    # No order and a search: the best match first, whatever its kind. No order and no
    # search: grouped by kind, as a folder view lists sub-folders before files.
    field = order.field if order else ChildrenOrderField.KIND
    descending = order is not None and order.direction is ChildrenOrderDirection.DESC
    by_rank = order is None and search_query is not None

    keyed: list[QuerySet] = []
    for source in sources:
        queryset = _filtered(folder, source, filters, info, search_query)
        keyed.append(
            queryset.annotate(
                child_kind=Value(_KIND_INDEX[source.kind], output_field=IntegerField()),
                child_key=Cast("pk", TextField()),
                child_sort=_sort_expression(source, field),
                child_name=Cast(source.name_field, TextField()),
            )
            .order_by()
            .values_list("child_kind", "child_key", "child_sort", "child_name", "rank")
        )

    combined = keyed[0].union(*keyed[1:], all=True) if len(keyed) > 1 else keyed[0]
    if by_rank:
        ordering = [F("rank").desc(), F("child_kind").asc(), F("child_name").asc(nulls_last=True), F("child_key").asc()]
    else:
        primary = F("child_sort").desc(nulls_last=True) if descending else F("child_sort").asc(nulls_last=True)
        # Ties (and everything under KIND) fall back to the name, then the key, so a page never
        # shuffles between two requests.
        ordering = [primary, F("child_kind").asc(), F("child_name").asc(nulls_last=True), F("child_key").asc()]
    page = list(combined.order_by(*ordering)[offset : offset + limit])

    by_kind: dict[int, list[str]] = {}
    for kind_index, key, _sort, _name, _rank in page:
        by_kind.setdefault(kind_index, []).append(key)

    loaded: dict[tuple[int, str], object] = {}
    for kind_index, keys in by_kind.items():
        source = _CHILD_SOURCES[kind_index]
        rows = scope_queryset(getattr(folder, source.accessor).filter(pk__in=keys), info)
        for row in rows:
            loaded[(kind_index, str(row.pk))] = row

    return [loaded[(kind_index, key)] for kind_index, key, _sort, _name, _rank in page if (kind_index, key) in loaded]


@strawberry.type(description="How many of one kind of thing a folder holds, under the same filter as `children`")
class FolderChildKindCount:
    kind: enums.FolderChildKind
    count: int


@strawberry.type(description="How much a folder holds, under the same filter as `children`: the total, and per kind")
class FolderChildCounts:
    total: int = strawberry.field(description="Everything `children` would page through with this filter")
    kinds: list[FolderChildKindCount] = strawberry.field(description="One entry per kind the filter admits, zeros included, in the order KIND sorts them")


def children_counts(
    info: Info,
    parent: strawberry.ID,
    filters: f.FolderChildrenFilter | None = None,
) -> FolderChildCounts:
    """What `children` pages through, counted: one count per kind, and their sum."""
    if filters is None:
        filters = f.FolderChildrenFilter()
    folder = get_for_org(models.Folder, info, id=parent)
    search_query = _search_query(filters)
    kinds = [FolderChildKindCount(kind=source.kind, count=_filtered(folder, source, filters, info, search_query).count()) for source in _sources(filters)]
    return FolderChildCounts(total=sum(entry.count for entry in kinds), kinds=kinds)

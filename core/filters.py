import datetime
import strawberry
from core import enums, models
from core.inputs.coords import BoundingBoxInput, CoordinateInput
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import descriptor_filter
from core.logic.descriptor_filter import DescriptorConstraintInput

#: What a `requires` filter says of itself, on every structure that has one.
REQUIRES_DESCRIPTION = (
    "Filter to the objects satisfying every one of these descriptor constraints: the `requires` of a port, handed on unchanged, so a picker offers what the port accepts. "
    "Answered in SQL from the descriptors stored on every row. A key the structure does not declare is refused rather than ignored"
)
from core.logic import file_link as file_link_logic
from core.logic import graph as graph_logic
from core.logic import space_graph
from core.scoping import for_org
from koherent.models import Task as KoherentTask
from strawberry import auto
from enum import Enum
from typing import Callable, Optional, Sequence
from strawberry_django.filters import FilterLookup
from kante.types import Info
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet
from django.db.models.functions import Coalesce
from embeddings.search import hybrid_search
import kante


@strawberry.input
class FolderChildrenFilter:
    show_children: bool | None = None
    search: str | None = None
    show_converted: bool | None = strawberry.field(
        default=None,
        description=(
            "Also list the containers that were converted from a file (those with a SOURCE file link). Hidden unless this is true, because the file they were made from "
            "is listed already. Any SOURCE link hides a container, wherever its file is kept; a RENDITION link (an export) never does"
        ),
    )


@strawberry.input(
    description=(
        "One container a file link points at: `kind` says which sort of thing it is, `id` says which one. Structured rather than a bare ID because a link can name four "
        "different kinds of container and their ids are drawn from separate sequences -- dataset 3 and table dataset 3 both exist, and an unqualified 3 could not choose"
    )
)
class FileLinkContainerRef:
    """One container a file link points at."""

    kind: enums.FileLinkContainerKind = strawberry.field(description="Which sort of container. It fixes which column the filter reads")
    id: strawberry.ID = strawberry.field(description="The container's ID, in the sequence its `kind` names")


@strawberry.input(
    description=(
        "What a lens picker is asking for: a destination space, and optionally which sort of candidate. Structured rather than a bare space id because `derivedOnly` and "
        "`asLayer` are qualifications *of* the placeability question -- a lens is not derived or label-shaped in the abstract, it is those things on the way into a particular space"
    )
)
class LensPlaceableFilter:
    """The destination space of a `placeableIn` question, and the narrowings of it."""

    space: strawberry.ID = strawberry.field(
        description="The space to be placed into. A *space*, not a scene: every scene over one world offers the same candidates, so a scene-shaped argument would ask for more than the answer depends on. Pass `scene.worldCoordinateSystem.id` to ask it of a scene"
    )
    derived_only: bool | None = strawberry.field(
        default=None,
        description=(
            "Keep only the lenses that *needed* a lineage tree to get here: the segmentations, deconvolutions and projections placed by an ancestor's registration. What the space "
            "registers directly is dropped, even when it is itself a derived dataset -- it does not need its lineage to be placeable. A narrowing of the candidate list and nothing "
            "more: every lens it keeps is one `createLayer` accepts, and every lens it drops is too"
        ),
    )
    as_layer: enums.LayerKind | None = strawberry.field(
        default=None,
        description=(
            "Keep only the lenses that could source a layer of this kind, named as `LayerKind` names it. The six array kinds require the lens to be drawable at all -- an x and a y axis "
            "of more than one pixel, the gate layer creation applies -- and three ask something further: `LABEL` a primary derivation declaring CATEGORIZED, `RGB` a channel axis with at "
            "least three positions to cut the components from, and `PHASOR` a MICROTIME or SPECTRUM axis. `RGB` is capacity only -- whether those three channels *are* red, green and blue "
            "is not a question any axis can answer, which is why RGB is never inferred. The five container kinds keep the lenses over the container the kind draws: `POINT` and `TRACK` a "
            "table with coordinate columns, `MESH` a mesh collection, `NETWORK` a network collection, `ANNOTATION` an annotation collection; a sparse lens answers `placeableIn` without "
            "`asLayer` only, since nothing draws a matrix. Note that *omitting* this applies no renderability gate, so `IMAGE` is a real narrowing rather than a no-op: the unqualified "
            "filter answers what is placeable, which is a spatial question, not what is drawable"
        ),
    )


#: The extensions each `FileMimeGroup` recognizes. Deliberately a filter-side table and not a
#: stored column: nothing to migrate, nothing to backfill, and no way for the label to drift
#: from the file it describes -- change this dict and every existing file reclassifies.
#:
#: Extension first, `contentType` only as a fallback, because a CZI/LIF/ND2 uploads as
#: `application/octet-stream`: a content-type rule would file every vendor image under OTHER,
#: which is exactly the set worth being able to find.
_MIME_GROUP_EXTENSIONS: dict[str, tuple[str, ...]] = {
    enums.FileMimeGroup.IMAGE.value: ("czi", "lif", "nd2", "oib", "oif", "lsm", "ims", "scn", "svs", "ndpi", "vsi", "dv", "ome.tiff", "ome.tif", "tiff", "tif", "png", "jpg", "jpeg", "gif", "bmp"),
    enums.FileMimeGroup.TABLE.value: ("csv", "tsv", "parquet", "feather", "arrow", "xlsx", "xls"),
    enums.FileMimeGroup.MESH.value: ("stl", "obj", "ply", "off", "glb", "gltf"),
    enums.FileMimeGroup.ANNOTATION.value: ("geojson", "roi", "xml"),
    enums.FileMimeGroup.DOCUMENT.value: ("pdf", "txt", "md", "rst", "docx", "json", "yaml", "yml"),
    enums.FileMimeGroup.ARCHIVE.value: ("zip", "tar", "gz", "tgz", "bz2", "7z"),
}

#: The `contentType` prefix each group falls back to when the extension says nothing.
_MIME_GROUP_PREFIXES: dict[str, tuple[str, ...]] = {
    enums.FileMimeGroup.IMAGE.value: ("image/",),
    enums.FileMimeGroup.TABLE.value: ("text/csv", "text/tab-separated-values"),
    enums.FileMimeGroup.DOCUMENT.value: ("text/", "application/pdf"),
    enums.FileMimeGroup.ARCHIVE.value: ("application/zip", "application/x-tar", "application/gzip"),
}


def _mime_group_q(prefix: str, group: enums.FileMimeGroup) -> Q:
    """Files whose extension -- or failing that, whose content type -- puts them in this group.

    OTHER is the complement rather than a list of its own: anything no group claims. Built
    that way so the groups stay exhaustive by construction, and adding an extension to one of
    them removes it from OTHER in the same edit.
    """
    value = group.value if hasattr(group, "value") else group

    def claim(name: str) -> Q:
        query = Q()
        for extension in _MIME_GROUP_EXTENSIONS.get(name, ()):
            query |= Q(**{f"{prefix}name__iendswith": f".{extension}"})
        for content_type in _MIME_GROUP_PREFIXES.get(name, ()):
            query |= Q(**{f"{prefix}content_type__istartswith": content_type})
        return query

    if value != enums.FileMimeGroup.OTHER.value:
        return claim(value)

    claimed = Q()
    for name in _MIME_GROUP_EXTENSIONS:
        claimed |= claim(name)
    return ~claimed


def _file_ids_with_links(direction: str | None = None) -> QuerySet:
    """The ids of every file carrying a link, or only one in the given direction.

    A values-list for a `pk__in` test rather than a join to negate: see `not_derived`.
    """
    links = models.FileLink.objects.all()
    if direction is not None:
        links = links.filter(direction=direction)
    return links.values("file_id")


def _link_exclusion(prefix: str, ids: QuerySet, *, negate: bool) -> Q:
    """`pk__in` these ids, or its complement."""
    matches = Q(**{f"{prefix}id__in": ids})
    return ~matches if negate else matches


def _containers_with_source_file(column: str) -> QuerySet:
    """The ids of every container of one kind some file was converted into.

    A values-list for a `pk__in` test, for the same reason as `_file_ids_with_links`.
    """
    return models.FileLink.objects.filter(direction=enums.FileLinkDirectionChoices.SOURCE.value, **{f"{column}__isnull": False}).values(f"{column}_id")


def _container_link_q(prefix: str, ref: "FileLinkContainerRef", direction: str | None = None) -> Q:
    """Files linked to the container this ref names, optionally in one direction only.

    The kind -> column mapping comes from `core.logic.file_link.column_for_kind`, which is
    composed from the same two tables the writers use -- so a filter and a mutation cannot
    disagree about which column a kind means.
    """
    column = file_link_logic.column_for_kind(ref.kind)
    lookup = {f"{prefix}links__{column}_id": ref.id}
    if direction is not None:
        lookup[f"{prefix}links__direction"] = direction
    return Q(**lookup)


# Mixins: reusable filter fields shared across filter types. All methods are
# prefix-aware so they compose correctly when the filter is nested inside
# another filter (the prefix carries the relation path).


@strawberry.input
class IdsFilterMixin:
    @kante.filter_field(description="Filter by list of IDs")
    def ids(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})


@strawberry.input
class ColumnOptionFilter:
    """Narrowings of a picker's options, shared by every options query.

    One input for all four -- `colorByOptions` and `filterByOptions` over a mesh collection,
    `labelColorByOptions` and `labelFilterByOptions` over a lens -- because they narrow one set: which columns are reachable, which roles
    they declare and which table they read from are questions about the candidates, not about
    what a caller intends to do with them.

    A plain input rather than a `kante.filter_type`, and applied in Python rather than in SQL,
    because there is no single queryset behind an option: it is a (join path, table, column)
    triple derived from a graph walk and a schema walk. The same reason `FolderChildrenFilter`
    is shaped this way.
    """

    search: str | None = strawberry.field(default=None, description="Case-insensitive substring, matched against the column's name, its `longName` and the name of the table it lives in. The same `icontains` the list queries' `search` uses")
    controls: list[enums.ColumnControl] | None = strawberry.field(default=None, description="Keep only the options admitting these controls: MEASURE for the ones taking a colormap and a range, CATEGORICAL for the ones taking a value set")
    roles: list[enums.ColumnRole] | None = strawberry.field(default=None, description="Keep only the options whose column declares one of these roles. Finer than `controls`, which groups the roles into the two the pickers actually branch on")
    table: strawberry.ID | None = strawberry.field(
        default=None,
        description="Keep only the options whose value is **read from** this table -- the terminal one, not a table the `joinPath` passes through on the way. An option hopping from A into B is kept by `table: B` and dropped by `table: A`"
    )
    direct_only: bool | None = strawberry.field(default=None, description="Keep only the options whose `joinPath` is empty -- the columns of the tables the collection's ids key directly, with no `references` hop")


@strawberry.input
class NameSearchFilterMixin:
    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


# The two mixins below are the semantic twins of the two above, for the models that carry an
# embedding (``embeddings.models.EmbeddedDescriptionMixin``: Folder, ArrayDataset,
# TableDataset). Same lexical leg, OR "the query means the description"; lexical hits rank
# first, then by similarity. Nested use (``prefix``) stays lexical -- the filter agent's
# parent is what gets ordered, not the child.
@strawberry.input
class SemanticSearchFilterMixin:
    """``search`` = full-text on the name OR semantic similarity to name + description."""

    @kante.filter_field(description="Search by name (full-text) or by the meaning of the query against name and description. Textual matches rank first, then by similarity; an explicit `ordering` replaces that ranking")
    def search(self, info: Info, queryset: QuerySet, value: str, prefix: str) -> tuple[QuerySet, Q]:
        """Annotate the distance and OR the semantic predicate onto the full-text one."""
        return hybrid_search(queryset, prefix, value, Q(**{f"{prefix}name__search": value}))


@strawberry.input
class SemanticNameSearchFilterMixin:
    """``search`` = substring of the name OR semantic similarity to name + description."""

    @kante.filter_field(description="Search by name (case-insensitive substring) or by the meaning of the query against name and description. Substring matches rank first, then by similarity; an explicit `ordering` replaces that ranking")
    def search(self, info: Info, queryset: QuerySet, value: str, prefix: str) -> tuple[QuerySet, Q]:
        """Annotate the distance and OR the semantic predicate onto the substring one."""
        return hybrid_search(queryset, prefix, value, Q(**{f"{prefix}name__icontains": value}))


@strawberry.input
class CreatedAtFilterMixin:
    @kante.filter_field(description="Filter for items created before this datetime")
    def created_before(self, info: Info, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}created_at__lt": value})

    @kante.filter_field(description="Filter for items created after this datetime")
    def created_after(self, info: Info, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}created_at__gt": value})


@strawberry.input
class OwnedFilterMixin(CreatedAtFilterMixin):
    @kante.filter_field(description="Filter by the creator's subject ID")
    def owner(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}creator__sub": value})


@strawberry.input
class PinnedFilterMixin:
    @kante.filter_field(description="Filter by whether the current user has pinned the item")
    def pinned(self, info: Info, value: bool, prefix: str) -> Q:
        if value:
            return Q(**{f"{prefix}pinned_by": info.context.request.user})
        return ~Q(**{f"{prefix}pinned_by": info.context.request.user})


@strawberry.input
class TagsFilterMixin:
    @kante.filter_field(description="Filter by tag names")
    def tags(self, info: Info, queryset: QuerySet, value: list[str], prefix: str) -> tuple[QuerySet, Q]:
        # Multiple matching tags would duplicate rows on the join.
        return queryset.distinct(), Q(**{f"{prefix}tags__name__in": value})


@strawberry.input
class CreatedThroughFilterMixin:
    @kante.filter_field(description="Filter by the rekuest task id the item was created through")
    def created_through_task(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}created_through__task_id": value})

    @kante.filter_field(description="Filter by the database ID of the task the item was created through (the `createdThrough { id }` field)")
    def created_through(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match items created through the task with this database ID."""
        return Q(**{f"{prefix}created_through_id": value})

    @kante.filter_field(description="Filter by the sub of the user that assigned the creating task")
    def assigned_by(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        # Hits the denormalized FK on the model itself; the join through the
        # (very large) task table would scale with the user's task count.
        return Q(**{f"{prefix}created_through_by__sub": value})

    @kante.filter_field(description="Filter by the database ID of the user that assigned the creating task (the `createdThroughBy { id }` field)")
    def created_through_by(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match items whose creating task was assigned by the user with this database ID."""
        return Q(**{f"{prefix}created_through_by_id": value})


# Store filters


# Folder filter (needed by ImageFilter/FileFilter as a nested filter)


@kante.filter_type(models.Folder)
class FolderFilter(IdsFilterMixin, SemanticSearchFilterMixin, OwnedFilterMixin, PinnedFilterMixin, TagsFilterMixin, CreatedThroughFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]
    description: Optional[FilterLookup[str]]
    is_default: Optional[bool]

    @kante.filter_field(description="Filter for folders with (true) or without (false) a parent")
    def parentless(self, info: Info, value: bool, prefix: str) -> Q:
        if value:
            return Q(**{f"{prefix}parent": None})
        return ~Q(**{f"{prefix}parent": None})

    @kante.filter_field(description="Filter by the parent folder (list the children of a folder)")
    def parent(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match folders that are direct children of the folder with this ID."""
        return Q(**{f"{prefix}parent_id": value})


@kante.filter_type(models.File)
class FileFilter(IdsFilterMixin, NameSearchFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.File, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]
    size: Optional[FilterLookup[int]]
    content_type: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the folder this file belongs to")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}folder_id__in": value})

    @kante.filter_field(
        description=(
            "Filter for files nothing was exported into: the raw sources a converter read, as opposed to the files written out of data already here. Reads the file's links, "
            "which replaced the `origins` M2M -- that column was never written by any resolver, so this filter used to answer `true` for every file in the database"
        )
    )
    def not_derived(self, info: Info, value: bool, prefix: str) -> Q:
        """Match files nothing here was exported into."""
        # A subquery, not `~Q(links__direction=...)`. Negating a to-many lookup is correct on
        # its own, but the moment a second `links__` lookup lands in the same query -- say
        # `{notDerived: true, sourceOf: {...}}` -- Django builds a second join and the
        # negation stops meaning what it reads as. `_derived_dataset_ids` already draws this
        # line on the dataset side; this is the same shape.
        return _link_exclusion(prefix, _file_ids_with_links(direction=enums.FileLinkDirectionChoices.RENDITION.value), negate=value)

    @kante.filter_field(
        description=(
            "Filter for files no data references at all -- the uploads nothing was ever converted from and nothing was ever written into. The orphans, in other words: what a "
            "cleanup view wants. `notDerived` is the weaker question (nothing was *exported* into it), so every unlinked file is also notDerived, and not the reverse"
        )
    )
    def unlinked(self, info: Info, value: bool, prefix: str) -> Q:
        """Match files with no links in either direction."""
        return _link_exclusion(prefix, _file_ids_with_links(), negate=value)

    @kante.filter_field(
        description=(
            "Filter to the files this container was produced from -- the CZI a converter read to write its arrays. The file-side mirror of `ArrayDatasetFilter.sourceFile`, and the "
            "reason this takes a `{kind, id}` rather than a bare ID: dataset 3 and table 3 both exist, so an unqualified id could not say which was meant"
        )
    )
    def source_of(self, info: Info, queryset: QuerySet, value: "FileLinkContainerRef", prefix: str) -> tuple[QuerySet, Q]:
        """Match files this container was produced from."""
        return queryset.distinct(), _container_link_q(prefix, value, direction=enums.FileLinkDirectionChoices.SOURCE.value)

    @kante.filter_field(description="Filter to the files written out of this container -- the OME-TIFF a dataset was exported to. The opposite direction from `sourceOf`")
    def exported_from(self, info: Info, queryset: QuerySet, value: "FileLinkContainerRef", prefix: str) -> tuple[QuerySet, Q]:
        """Match files written out of this container."""
        return queryset.distinct(), _container_link_q(prefix, value, direction=enums.FileLinkDirectionChoices.RENDITION.value)

    @kante.filter_field(description="Filter to the files linked to this container in *either* direction: read into it or written out of it. Use `sourceOf` or `exportedFrom` when the direction matters")
    def linked_to(self, info: Info, queryset: QuerySet, value: "FileLinkContainerRef", prefix: str) -> tuple[QuerySet, Q]:
        """Match files linked to this container in either direction."""
        return queryset.distinct(), _container_link_q(prefix, value)

    @kante.filter_field(description="Filter to files linked under this series of a multi-series file -- 'series-3' of a LIF. Matches on any link, in either direction")
    def series_identifier(self, info: Info, queryset: QuerySet, value: str, prefix: str) -> tuple[QuerySet, Q]:
        """Match files with a link naming this series."""
        return queryset.distinct(), Q(**{f"{prefix}links__series_identifier": value})

    @kante.filter_field(description="Filter by whether the file's bytes ever arrived: false finds the `File` rows whose upload was granted and never completed, which carry no store at all")
    def has_store(self, info: Info, value: bool, prefix: str) -> Q:
        """Match files that do or do not have a store."""
        return Q(**{f"{prefix}store__isnull": not value})

    @kante.filter_field(
        description=(
            "Filter by whether the upload completed. **Implies a store**: a file with no store at all is `hasStore: false`, not `populated: false`, so the two are not "
            "complementary and combining `hasStore: false` with `populated: false` matches nothing"
        )
    )
    def populated(self, info: Info, value: bool, prefix: str) -> Q:
        """Match files whose store is (or is not) populated. Storeless files match neither."""
        return Q(**{f"{prefix}store__populated": value})

    @kante.filter_field(description="Filter by whether any unstructured metadata has been attached to the file")
    def has_metadata(self, info: Info, queryset: QuerySet, value: bool, prefix: str) -> tuple[QuerySet, Q]:
        """Match files that do or do not carry unstructured metadata."""
        has_meta = Q(**{f"{prefix}unstructured_metas__isnull": False})
        return queryset.distinct(), has_meta if value else ~has_meta

    @kante.filter_field(
        description=(
            "Filter by file extension, case-insensitively and with the leading dot optional: `czi`, `.czi` and `CZI` are the same request, and `ome.tiff` matches only the "
            "double extension. A normalizing convenience over `name: {iEndsWith: \".czi\"}`, which is still there if you want the raw lookup"
        )
    )
    def extension(self, info: Info, value: str, prefix: str) -> Q:
        """Match files whose name ends in this extension."""
        suffix = value.strip().lstrip(".")
        if not suffix:
            return Q()
        return Q(**{f"{prefix}name__iendswith": f".{suffix}"})

    @kante.filter_field(
        description=(
            "Filter to the files holding one sort of thing. Derived from the extension at query time and stored nowhere -- see `FileMimeGroup`, which explains why this reads "
            "the name rather than `contentType`. A curated list, so treat it as a picker convenience and filter on `name` or `contentType` when you need an exact answer"
        )
    )
    def mime_group(self, info: Info, value: enums.FileMimeGroup, prefix: str) -> Q:
        """Match files whose extension puts them in this group."""
        return _mime_group_q(prefix, value)


@kante.filter_type(models.FileLink)
class FileLinkFilter(IdsFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    """Filters for the links between a file and the data it encodes."""

    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.FileLink, queryset, value, prefix)

    id: auto
    direction: Optional[enums.FileLinkDirection]
    series_identifier: Optional[FilterLookup[str]]
    value_relation: Optional[enums.ValueRelation]

    @kante.filter_field(description="Filter by the file side of the link")
    def file(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match links whose file side is this file."""
        return Q(**{f"{prefix}file_id": value})


# Multi-dimensional data system filters


@kante.filter_type(models.ArrayDataset)
class ArrayDatasetFilter(IdsFilterMixin, SemanticNameSearchFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]
    description: Optional[FilterLookup[str]]

    @kante.filter_field(
        description=(
            "Filter to the datasets satisfying every one of these descriptor constraints: the `requires` of a port, handed on unchanged, so a picker offers what the port accepts. "
            "Answered in SQL from the descriptors stored on every dataset (`@mikro/value_kind` restored whenever a derivation edge leaving it is drawn). A key datasets do not declare is refused rather than ignored"
        )
    )
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.ArrayDataset, queryset, value, prefix)

    # Filing, not placement. `dataset` below and `placeableIn` ask where the data *is*;
    # these two ask where a user *keeps* it.
    @kante.filter_field(description="Filter by the folder this dataset is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match array datasets filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match array datasets filed in any of the given folders."""
        return Q(**{f"{prefix}folder_id__in": value})

    # What a dataset is, is materialized onto `stored_spec` at creation (see ArrayDataset.spec and
    # core.logic.graph.create_pixel_axes), so this reads the column rather than re-deriving the
    # spec in SQL. A stored list holds exactly one spatial member plus a modifier per non-spatial
    # axis type, so JSONB containment gives the all-of semantics directly: two spatial members can
    # never co-occur in a stored list, so `[IMAGE, VOLUME]` matches nothing, and a headless
    # dataset (empty list) is excluded by any non-empty request without a special guard.

    @kante.filter_field(
        description="Filter to datasets satisfying every one of these specs, e.g. [VOLUME, TIMESERIES] for 3D timelapses. Materialized at creation from the axes of the intrinsic coordinate system and the level-0 shape; an axis counts only when it has more than one position, so a stack of one plane is an IMAGE and a single frame is not a TIMESERIES (use `hasAxisTypes` to ask whether an axis is declared at all). A dataset carries one spatial spec (by how many such SPACE axes it has) plus a modifier per non-spatial axis type it has more than one position along (TIMESERIES, MULTICHANNEL, SPECTRAL, FLIM, DISPLACEMENT_FIELD, COORDINATE_FIELD, INDEXED), and TRACE when it is a curve along one measured axis -- `[TRACE]` finds what a chart can draw whole. Two spatial specs together match nothing"
    )
    def spec(self, info: Info, queryset: QuerySet, value: list[enums.ArrayDatasetSpec], prefix: str) -> tuple[QuerySet, Q]:
        if not value:
            return queryset, Q()
        return queryset, Q(**{f"{prefix}stored_spec__contains": [spec.value for spec in value]})

    @kante.filter_field(description="Filter to datasets whose intrinsic coordinate system carries every one of these axis types, e.g. [TIME, CHANNEL]. The raw form of `spec`: it asks whether an axis is declared at all, where a spec counts it only when it has more than one position")
    def has_axis_types(self, info: Info, queryset: QuerySet, value: list[enums.AxisType], prefix: str) -> tuple[QuerySet, Q]:
        types = {axis_type.value for axis_type in value}
        if not types:
            return queryset, Q()
        queryset, alias = _annotate_axis_type_count(queryset, prefix, types)
        return queryset, Q(**{alias: len(types)})

    @kante.filter_field(description="Filter by whether the dataset carries a resolution pyramid: true for the multiscale ones, false for those with a single level")
    def multiscale(self, info: Info, queryset: QuerySet, value: bool, prefix: str) -> tuple[QuerySet, Q]:
        # The same derivation as the `multiscale` property -- more than one level -- as a query.
        alias = f"_{prefix.replace('__', '_')}level_count"
        queryset = _annotate_once(queryset, alias, Count(f"{prefix}data_arrays", distinct=True))
        return queryset, Q(**{f"{alias}__gt": 1}) if value else Q(**{f"{alias}__lte": 1})

    @kante.filter_field(
        description="Filter by whether the dataset has an edge into a space with real units. False finds the data that is still only pixels, with no pixel size or stage pose recorded. Unrelated to a phasor histogram's `calibrated`, which is about reference correction"
    )
    def has_physical_space(self, info: Info, queryset: QuerySet, value: bool, prefix: str) -> tuple[QuerySet, Q]:
        # A physical space is not a thing a dataset owns (RFC-9): it is an edge out of the
        # dataset's space into one whose axes carry units. So "has a physical space" is a
        # question about the graph, and it is asked as one -- an edge whose far side has a
        # united axis.
        physical_space = models.Transformation.objects.filter(
            input_id=OuterRef(f"{prefix}coordinate_system_id"),
            output__axes__unit__isnull=False,
        ).exclude(kind=enums.TransformKindChoices.UNMAPPABLE.value)
        queryset = _annotate_once(queryset, "_has_physical_space", Exists(physical_space))
        return queryset, Q(_has_physical_space=value)

    @kante.filter_field(description="Filter to datasets rendered in this scene, through their lenses' layers. What is actually staged there -- for what merely could be, use `placeableIn`")
    def scene(self, info: Info, queryset: QuerySet, value: strawberry.ID, prefix: str) -> tuple[QuerySet, Q]:
        # The inverse of the `scenes` field, which is itself derived rather than stored:
        # a scene is a composition, so there is no dataset-to-scene column to filter on.
        # Two to-many hops (lenses, then layers), either of which can repeat the row.
        return queryset.distinct(), Q(**{f"{prefix}lenses__layers__scene_id": value})

    @kante.filter_field(description="Filter to datasets placeable into this coordinate system: those with a lens whose space reaches it across steps that compose into one affine map, walking the transformation edges. A route crossing a FIELD is not one -- it relates the two spaces by the values of an array, which no renderer can draw with, so such a dataset is not offered and `createLayer` would refuse it. Takes a *space*, not a scene, because that is all the answer depends on -- every scene over one world offers the same candidates. Pass `scene.worldCoordinateSystem.id` to ask it of a scene. What could be staged there -- for what already is, use `scene`")
    def placeable_in(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        space = _placeable_destination(info, value)
        if space is None:
            return Q(pk__in=[])
        return Q(**{f"{prefix}id__in": graph_logic.placeable_lens_dataset_ids(space)})

    @kante.filter_field(
        description="Filter to the datasets computed from this one -- the deconvolutions, segmentations and projections that named a space of it as their parent. Every child, not just the ones it places: a fusion that named it second is listed, and so is a child whose derivation is UNMAPPABLE, since it still came from here"
    )
    def derived_from(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": _derived_dataset_ids(source_id=value)})

    @kante.filter_field(description="Filter for datasets that were acquired rather than computed: true for the roots, those with no derivation edge into another dataset's space")
    def not_derived(self, info: Info, value: bool, prefix: str) -> Q:
        derived = Q(**{f"{prefix}id__in": _derived_dataset_ids()})
        return ~derived if value else derived

    @kante.filter_field(
        description=(
            "Filter to the datasets converted from this file -- every series of it, unless `sourceSeriesIdentifier` narrows that. A file link, not a derivation: this asks which "
            "bytes the arrays were read out of, where `derivedFrom` asks which data they were computed from. A dataset can honestly answer both"
        )
    )
    def source_file(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match datasets converted from this file."""
        return Q(**{f"{prefix}file_links__file_id": value, f"{prefix}file_links__direction": enums.FileLinkDirectionChoices.SOURCE.value})

    @kante.filter_field(description="Filter to the datasets converted from one series of a file. Pair it with `sourceFile`; alone it matches that series identifier in any file")
    def source_series_identifier(self, info: Info, value: str, prefix: str) -> Q:
        """Match datasets converted from this series of a file."""
        return Q(**{f"{prefix}file_links__series_identifier": value, f"{prefix}file_links__direction": enums.FileLinkDirectionChoices.SOURCE.value})

    @kante.filter_field(
        description="Filter by whether the dataset was converted out of a file: true for the series a converter read from an upload, false for data written straight in (by an app, an agent, a script). Pair `false` with `notDerived` for the acquired data that came without a file"
    )
    def has_source_file(self, info: Info, value: bool, prefix: str) -> Q:
        """Match datasets with, or without, a SOURCE file link."""
        return _link_exclusion(prefix, _containers_with_source_file("dataset"), negate=not value)

    # Overlap, read off the derived columns (core.derived): a dataset whose acquisition began
    # before the window closed and ended after it opened. One with no stated time never matches.
    @kante.filter_field(description="Filter to datasets acquired, at least in part, after this moment: their `acquisitionEnd` is at or after it. With `acquiredBefore`, the datasets whose acquisition overlaps the window. A dataset with no stated acquisition time never matches")
    def acquired_after(self, info: Info, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}acquisition_end__gte": value})

    @kante.filter_field(description="Filter to datasets acquired, at least in part, before this moment: their `acquisitionStart` is at or before it. With `acquiredAfter`, the datasets whose acquisition overlaps the window. A dataset with no stated acquisition time never matches")
    def acquired_before(self, info: Info, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}acquisition_start__lte": value})

    @kante.filter_field(description="Filter by whether the dataset's anchors contradict each other: true finds the nonsensical metadata (`derivationConflicts`), false the consistent")
    def has_derivation_conflicts(self, info: Info, value: bool, prefix: str) -> Q:
        # A subquery, not a join: a dataset with a conflict on both ends would come back twice.
        conflicted = Q(**{f"{prefix}pk__in": models.DerivationConflict.objects.values("dataset_id")})
        return conflicted if value else ~conflicted

    @kante.filter_field(description="Filter by whether the dataset nominates a scene to open (through its whole lens). False finds the ones with no thumbnail")
    def has_default_scene(self, info: Info, value: bool, prefix: str) -> Q:
        """Match datasets whose whole lens does or does not nominate a scene."""
        nominating = models.ArrayLens.objects.filter(dataset_id=OuterRef(f"{prefix}pk" if prefix else "pk"), slices=[], default_scene__isnull=False)
        return Q(Exists(nominating)) if value else ~Q(Exists(nominating))


@kante.filter_type(models.Animation)
class AnimationFilter(IdsFilterMixin, NameSearchFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the scene this tour flies through")
    def scene(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}scene_id": value})


@kante.filter_type(models.SceneSnapshot)
class SceneSnapshotFilter(IdsFilterMixin, NameSearchFilterMixin, OwnedFilterMixin, PinnedFilterMixin, CreatedThroughFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]

    # No `dataset` filter: a snapshot is a picture of a composition and has no dataset FK to
    # hang one off. Which datasets a picture shows is a placement question, and answering it
    # means a graph walk per scene, which a list filter should not pay for. The nearest thing
    # is `sceneSnapshots(filters: {scene: <a dataset's defaultScene>})` -- the scene a dataset
    # nominates, which is a choice rather than a derivation.

    @kante.filter_field(description="Filter by the scene this snapshot is a picture of")
    def scene(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}scene_id": value})

    @kante.filter_field(description="Filter by a list of scenes (fetch the tiles for a set of scenes in one query, the way a picker does)")
    def scenes(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match snapshots of any of the given scenes."""
        return Q(**{f"{prefix}scene_id__in": value})


@kante.filter_type(models.DataArray)
class DataArrayFilter(IdsFilterMixin):
    id: auto
    level: Optional[FilterLookup[int]]

    @kante.filter_field(description="Filter by the dataset this array belongs to")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}dataset_id": value})


@kante.filter_type(models.AnnotationCollection)
class AnnotationCollectionFilter(IdsFilterMixin, OwnedFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.AnnotationCollection, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the folder this annotation collection is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match annotation collections filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match annotation collections filed in any of the given folders."""
        return Q(**{f"{prefix}folder_id__in": value})

    @kante.filter_field(description="Filter by the scene this collection was minted for as its default drawing surface")
    def scene(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}scene_id": value})

    @kante.filter_field(description="Filter by the coordinate system the annotations are drawn in (the collection's own)")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__id": value})

    @kante.filter_field(description="Filter by the dataset the shapes are drawn over, following the derivation edge")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__in": _systems_derived_from_dataset(value)})

    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@kante.filter_type(models.Annotation)
class AnnotationFilter(IdsFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]
    description: Optional[FilterLookup[str]]
    kind: auto

    @kante.filter_field(
        description=(
            "Filter to the annotations satisfying every one of these descriptor constraints: the `requires` of a port, handed on unchanged, so a picker offers what the port accepts. "
            "Answered in SQL from the descriptors stored on every annotation: `@mikro/annotation_kind` is the kind (named as the descriptor names it, e.g. PATH) and the axis counts are those of the collection's drawing space. A key annotations do not declare is refused rather than ignored"
        )
    )
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Annotation, queryset, value, prefix)

    @kante.filter_field(description="Filter by the collection this annotation belongs to")
    def collection(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}collection_id": value})

    @kante.filter_field(description="Filter by the coordinate system this annotation is drawn in (its collection's own)")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}collection__coordinate_system__id": value})

    @kante.filter_field(description="Filter by the dataset the annotations are drawn over, following the collection's derivation edge")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}collection__coordinate_system__in": _systems_derived_from_dataset(value)})

    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    def _require_frame(self, op: str) -> None:
        # Boxes only compare within one frame: every collection's bbox lives in its
        # own nearest-intrinsic space, so an unscoped spatial predicate would compare
        # numbers from different spaces and call the mismatches results.
        if self.collection is strawberry.UNSET and self.coordinate_system is strawberry.UNSET:
            raise ValueError(f"`{op}` compares boxes within one frame: pass `collection` or `coordinateSystem` alongside it.")

    @kante.filter_field(
        description="Filter to annotations pinned to every one of these coordinates, e.g. [{name: 't', value: 3}]. GIN-backed containment on the stored coordinate dict; an annotation that spans a coordinate does not match a pin on it"
    )
    def pinned_to(self, info: Info, value: list[CoordinateInput], prefix: str) -> Q:
        if not value:
            return Q()
        return Q(**{f"{prefix}coordinates__contains": {coordinate.name: coordinate.value for coordinate in value}})

    @kante.filter_field(
        description="Filter to annotations whose intrinsic bounding box overlaps this box (GiST-backed). Only meaningful within one frame: pass `collection` or `coordinateSystem` alongside, and bound every axis of that frame -- a box of lower rank is refused rather than zero-filled on the missing coordinates"
    )
    def intersects(self, info: Info, value: BoundingBoxInput, prefix: str) -> Q:
        self._require_frame("intersects")
        rank = self._frame_rank()
        if rank is not None and (len(value.min) != rank or len(value.max) != rank):
            raise ValueError(
                f"`intersects` names a box of rank {len(value.min)} in a frame of {rank} axes. A probe bounds every axis of its frame: a coordinate left out would be read as 0, "
                "which is a position, not an absence."
            )
        return Q(**{f"{prefix}bbox_cube__overlaps": (value.min, value.max)})

    def _frame_rank(self) -> int | None:
        """How many axes the frame the predicate was scoped to has: the collection's box frame, or the named system."""
        if self.collection is not strawberry.UNSET and self.collection is not None:
            collection = models.AnnotationCollection.objects.filter(pk=self.collection).first()
            frame = collection.effective_bbox_system if collection is not None else None
            return frame.axes.count() if frame is not None else None
        if self.coordinate_system is not strawberry.UNSET and self.coordinate_system is not None:
            system = models.CoordinateSystem.objects.filter(pk=self.coordinate_system).first()
            return system.axes.count() if system is not None else None
        return None

    @kante.filter_field(
        description="Filter to annotations whose intrinsic bounding box contains this point (GiST-backed). Only meaningful within one frame: pass `collection` or `coordinateSystem` alongside"
    )
    def contains_point(self, info: Info, value: list[float], prefix: str) -> Q:
        self._require_frame("containsPoint")
        return Q(**{f"{prefix}bbox_cube__contains_point": value})


def _lens_spec_q(kind: "type[models.Lens]", value: "Sequence[Enum]", prefix: str) -> Q:
    """The lenses of one kind whose stored spec holds every one of these members. An empty list asks nothing."""
    if not value:
        return Q()
    return Q(**{f"{prefix}polymorphic_ctype": ContentType.objects.get_for_model(kind), f"{prefix}stored_spec__contains": [member.value for member in value]})


@kante.filter_type(models.Lens)
class LensFilter(IdsFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    id: auto

    @kante.filter_field(
        description=(
            "Filter to the lenses satisfying every one of these descriptor constraints: the `requires` of a port, handed on unchanged, so a picker offers what the port accepts. "
            "Answered in SQL from the descriptors stored on every lens, restored when what they count changes (a derivation edge, a shape drawn). A key only some kinds declare holds of no lens of another kind. A key no lens declares is refused rather than ignored"
        )
    )
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Lens, queryset, value, prefix)

    @kante.filter_field(description="Filter by the kind of container the lens selects over")
    def kind(self, info: Info, value: enums.LensKind, prefix: str) -> Q:
        # By the row's content type -- the column every polymorphic row carries, indexed with
        # its FK -- rather than by a join into the kind's table.
        model = next(model for model in models.Lens.KIND_BY_CONTAINER.values() if model.KIND == value.value)
        return Q(**{f"{prefix}polymorphic_ctype": ContentType.objects.get_for_model(model)})

    # One filter per vocabulary, because a GraphQL argument has one type and the three enums
    # share member names (SCALAR is an array's and a matrix's). Each reads the spec
    # materialized on the row and keeps only its own kind, so `arraySpec: [IMAGE]` never
    # answers a table. JSONB containment gives the all-of semantics, as on the datasets.
    @kante.filter_field(
        description="Filter to the array lenses whose selection satisfies every one of these specs, e.g. [IMAGE] for every plane, single-channel or not, or [PROFILE, TRACE] for what a chart draws as a trace. The spec is the selection's, not the dataset's: an axis counts only when the lens keeps more than one position along it, so one plane of a stack matches [IMAGE] and its dataset [VOLUME]"
    )
    def array_spec(self, info: Info, value: list[enums.ArrayDatasetSpec], prefix: str) -> Q:
        return _lens_spec_q(models.ArrayLens, value, prefix)

    @kante.filter_field(
        description="Filter to the table lenses whose selection satisfies every one of these specs, e.g. [POINTS_2D, TRACKED]. The spec is the selection's: a coordinate column a window pins to one value (min == max) does not count, so one frame of a tracking table does not match [TIMED]"
    )
    def table_spec(self, info: Info, value: list[enums.TableDatasetSpec], prefix: str) -> Q:
        return _lens_spec_q(models.TableLens, value, prefix)

    @kante.filter_field(
        description="Filter to the sparse lenses whose selection satisfies every one of these specs, e.g. [SLICE] for the lenses a SPARSE colour or filter picker entry accepts. An axis a window pins to one position does not count"
    )
    def sparse_spec(self, info: Info, value: list[enums.SparseDatasetSpec], prefix: str) -> Q:
        return _lens_spec_q(models.SparseLens, value, prefix)

    @kante.filter_field(description="Filter to the array lenses over this dataset")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}arraylens__dataset_id": value})

    @kante.filter_field(description="Filter to the lenses over this table dataset")
    def table_dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}tablelens__table_dataset_id": value})

    @kante.filter_field(description="Filter to the lenses over this sparse dataset")
    def sparse_dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}sparselens__sparse_dataset_id": value})

    @kante.filter_field(description="Filter to the lenses over this mesh collection")
    def mesh_collection(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}meshlens__mesh_collection_id": value})

    @kante.filter_field(description="Filter to the lenses over this network collection")
    def network_collection(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}networklens__network_collection_id": value})

    @kante.filter_field(description="Filter to the lenses over this annotation collection")
    def annotation_collection(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}annotationlens__annotation_collection_id": value})

    @kante.filter_field(
        description=(
            "Filter by whether the lens cuts anything out of its container. True keeps the crops, sub-volumes and windows; false the whole lenses, one per container -- a list meant for "
            "people usually wants true"
        )
    )
    def sliced(self, info: Info, value: bool, prefix: str) -> Q:
        whole = Q()
        for kind in models.Lens.KIND_BY_CONTAINER.values():
            column = "slices" if kind is models.ArrayLens else "windows"
            whole |= Q(**{f"{prefix}{kind.__name__.lower()}__{column}": []})
        return ~whole if value else whole

    @kante.filter_field(
        description=(
            "Filter array lenses by whether their dataset was acquired rather than computed: true keeps the lenses over the roots, false those over a derived dataset -- the "
            "same line `ArrayDatasetFilter.notDerived` draws. Only array lenses are judged: a lens over a table, sparse dataset, mesh, network or annotation collection passes "
            "true and fails false"
        )
    )
    def not_derived(self, info: Info, value: bool, prefix: str) -> Q:
        # A pk subquery, not `~Q(arraylens__dataset_id__in=...)`: negating across the child join
        # would read NULL for every non-array lens and drop it, emptying a mixed list of its
        # tables and meshes.
        derived = Q(**{f"{prefix}pk__in": models.ArrayLens.objects.filter(dataset_id__in=_derived_dataset_ids()).values("pk")})
        return ~derived if value else derived

    @kante.filter_field(
        description=(
            "Search by the lens' own name or, since most lenses have none and are known by their container's, by its container's label: the name of a dataset, table, sparse dataset or "
            "annotation collection, the name or store key of a mesh or network collection (case-insensitive substring)"
        )
    )
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return (
            Q(**{f"{prefix}name__icontains": value})
            | Q(**{f"{prefix}arraylens__dataset__name__icontains": value})
            | Q(**{f"{prefix}tablelens__table_dataset__name__icontains": value})
            | Q(**{f"{prefix}sparselens__sparse_dataset__name__icontains": value})
            | Q(**{f"{prefix}meshlens__mesh_collection__name__icontains": value})
            | Q(**{f"{prefix}meshlens__mesh_collection__store__key__icontains": value})
            | Q(**{f"{prefix}networklens__network_collection__name__icontains": value})
            | Q(**{f"{prefix}networklens__network_collection__store__key__icontains": value})
            | Q(**{f"{prefix}annotationlens__annotation_collection__name__icontains": value})
        )

    @kante.filter_field(
        description=(
            "Filter to lenses placeable into a coordinate system: those whose space reaches it across steps that compose into one affine map, walking the transformation edges. Takes a *space*, not a "
            "scene -- pass `scene.worldCoordinateSystem.id` to ask it of a scene. `derivedOnly` and `asLayer` narrow the answer for a particular picker; with neither, this is "
            "the whole set layer creation would accept"
        )
    )
    def placeable_in(self, info: Info, value: "LensPlaceableFilter", prefix: str) -> Q:
        space = _placeable_destination(info, value.space)
        if space is None:
            return Q(pk__in=[])

        # `bool(...)` rather than `is not None`: an omitted nested field can arrive as
        # `strawberry.UNSET` rather than None (see `AnnotationFilter._require_frame`), and
        # UNSET is not None -- which would send every unparameterised query down the
        # narrowed path below. Falsiness is right for UNSET, None and False alike.
        placeable = graph_logic.placeable_container_ids(space, derived_only=bool(value.derived_only))
        dataset_ids = placeable[models.ArrayDataset]
        if not value.as_layer:
            # The plain path, and it stays plain indexed `<container>_id__in` lookups, one per
            # kind: placeability is a property of the container, so every lens of a placeable
            # container is placeable and no `distinct()` is needed.
            placeable_lenses = Q(**{f"{prefix}arraylens__dataset_id__in": dataset_ids})
            for container_model, lens_model in models.Lens.KIND_BY_CONTAINER.items():
                if lens_model is not models.ArrayLens:
                    placeable_lenses |= Q(**{f"{prefix}{lens_model.__name__.lower()}__{lens_model.CONTAINER_FIELD}_id__in": placeable[container_model]})
            return placeable_lenses

        if value.as_layer in _COLLECTION_LAYER_KINDS:
            lens_model, container_ids = _COLLECTION_LAYER_KINDS[value.as_layer](placeable)
            return Q(**{f"{prefix}{lens_model.__name__.lower()}__{lens_model.CONTAINER_FIELD}_id__in": container_ids})

        if value.as_layer == enums.LayerKind.LABEL:
            dataset_ids = graph_logic.categorized_dataset_ids(dataset_ids)
        # Renderability is per *lens* -- a slice can crop x to a single column -- so the
        # answer stops being a dataset question and the filter keys on lens ids.
        return Q(**{f"{prefix}id__in": _renderable_lens_ids(dataset_ids, requires=_LENS_KIND_REQUIREMENTS.get(value.as_layer))})

    @kante.filter_field(
        description=(
            "Filter to lenses whose extent overlaps this annotation, wherever the two are co-registered: lenses over the dataset it was drawn on, and lenses over any other dataset "
            "registered into a space its collection reaches. Both extents are composed per request from the shapes and the edges and compared in the nearest space they share, so "
            "this costs one coordinate-graph walk per such space rather than an index lookup. A lens the server cannot bound there -- placed only per index, or across a warp -- is "
            "not returned"
        )
    )
    def overlaps_annotation(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match the lenses `space_graph.lenses_overlapping` finds; an annotation that is not here matches none."""
        annotation = for_org(models.Annotation, info).filter(pk=value).select_related("collection__coordinate_system").prefetch_related("collection__coordinate_system__axes").first()
        if annotation is None:
            return Q(pk__in=[])
        return Q(**{f"{prefix}id__in": space_graph.lenses_overlapping(info, annotation)})

    @kante.filter_field(
        description=(
            "Filter to the lenses co-registered with this annotation: lenses over the dataset it was drawn on, and lenses over any other dataset registered into a space its "
            "collection reaches, walking the transformation edges. The candidates `overlapsAnnotation` compares the shape against, without the comparison -- so a crop the shape "
            "lies outside of is still returned. What a picker offers once an annotation is chosen. The lenses over the annotation's own collection are always among them; an annotation whose collection has no edge out of its drawing space reaches no other"
        )
    )
    def reachable_from(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match the lenses `space_graph.lenses_reachable_from` finds; an annotation that is not here matches none."""
        annotation = for_org(models.Annotation, info).filter(pk=value).select_related("collection__coordinate_system").first()
        if annotation is None:
            return Q(pk__in=[])
        return Q(**{f"{prefix}id__in": space_graph.lenses_reachable_from(info, annotation)})


@kante.filter_type(models.Scene)
class SceneFilter(IdsFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Scene, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]
    blending: auto

    @kante.filter_field(description="Search by name (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @kante.filter_field(description="Filter to the scenes with a layer drawing through this lens or any selection within it, of any kind: the same set the lens' own `scenes` answers")
    def lens(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        lens = for_org(models.Lens, info).filter(pk=value).first()
        within = coordinate_system_logic.lens_ids_within(lens) if lens is not None else [int(value)]
        return Q(**{f"{prefix}pk__in": models.Layer.objects.filter(lens_id__in=within).values("scene_id")})

    # Named `defaultForLens`, not `defaultFor`, because the *field* `Scene.defaultFor` is already
    # the list of lenses nominating this scene. One name meaning a list on the type and a single
    # id on its filter is the sort of thing that reads fine while being written and confuses
    # everyone afterwards.
    @kante.filter_field(description="Filter to the scene this lens nominates as its default -- a container's whole lens for the container. A choice someone made, not the set of scenes that show the lens; for that, ask the lens' `scenes`")
    def default_for_lens(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match the scene a given lens nominates."""
        return Q(**{f"{prefix}default_for_lenses__id": value})



@kante.filter_type(models.Layer)
class LayerFilter(IdsFilterMixin):
    id: auto
    kind: auto
    blending: auto

    @kante.filter_field(description="Filter by the scene this layer is placed in")
    def scene(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}scene_id": value})

    @kante.filter_field(description="Filter image layers by the lens they render")
    def lens(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}lens_id": value})


@kante.filter_type(models.CoordinateAnchor)
class CoordinateAnchorFilter(IdsFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.CoordinateAnchor, queryset, value, prefix)

    id: auto
    dataset: Optional[FilterLookup[strawberry.ID]]
    table: Optional[FilterLookup[strawberry.ID]]
    sparse: Optional[FilterLookup[strawberry.ID]]


# Task provenance filters


@kante.filter_type(KoherentTask)
class TaskFilter(IdsFilterMixin, CreatedAtFilterMixin):
    task_id: Optional[FilterLookup[str]]
    parent_task_id: Optional[FilterLookup[str]]
    root_task_id: Optional[FilterLookup[str]]
    agent_client_id: Optional[FilterLookup[str]]
    issuer: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the assigner's subject ID")
    def assigner(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}assigner__sub": value})

    @kante.filter_field(description="Filter by the assigner's database user ID")
    def assigner_id(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match tasks assigned by the user with this database ID."""
        return Q(**{f"{prefix}assigner_id": value})

    @kante.filter_field(description="Search by task id or executing agent client id (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        """Match tasks whose task id or executing agent client id contains the given text."""
        return Q(**{f"{prefix}task_id__icontains": value}) | Q(**{f"{prefix}agent_client_id__icontains": value})


@kante.filter_type(models.CoordinateSystem)
class CoordinateSystemFilter(IdsFilterMixin, NameSearchFilterMixin, OwnedFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.CoordinateSystem, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]

    # `kind` is gone with ownership (RFC-9). What a space *is* follows from what lives in it,
    # and "nothing lives here" -- a pure reference frame, a world -- is the only distinction
    # the old four-value label was really carrying.
    @kante.filter_field(description="Filter to the spaces nothing lives in: pure reference frames, the worlds and atlases sources are registered into. False finds the spaces some data actually occupies")
    def uninhabited(self, info: Info, value: bool, prefix: str) -> Q:
        # One list, in `core.logic.graph.CONTAINERS`. A hand-written copy here answers
        # "nothing lives in this space" while something does.
        condition = Q(**{f"{prefix}{related_name}__isnull": True for related_name in graph_logic.RESIDENT_RELATIONS})
        return condition if value else ~condition

    @kante.filter_field(description="Filter to the spaces this dataset's data lives in: its own grid, and the grids of its pyramid levels and lenses")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}datasets__id": value}) | Q(**{f"{prefix}lenses__arraylens__dataset_id": value}) | Q(**{f"{prefix}data_arrays__dataset_id": value})

    @kante.filter_field(description="Filter by a scene composing over this system as its world")
    def scene(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}scenes__pk": value})


@kante.filter_type(models.Axis)
class AxisFilter(IdsFilterMixin):
    id: auto
    name: Optional[FilterLookup[str]]
    type: auto

    @kante.filter_field(description="Filter by the coordinate system this axis belongs to")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system_id": value})


@kante.filter_type(models.Transformation)
class TransformationFilter(IdsFilterMixin, OwnedFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.Transformation, queryset, value, prefix)

    id: auto
    kind: auto

    # Not `auto`: that would mint a second SDL enum from the TextChoices twin
    # (PlacementValidityChoices) beside the strawberry PlacementValidity every
    # other field uses.
    @kante.filter_field(description="Filter by how much the edge's map is actually known, e.g. UNKNOWN to list every placement that is still an assumption")
    def validity(self, info: Info, value: enums.PlacementValidity, prefix: str) -> Q:
        return Q(**{f"{prefix}validity": value.value})

    @kante.filter_field(description="Filter by the coordinate system this transformation maps from")
    def input(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}input_id": value})

    @kante.filter_field(description="Filter by the coordinate system this transformation maps to")
    def output(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}output_id": value})

    # There is deliberately no `scene` filter. One used to walk `scenes__id`, the membership
    # M2M RFC-6 deleted -- so it had been raising `FieldError` ever since, unnoticed because
    # nothing selects it. It has no honest replacement either: an edge is not a member of a
    # composition. An edge *into a space* is `output: <systemId>` above, and the field form
    # of that same question is `CoordinateSystem.registrations`.


@kante.filter_type(models.MeshCollection)
class MeshCollectionFilter(IdsFilterMixin, OwnedFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.MeshCollection, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Search by the collection's label: its name, or its store's key (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        """Match the collection by its name or, when it has none, by its store's key."""
        return Q(**{f"{prefix}name__icontains": value}) | Q(**{f"{prefix}store__key__icontains": value})

    @kante.filter_field(description="Filter by the folder this mesh collection is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match mesh collections filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match mesh collections filed in any of the given folders."""
        return Q(**{f"{prefix}folder_id__in": value})

    @kante.filter_field(description="Filter by the coordinate system the mesh geometry is expressed in (the collection's own)")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__id": value})

    @kante.filter_field(description="Filter by the dataset the meshes were extracted from, following the derivation edge")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__in": _systems_derived_from_dataset(value)})


@kante.filter_type(models.NetworkCollection)
class NetworkCollectionFilter(IdsFilterMixin, OwnedFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.NetworkCollection, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]

    @kante.filter_field(description="Search by the collection's label: its name, or its store's key (case-insensitive substring)")
    def search(self, info: Info, value: str, prefix: str) -> Q:
        """Match the collection by its name or, when it has none, by its store's key."""
        return Q(**{f"{prefix}name__icontains": value}) | Q(**{f"{prefix}store__key__icontains": value})

    @kante.filter_field(description="Filter by the folder this network collection is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match network collections filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match network collections filed in any of the given folders."""
        return Q(**{f"{prefix}folder_id__in": value})

    @kante.filter_field(description="Filter by the coordinate system the network geometry is expressed in (the collection's own)")
    def coordinate_system(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__id": value})

    @kante.filter_field(description="Filter by the dataset the networkes were extracted from, following the derivation edge")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__in": _systems_derived_from_dataset(value)})


@kante.filter_type(models.SparseDataset)
class SparseDatasetFilter(IdsFilterMixin, NameSearchFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.SparseDataset, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]
    description: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the folder this sparse dataset is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match sparse datasets filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter to datasets holding a layout indexed on this axis -- the ones that can answer about it in one contiguous read rather than by scanning")
    def indexes_axis(self, info: Info, value: str, prefix: str) -> Q:
        """Match sparse datasets whose stored layouts index an axis of this name."""
        return Q(**{f"{prefix}coordinate_system__axes__name": value})


@kante.filter_type(models.TableDataset)
class TableDatasetFilter(IdsFilterMixin, SemanticNameSearchFilterMixin, OwnedFilterMixin, CreatedThroughFilterMixin):
    @kante.filter_field(description=REQUIRES_DESCRIPTION)
    def requires(self, info: Info, queryset: QuerySet, value: list[DescriptorConstraintInput], prefix: str) -> tuple[QuerySet, Q]:
        return descriptor_filter.requires(models.TableDataset, queryset, value, prefix)

    id: auto
    name: Optional[FilterLookup[str]]
    description: Optional[FilterLookup[str]]

    @kante.filter_field(description="Filter by the folder this table dataset is filed in")
    def folder(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        """Match table datasets filed in the folder with this ID."""
        return Q(**{f"{prefix}folder_id": value})

    @kante.filter_field(description="Filter by a list of folder IDs")
    def folders(self, info: Info, value: list[strawberry.ID], prefix: str) -> Q:
        """Match table datasets filed in any of the given folders."""
        return Q(**{f"{prefix}folder_id__in": value})

    @kante.filter_field(description="Filter by whether the table was converted out of a file: true for the tables a converter read from an upload, false for those written straight in")
    def has_source_file(self, info: Info, value: bool, prefix: str) -> Q:
        """Match table datasets with, or without, a SOURCE file link."""
        return _link_exclusion(prefix, _containers_with_source_file("table_dataset"), negate=not value)

    @kante.filter_field(description="Filter by the dataset the table was computed from, following its derivation edge")
    def dataset(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}coordinate_system__in": _systems_derived_from_dataset(value)})

    # Materialized onto `stored_spec` at creation, as an array dataset's is, so JSONB
    # containment gives the all-of semantics directly: a stored list holds exactly one
    # placement member, so two of them together match nothing.
    @kante.filter_field(
        description="Filter to tables satisfying every one of these specs, e.g. [POINTS_3D, TRACKED] for 3D tracks, or [SERIES] for a value against a single measured coordinate, which a chart draws without an axis to choose. Materialized at creation from the declared columns: one placement member (by how many SPACE coordinate columns the table has) plus a modifier per declared fact. Declared, not measured -- TIMED says a TIME coordinate column exists. Two placement members together match nothing. `hasColumnRole` is the raw form"
    )
    def spec(self, info: Info, queryset: QuerySet, value: list[enums.TableDatasetSpec], prefix: str) -> tuple[QuerySet, Q]:
        if not value:
            return queryset, Q()
        return queryset, Q(**{f"{prefix}stored_spec__contains": [spec.value for spec in value]})

    @kante.filter_field(description="Filter to tables that declare a column of this role, e.g. TRACK_ID")
    def has_column_role(self, info: Info, value: enums.ColumnRole, prefix: str) -> Q:
        return Q(**{f"{prefix}columns__role": value.value})

    @kante.filter_field(description="Filter to table datasets placeable into this coordinate system: those whose own coordinate system reaches it across steps that compose into one affine map, walking the transformation edges. Takes a *space*, not a scene -- pass `scene.worldCoordinateSystem.id` to ask it of a scene")
    def placeable_in(self, info: Info, value: strawberry.ID, prefix: str) -> Q:
        space = _placeable_destination(info, value)
        if space is None:
            return Q(pk__in=[])
        return Q(**{f"{prefix}id__in": graph_logic.placeable_table_dataset_ids(space)})


def _placeable_destination(info: Info, value: strawberry.ID) -> "models.CoordinateSystem | None":
    """The space a `placeableIn` filter is asking about, or None when there is no such space here.

    Organization-scoped, unlike the scene lookup this replaced: the walk it feeds reads another
    org's edges otherwise, and while the rows that come back are scoped by the parent field
    anyway, "which of my datasets are placeable in your world" is still an answer this server
    has no reason to give. `None` rather than a raise, because a filter naming a space that is
    not there should return nothing, not fail the whole query.
    """
    return for_org(models.CoordinateSystem, info).filter(pk=value).first()


def _requires_rgb_capacity(axes: "Sequence[coords_logic.AxisSpec]", names: "Sequence[str]", shape: "Sequence[int]") -> bool:
    """Whether this lens has a channel axis with the three positions an RGB layer indexes.

    Structural capacity only. Whether those three channels *are* red, green and blue is a
    fact about the acquisition that no axis can carry, which is why RGB is never inferred
    and always stated -- see `enums.BootstrapLayerKind`. A picker offering this narrowing is
    answering "could I", not "should I".
    """
    intensity = coords_logic.resolve_render_axes(axes).intensity
    if intensity is None:
        return False
    return shape[names.index(intensity)] >= 3


def _requires_phasor_axis(axes: "Sequence[coords_logic.AxisSpec]", names: "Sequence[str]", shape: "Sequence[int]") -> bool:
    """Whether this lens carries a MICROTIME or SPECTRUM axis -- the batched form of `assert_phasor_axis`."""
    return coords_logic.resolve_render_axes(axes).phasor is not None


def _requires_vector_axis(axes: "Sequence[coords_logic.AxisSpec]", names: "Sequence[str]", shape: "Sequence[int]") -> bool:
    """Whether this lens carries a drawable DISPLACEMENT axis -- the batched form of `assert_vector_axis`.

    The same three conditions that mutation refuses on, asked as a predicate: the axis
    exists, it carries 2 or 3 components, and no more components than there are spatial
    axes to draw them along. A picker offering less than the mutation checks would offer
    a lens creation then refuses.
    """
    render = coords_logic.resolve_render_axes(axes)
    if render.vector is None or render.vector not in names:
        return False
    components = shape[names.index(render.vector)]
    spatial = 2 if render.z is None else 3
    return 2 <= components <= 3 and components <= spatial


#: What each lens-layer kind asks of a lens *beyond* renderability, which every member requires.
#: IMAGE, INTENSITY and LABEL are absent because they ask nothing extra: any drawable lens
#: yields a general or a single-channel layer, and LABEL's extra condition is about the
#: dataset's derivation rather than its axes, so it narrows `dataset_ids` earlier instead.
_LENS_KIND_REQUIREMENTS: "dict[enums.LayerKind, Callable[[Sequence[coords_logic.AxisSpec], Sequence[str], Sequence[int]], bool]]" = {
    enums.LayerKind.RGB: _requires_rgb_capacity,
    enums.LayerKind.PHASOR: _requires_phasor_axis,
    enums.LayerKind.VECTOR: _requires_vector_axis,
}


def _point_table_ids(table_ids: "set[int]", *, track: bool) -> set[int]:
    """Of these tables, the ones a point layer draws -- POINTS_2D or more -- and, for a track layer, the ones that are also TRACKED.

    The batched form of the gates `createPointLayer` and `createTrackLayer` apply, read off
    the same stored spec, so a picker never offers a table lens a layer mutation would refuse.
    """
    if not table_ids:
        return set()
    drawable = Q()
    for spec in coords_logic.POINT_TABLE_SPECS:
        drawable |= Q(stored_spec__contains=[spec.value, enums.TableDatasetSpec.TRACKED.value] if track else [spec.value])
    return set(models.TableDataset.objects.filter(drawable, pk__in=table_ids).values_list("pk", flat=True))


#: The collection layer kinds: which lens model answers, over which of the placeable containers.
_COLLECTION_LAYER_KINDS: "dict[enums.LayerKind, Callable[[dict[type, set[int]]], tuple[type, set[int]]]]" = {
    enums.LayerKind.POINT: lambda placeable: (models.TableLens, _point_table_ids(placeable[models.TableDataset], track=False)),
    enums.LayerKind.TRACK: lambda placeable: (models.TableLens, _point_table_ids(placeable[models.TableDataset], track=True)),
    enums.LayerKind.MESH: lambda placeable: (models.MeshLens, placeable[models.MeshCollection]),
    enums.LayerKind.NETWORK: lambda placeable: (models.NetworkLens, placeable[models.NetworkCollection]),
    enums.LayerKind.ANNOTATION: lambda placeable: (models.AnnotationLens, placeable[models.AnnotationCollection]),
}


def _renderable_lens_ids(dataset_ids: "set[int]", requires: "Callable[[Sequence[coords_logic.AxisSpec], Sequence[str], Sequence[int]], bool] | None" = None) -> set[int]:
    """Of these datasets' lenses, the ids of the ones that can actually be drawn.

    ``requires`` is an optional second gate applied to the same walk, for the kinds that ask
    something of the axes on top of renderability -- an RGB layer needs three channels to cut
    its components from, a phasor layer needs an axis to transform over.

    The batched form of the check `core.mutations.layer.assert_renderable` makes one lens at
    a time, so a picker never offers a lens creation would then refuse. Per *lens*, not per
    dataset, because a slice can crop x or y to a single column and that lens is undrawable
    while its dataset is fine. Loaded through `ArrayLens.objects.with_sources()`, which fetches
    every lens' dataset, axes and arrays in one query per relation, so the per-lens properties
    read nothing further.
    """
    if not dataset_ids:
        return set()
    renderable: set[int] = set()
    for lens in models.ArrayLens.objects.with_sources().filter(dataset_id__in=dataset_ids):
        axes, names = lens.axis_specs, lens.axis_names
        dataset_shape = lens.dataset.shape_list
        # A dataset whose axes and arrays disagree, or that has no array at all, has nothing to
        # draw -- and a picker is the wrong place to discover the mismatch.
        if len(names) != len(dataset_shape):
            continue
        shape = coords_logic.lens_shape(dataset_shape, names, lens.slices_list)
        if not coords_logic.is_renderable(axes, names, shape):
            continue
        if requires is not None and not requires(axes, names, shape):
            continue
        renderable.add(lens.pk)
    return renderable
def _annotate_once(queryset: QuerySet, alias: str, expression) -> QuerySet:
    """Add an annotation unless the alias is already taken by an identical one.

    An alias is global to the queryset while a filter is not: `AND`/`OR` recurse with
    the same prefix, so two branches may annotate one queryset. Django keeps the first
    annotation of a repeated alias and silently drops the second, so callers must name
    an alias for the *expression* it stands for -- then a repeat is the same question
    asked twice, and skipping it is right. Never call this with an alias whose
    expression can vary.
    """
    if alias in queryset.query.annotations:
        return queryset
    return queryset.annotate(**{alias: expression})


def _derived_dataset_ids(source_id: strawberry.ID | None = None):
    """The ids of every dataset that was derived, or only those derived from one source.

    `graph_logic.derivation_edges` expressed as a query, and it has to agree with it.
    An edge is a derivation when it leaves a space a dataset *lives in* (so
    `input__datasets` is what names the child, and a mesh or table collection's edge is
    excluded -- it does not set out from one) and lands in a space some *other* dataset's
    data lives in. The Coalesce is `graph_logic.system_dataset` in SQL: whichever resident
    the output space has is the dataset it came from.

    The self-exclusion is the load-bearing part. A level edge and a lens edge land in the
    dataset's own grid, which would otherwise make a dataset its own parent.
    `derivation_edges` drops them with `source.pk != dataset.pk`; the same test here
    compares two columns of the one row, so no subquery correlation is needed. A
    physical-space edge needs no exclusion any more -- it lands in a space *nothing* lives in,
    so the Coalesce is null and the `_source_dataset__isnull` filter drops it.

    Kind-blind, exactly as `derivation_edges` is: an UNMAPPABLE derivation is still a
    derivation, and it is the one machine-readable answer to why a dataset cannot be
    placed. Filtering it here would restore the silence that kind was invented to break.
    """
    source_dataset = Coalesce(
        "output__datasets__id",
        "output__lenses__arraylens__dataset_id",
        "output__data_arrays__dataset_id",
    )
    # The other containers a derivation may land in: a table, a sparse dataset, a mesh, a
    # network, an annotation collection, each keyed to itself as `container_map` keys it.
    # Any of them is another container than the child dataset, so no self-exclusion applies.
    source_collection = Coalesce(
        "output__table_datasets__id",
        "output__sparse_datasets__id",
        "output__mesh_collections__id",
        "output__network_collections__id",
        "output__annotation_collections__id",
    )
    edges = (
        models.Transformation.objects.filter(input__datasets__isnull=False)
        .exclude(kind=enums.TransformKindChoices.FIELD.value)  # a FIELD is a lookup, never a derivation (`is_derivation_edge`)
        .annotate(_source_dataset=source_dataset, _source_collection=source_collection)
    )
    if source_id is not None:
        edges = edges.filter(_source_dataset=source_id).exclude(_source_dataset=F("input__datasets__id"))
    else:
        edges = edges.filter((Q(_source_dataset__isnull=False) & ~Q(_source_dataset=F("input__datasets__id"))) | Q(_source_collection__isnull=False))
    return edges.values_list("input__datasets__id", flat=True)


def _annotate_axis_type_count(queryset: QuerySet, prefix: str, types: set[str]) -> tuple[QuerySet, str]:
    """Annotate how many of `types` a dataset's intrinsic axes match, and return the alias to compare against.

    Counts the distinct axis *types* matched, which is how an all-of test is written:
    it equals `len(types)` exactly when every requested type is present.

    The alias names the expression, because an alias is global to the queryset while
    a filter is not: `AND`/`OR` recurse with the same prefix, so two branches can
    annotate one queryset. Two branches asking the same question then share the one
    annotation (identical expression, so the guard skips the second), and two asking
    different questions get different aliases instead of one silently shadowing the
    other. The Count is distinct because another filter may join in rows that
    multiply these out.
    """
    axes = f"{prefix}coordinate_system__axes"
    alias = f"_{prefix.replace('__', '_')}matched_axis_types__{'_'.join(sorted(types))}"
    expression = Count(f"{axes}__type", filter=Q(**{f"{axes}__type__in": list(types)}), distinct=True)
    return _annotate_once(queryset, alias, expression), alias


def _systems_derived_from_dataset(dataset_id: strawberry.ID):
    """The collection systems whose derivation edge lands in this dataset.

    A subquery rather than a join: `Transformation.input`/`output` are declared
    `related_name="+"`, so there is no reverse accessor to filter across, and a collection
    keeps no dataset column of its own -- the edge is the only place that fact lives, and
    duplicating it onto the collection is the copy this whole graph exists to avoid.
    """
    return models.Transformation.objects.filter(
        input__isnull=False,
    ).filter(
        Q(output__datasets__id=dataset_id) | Q(output__lenses__arraylens__dataset_id=dataset_id) | Q(output__data_arrays__dataset_id=dataset_id)
    ).values("input_id")

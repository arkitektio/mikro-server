"""The options query: what a layer may be coloured or filtered by.

Rooted on the **source** -- a mesh collection, or the lens a label layer renders -- rather than
on a coordinate system as ``attributePlans`` is, because the question is asked while looking at
one of those and the space is an implementation detail of the answer. That is also the difference in kind between the two queries: a plan is an instruction to
execute per hover, an option is a choice to author once. They walk related ground and are not
substitutes -- see :mod:`core.logic.column_options` for what `attributePlans` includes that this
must not, and excludes that this must.
"""

import strawberry
from kante.types import Info
from strawberry_django.pagination import OffsetPaginationInput

from core import filters as core_filters
from core import enums, models, types
from core.logic import column_options as column_options_logic
from core.scoping import get_for_org
from core.utils import paginate_list


def _matches(spec: column_options_logic.ColumnOptionSpec, filters: "core_filters.ColumnOptionFilter") -> bool:
    """Whether one option survives the narrowings the caller asked for."""
    if filters.direct_only and spec.join_path:
        return False
    # The terminal table, deliberately: an option is where its *value* is read, and a table the
    # path merely passes through is not where anything is read from. Documented on the field,
    # because "involves this table" is the other plausible reading and the two disagree exactly
    # on the hopped options.
    if filters.table is not None and (spec.table is None or str(spec.table.pk) != str(filters.table)):
        return False
    # A role is a property of a column, and a slice of a matrix has none -- so narrowing by role
    # excludes the sparse half rather than matching all of it. Filtering for what an option does
    # not have should return fewer options, never more.
    if filters.roles is not None and (spec.column is None or spec.column.role not in {role.value for role in filters.roles}):
        return False
    if filters.controls is not None:
        control = enums.ColumnControl.MEASURE if spec.is_measure else enums.ColumnControl.CATEGORICAL
        if control.value not in {choice.value for choice in filters.controls}:
            return False
    if filters.search:
        needle = filters.search.strip().casefold()
        if spec.is_graph:
            parts = (spec.graph_attribute,)
        elif spec.is_sparse:
            parts = (*spec.axes, spec.sparse_dataset.name)
        else:
            parts = (spec.column.name, spec.column.long_name, spec.table.name)
        haystack = " ".join(part for part in parts if part)
        if needle and needle not in haystack.casefold():
            return False
    return True


def _options(
    info: Info,
    system: "models.CoordinateSystem",
    filters: "core_filters.ColumnOptionFilter | None",
    pagination: OffsetPaginationInput | None,
    max_join_depth: int,
) -> list[column_options_logic.ColumnOptionSpec]:
    """The candidates, narrowed and paged -- everything the four queries share, which is everything.

    They differ only in where the walk starts and in the name of the type it projects into, the
    way `LabelColorBy` and `MeshColorBy` differ: a picker authoring a rule should not have to
    read a type named for colouring, and the set behind both is one walk and one answer. Rooted
    on the **system** for the same reason the write path is -- a mask and a mesh collection are
    the same thing to a FIELD edge.
    """
    specs = column_options_logic.build_column_options(
        system,
        info.context.request.organization,
        max_join_depth=max_join_depth,
    )
    return _narrow(specs, filters, pagination)


def _narrow(
    specs: "list[column_options_logic.ColumnOptionSpec]",
    filters: "core_filters.ColumnOptionFilter | None",
    pagination: OffsetPaginationInput | None,
) -> list[column_options_logic.ColumnOptionSpec]:
    """The narrowing and paging every candidate list goes through, whatever built it."""
    if filters is not None:
        specs = [spec for spec in specs if _matches(spec, filters)]

    if pagination is not None:
        specs = paginate_list(specs, offset=pagination.offset or 0, limit=pagination.limit)

    return specs


def _network_options(
    info: Info,
    network_collection: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None",
    pagination: OffsetPaginationInput | None,
    max_join_depth: int,
) -> list[column_options_logic.ColumnOptionSpec]:
    """The network candidates: graph attributes first, then the depth-zero-rooted walk.

    Rooted on the collection rather than on its system, because half the answer -- the graph
    attributes -- lives on the collection's store and no system knows it. The other half is
    the shared walk, restricted for the reason `build_network_column_options` states.
    """
    collection = get_for_org(models.NetworkCollection, info, id=network_collection)
    specs = column_options_logic.build_network_column_options(
        collection,
        info.context.request.organization,
        max_join_depth=max_join_depth,
    )
    return _narrow(specs, filters, pagination)


def _control(spec: column_options_logic.ColumnOptionSpec) -> "enums.ColumnControl":
    """Which control a column admits, from the one frozenset the write path enforces."""
    return enums.ColumnControl.MEASURE if spec.is_measure else enums.ColumnControl.CATEGORICAL


def _join_path(spec: column_options_logic.ColumnOptionSpec) -> "list[types.ColumnOptionJoinStep]":
    """The hops that reach this column, as the client will pass them back."""
    return [types.ColumnOptionJoinStep(table=table, column=column) for table, column in spec.join_path]


def _mesh_system(info: Info, mesh_collection: strawberry.ID) -> "models.CoordinateSystem":
    """The space a collection's FIELD edges leave from, scoped to the caller's org."""
    return column_options_logic.mesh_collection_system(get_for_org(models.MeshCollection, info, id=mesh_collection))


def _lens_system(info: Info, lens: strawberry.ID) -> "models.CoordinateSystem":
    """The space a mask's pixels live in, scoped to the caller's org."""
    return column_options_logic.lens_source_system(get_for_org(models.Lens, info, id=lens))


def _color_by_options(specs: "list[column_options_logic.ColumnOptionSpec]") -> "list[types.ColorByOption]":
    """Project the candidates into the colour picker's type."""
    return [
        types.ColorByOption(
            table=spec.table,
            column=spec.column,
            sparse_dataset=spec.sparse_dataset,
            axes=list(spec.axes),
            graph_attribute=spec.graph_attribute,
            target=spec.target,
            control=_control(spec),
            join_path=_join_path(spec),
        )
        for spec in specs
    ]


def _filter_by_options(specs: "list[column_options_logic.ColumnOptionSpec]") -> "list[types.FilterByOption]":
    """Project the same candidates into the filter picker's type."""
    return [
        types.FilterByOption(
            table=spec.table,
            column=spec.column,
            sparse_dataset=spec.sparse_dataset,
            axes=list(spec.axes),
            graph_attribute=spec.graph_attribute,
            target=spec.target,
            control=_control(spec),
            join_path=_join_path(spec),
        )
        for spec in specs
    ]


def color_by_options(
    info: Info,
    mesh_collection: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.ColorByOption]:
    """Every column this collection's objects can be coloured or filtered by.

    The set is exactly what `createMeshLayer(colorBys:)` and `filterBys` accept: it is built from
    the same reachability walk the mutation validates against, and its `control` comes from the
    same frozenset the mutation enforces. An options query that drifts from its write path is
    worse than none -- it teaches a picker to offer refusals.

    What it deliberately does not carry is the columns' *values*. A picker wanting the classes of
    a categorical column, or the range of a measured one, reads them from the parquet itself: it
    holds an `accessGrant` for that store and is reading the table anyway, so a scan here would
    duplicate a query the client can make locally at a cost this server cannot bound.
    """
    return _color_by_options(_options(info, _mesh_system(info, mesh_collection), filters, pagination, max_join_depth))


def filter_by_options(
    info: Info,
    mesh_collection: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.FilterByOption]:
    """Every column this collection's objects can be filtered by.

    The same candidates `colorByOptions` returns, and deliberately so: a colouring and a rule
    read the same column through the same join and branch on the same role, so offering two
    different sets would mean one of them was wrong. What differs is the prose -- MEASURE means
    a `min`/`max` bound here and a colormap there -- which is why it is a second name rather
    than a second walk.
    """
    return _filter_by_options(_options(info, _mesh_system(info, mesh_collection), filters, pagination, max_join_depth))


def network_color_by_options(
    info: Info,
    network_collection: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.ColorByOption]:
    """Everything a network layer over this collection can be coloured or filtered by.

    Two halves, one list, and the set is exactly what `createNetworkLayer(colorBys:)` accepts.
    The GRAPH half comes first: the per-node values the collection itself carries -- the names
    its manifest declares, plus `radius` when the encoding carries one -- offered from the same
    vocabulary the mutation validates against. The COLUMN/SPARSE half is the shared walk,
    rooted at depth zero: only FIELD edges standing on the collection's own system are offered,
    because the wider fact walk reaches the image the network was traced from and its tables
    are keyed by mask-instance ids -- joins an object id cannot execute, so offering them
    would teach the picker refusals.
    """
    return _color_by_options(_network_options(info, network_collection, filters, pagination, max_join_depth))


def network_filter_by_options(
    info: Info,
    network_collection: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.FilterByOption]:
    """Everything a network layer over this collection can be filtered by.

    The same candidates `networkColorByOptions` returns, under the filter picker's name -- the
    second-name-not-second-walk that pairs every other options query. A GRAPH option here is a
    per-node rule: bounds over Strahler order, degree or a radius hide individual nodes and
    segments, where a COLUMN or SPARSE rule keeps or drops whole objects.
    """
    return _filter_by_options(_network_options(info, network_collection, filters, pagination, max_join_depth))


def label_color_by_options(
    info: Info,
    lens: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.ColorByOption]:
    """Every column a mask's objects can be coloured or filtered by.

    `colorByOptions` rooted on a lens instead of a collection, and the same answer for the same
    reason: a mask's pixel values dereference into a table by exactly the FIELD edge a
    collection's ids do, so the walk, the measure-vs-categorical rule and the invariant are one.
    The set this returns is exactly what `createLabelLayer(render: {colorBys: ...})` accepts.

    Rooted on the **lens**, not the mask dataset, because that is what a label layer is created
    from and what the boundary resolves the system through -- asking with the dataset would mean
    the picker and the mutation could disagree about which space is in play.
    """
    return _color_by_options(_options(info, _lens_system(info, lens), filters, pagination, max_join_depth))


def label_filter_by_options(
    info: Info,
    lens: strawberry.ID,
    filters: "core_filters.ColumnOptionFilter | None" = None,
    pagination: OffsetPaginationInput | None = None,
    max_join_depth: int = 1,
) -> list[types.FilterByOption]:
    """Every column a mask's objects can be filtered by.

    The same candidates `labelColorByOptions` returns, under the name that reads right where a
    rule is being authored -- the same second-name-not-second-walk that pairs `filterByOptions`
    with `colorByOptions` over a collection.
    """
    return _filter_by_options(_options(info, _lens_system(info, lens), filters, pagination, max_join_depth))

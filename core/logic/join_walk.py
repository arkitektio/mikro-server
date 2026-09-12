"""The record-land walk: every hop a plan's landing can take through the schema, tables and matrices alike.

A FIELD edge is the single crossing from geometry into record-land, and the coordinate graph
stops there -- *"tables are leaves"* (:mod:`core.logic.attribute_plans`). What relates one record
container to another is never an edge but a **schema fact**, and there are exactly two:

* ``Column.references`` -- *this column's values identify rows of that table*;
* ``SparseAxisReference`` -- *positions along this matrix axis identify rows of that table*, which
  read backwards is *that table's row ids are positions along this axis*, and so a table can hop
  **into** a matrix wherever a layout indexes that axis (:meth:`SparseDataset.array_indexing`).

This module walks those facts breadth-first from a plan's landing and hands back one hop per
reachable container, each saying which fact it crossed, what the worker holds by then (one value
or a set of them), and where it lands. Nothing is executed and nothing is read: a hop is a recipe,
exactly as the landing it extends is, and the client runs them in order
(``docs/attribute-plans-api.md``). RFC-7's non-goal was *server-side* chaining; this is the server
*describing* the chain, which is what it already did for one step.

The walk is fenced the way the picker's is (:mod:`core.logic.column_options`, whose reference walk
this generalises): depth is bounded by :data:`MAX_JOIN_DEPTH`, and cycles are cut **per branch**
with the landing itself in the visited set -- without that, a matrix that hops to its feature table
would hop straight back into itself one level later. Batched per level, never per node, because a
schema can fan out and the cost must grow with the depth a caller asked for rather than with the
size of someone's warehouse.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal

from django.db.models import Prefetch

from core import enums, models

if TYPE_CHECKING:
    from authentikate.models import Organization

#: How many hops a chain may take. A bound, not a judgement: the schema graph can cycle and can
#: fan out, and an unbounded walk over someone's warehouse is a denial of service with a friendly
#: name. Four is far past any chain anyone has asked for. Shared with the picker's `joinPath`
#: validation, which is the other place a chain is bounded.
MAX_JOIN_DEPTH = 4

Cardinality = Literal["ONE", "MANY"]

#: A node of the walk: which kind of container, and which row. Two kinds because a table and a
#: matrix are hopped *from* differently (columns vs axes) and hopped *into* differently (a row vs
#: a slice); the key is what the per-branch cycle cut compares.
NodeKey = tuple[str, int]


@dataclass(frozen=True)
class JoinRoot:
    """Where one walk starts: a plan's landing, and for a matrix the axis it was landed on.

    ``arrived_axis`` matters for a matrix because the axis the id bound is not one to hop along
    -- its reference is the table the ids came *through*, and enumerating it would be a hop back
    to where the worker already stands.
    """

    table: "models.TableDataset | None" = None
    sparse_dataset: "models.SparseDataset | None" = None
    arrived_axis: str | None = None

    @property
    def key(self) -> NodeKey:
        """The node this root is."""
        return ("T", self.table.pk) if self.table is not None else ("S", self.sparse_dataset.pk)

    @property
    def cardinality(self) -> Cardinality:
        """What a hop out of this landing binds: one row's value, or every position of a slice."""
        return "ONE" if self.table is not None else "MANY"


@dataclass(frozen=True)
class JoinHop:
    """One hop of one root's chain, as the walk found it. The plan builder turns it into a lookup.

    ``parent`` is the hop index this binds from, ``0`` being the landing. ``via_column`` is set
    for a ``Column.references`` hop (the column whose values are bound) and for a table->matrix
    hop (the table's INDEX column, whose values are the positions); ``via_axis`` is set for a
    matrix axis crossed in either direction. The **held name** a lookup binds under is always
    the via's name -- the column's for a column, the axis' for an axis -- so a worker reads the
    value off the parent's result by exactly the name the hop states.
    """

    root: int
    index: int
    parent: int
    depth: int
    cardinality: Cardinality
    via_column: "models.Column | None" = None
    via_axis: str | None = None
    table: "models.TableDataset | None" = None
    sparse_dataset: "models.SparseDataset | None" = None
    # (matrix targets) the layout indexing the axis the held values bind, and the axes that come back.
    sparse_array: "models.SparseArray | None" = None
    key_axis: str | None = None
    value_axes: tuple[str, ...] = ()
    # (table targets) the INDEX column the held values bind, and what to select.
    key_column: "models.Column | None" = None
    attributes: tuple["models.Column", ...] = ()
    # The picker's identity for a pure table->table chain from the landing table; empty once a
    # matrix has been crossed, because no `joinPath` can name that.
    join_path: tuple[tuple["models.TableDataset", "models.Column"], ...] = ()

    @property
    def held(self) -> str:
        """The name the worker holds the bound value(s) under: the via's own name."""
        return self.via_column.name if self.via_column is not None else str(self.via_axis)


@dataclass
class _Frontier:
    """One branch tip: where it stands, how it got there, and what it must not revisit."""

    root: int
    key: NodeKey
    parent: int
    cardinality: Cardinality
    visited: frozenset[NodeKey]
    join_path: tuple[tuple["models.TableDataset", "models.Column"], ...]
    crossed_matrix: bool
    # For a matrix node: the axis the branch arrived on, never hopped along again.
    arrived_axis: str | None = None
    # The position of this tip among its siblings, for the level's stable order.
    order: tuple = ()


def load_tables(ids: Iterable[int], organization: "Organization") -> dict[int, "models.TableDataset"]:
    """One query for a whole level of tables, columns and their reference targets attached.

    Scoped to the organization even for hop targets. A reference is authored through
    ``get_for_org`` so a cross-org target should not exist, but "should not exist" is not a
    guarantee a read path gets to lean on.
    """
    identifiers = list(ids)
    if not identifiers:
        return {}
    tables = (
        models.TableDataset.objects.filter(pk__in=identifiers, organization=organization)
        .select_related("store")
        .prefetch_related(Prefetch("columns", queryset=models.Column.objects.select_related("references").order_by("order")))
    )
    return {table.pk: table for table in tables}


def load_sparse_datasets(ids: Iterable[int], organization: "Organization") -> dict[int, "models.SparseDataset"]:
    """One query for a level of matrices: axes, layouts and axis references attached."""
    identifiers = list(ids)
    if not identifiers:
        return {}
    datasets = models.SparseDataset.objects.filter(pk__in=identifiers, organization=organization).prefetch_related(
        "coordinate_system__axes",
        "arrays__store",
        Prefetch("axis_references", queryset=models.SparseAxisReference.objects.select_related("references").order_by("pk")),
    )
    return {dataset.pk: dataset for dataset in datasets}


def _reverse_references(table_ids: Iterable[int], organization: "Organization") -> dict[int, list["models.SparseAxisReference"]]:
    """The matrix axes whose positions are rows of each table -- the table->matrix door, one query."""
    identifiers = list(table_ids)
    if not identifiers:
        return {}
    by_table: dict[int, list] = {}
    references = (
        models.SparseAxisReference.objects.filter(references_id__in=identifiers, dataset__organization=organization)
        .select_related("dataset")
        .order_by("dataset_id", "pk")
    )
    for reference in references:
        by_table.setdefault(reference.references_id, []).append(reference)
    return by_table


def _index_column(table: "models.TableDataset") -> "models.Column | None":
    """The one INDEX coordinate column a value is looked up in, or None if the table is not that shape.

    A hop target is single-INDEX-keyed by construction -- ``resolve_reference_target`` refuses a
    reference to anything else at write time -- so this is defensive rather than a rule of its
    own; a target that somehow is not that shape simply gets no hop.
    """
    coordinates = [column for column in table.columns.all() if column.role == enums.ColumnRoleChoices.COORDINATE.value]
    if len(coordinates) != 1 or coordinates[0].axis_type != enums.AxisTypeChoices.INDEX.value:
        return None
    return coordinates[0]


def _attributes(table: "models.TableDataset") -> tuple["models.Column", ...]:
    """Every non-coordinate column: what a lookup selects. See ``build_attribute_plans`` for why all of them."""
    return tuple(column for column in table.columns.all() if column.role != enums.ColumnRoleChoices.COORDINATE.value)


def walk_joins(
    roots: "list[JoinRoot]",
    organization: "Organization",
    *,
    max_join_depth: int,
    cross_matrices: bool = True,
) -> dict[int, list[JoinHop]]:
    """Every hop reachable from each root, keyed by root position, in a stable breadth-first order.

    Depth is clamped to :data:`MAX_JOIN_DEPTH`; ``0`` runs no query at all. Within a root, hops
    are numbered from ``1`` (``0`` is the landing) in level order, and within a level by the
    parent hop, then the via's declared order (a column's ``order``, an axis' position), then the
    target -- so the list is the same on every call, which is what lets a client cache it.

    ``cross_matrices=False`` confines the walk to ``Column.references`` -- the picker's walk,
    which cannot store a position along an axis.
    """
    depth_limit = max(0, min(max_join_depth, MAX_JOIN_DEPTH))
    hops: dict[int, list[JoinHop]] = {position: [] for position in range(len(roots))}
    if depth_limit == 0 or not roots:
        return hops

    # The roots reloaded with everything the first expansion needs, rather than widening the
    # FIELD walk that found them: that walk is shared with every picker and must stay cheap.
    tables = load_tables((root.table.pk for root in roots if root.table is not None), organization)
    matrices = load_sparse_datasets((root.sparse_dataset.pk for root in roots if root.sparse_dataset is not None), organization)

    frontier = [
        _Frontier(root=position, key=root.key, parent=0, cardinality=root.cardinality, visited=frozenset({root.key}), join_path=(), crossed_matrix=root.sparse_dataset is not None, arrived_axis=root.arrived_axis, order=(position,))
        for position, root in enumerate(roots)
    ]
    counters = {position: 1 for position in range(len(roots))}

    for depth in range(1, depth_limit + 1):
        if not frontier:
            break

        reverse = _reverse_references((tip.key[1] for tip in frontier if tip.key[0] == "T"), organization) if cross_matrices else {}

        # First pass: what each tip could hop to, before any target is loaded, so the targets of
        # a whole level are fetched in two queries rather than one per hop.
        candidates: list[tuple[tuple, _Frontier, "models.Column | None", str | None, NodeKey]] = []
        for tip in frontier:
            kind, pk = tip.key
            if kind == "T":
                table = tables[pk]
                for column in table.columns.all():
                    if column.references_id is None or column.role == enums.ColumnRoleChoices.COORDINATE.value:
                        continue  # a COORDINATE reference is the product-space enumeration, not a hop
                    candidates.append((tip.order + (column.order, 0), tip, column, None, ("T", column.references_id)))
                for reference in reverse.get(pk, []):
                    index_column = _index_column(table)
                    if index_column is None:
                        continue
                    candidates.append((tip.order + (index_column.order, 1, reference.dataset_id), tip, index_column, reference.axis, ("S", reference.dataset_id)))
            else:
                matrix = matrices[pk]
                names = matrix.axis_names
                for reference in matrix.axis_references.all():
                    if reference.axis == tip.arrived_axis or reference.axis not in names:
                        continue  # the axis the branch came in on leads back where it came from
                    candidates.append((tip.order + (names.index(reference.axis),), tip, None, reference.axis, ("T", reference.references_id)))

        candidates = [entry for entry in candidates if entry[4] not in entry[1].visited]
        candidates.sort(key=lambda entry: (entry[0], entry[4]))

        # Second pass: load every target of the level at once, then build the hops in order.
        tables.update(load_tables({key[1] for *_, key in candidates if key[0] == "T" and key[1] not in tables}, organization))
        matrices.update(load_sparse_datasets({key[1] for *_, key in candidates if key[0] == "S" and key[1] not in matrices}, organization))

        next_frontier: list[_Frontier] = []
        for order, tip, via_column, via_axis, key in candidates:
            kind, pk = key
            if kind == "T":
                target = tables.get(pk)
                key_column = _index_column(target) if target is not None else None
                if target is None or key_column is None:
                    continue
                cardinality: Cardinality = "MANY" if tip.key[0] == "S" else tip.cardinality
                crossed = tip.crossed_matrix or via_axis is not None
                join_path = () if crossed else tip.join_path + ((tables[tip.key[1]], via_column),)
                hop = JoinHop(
                    root=tip.root, index=counters[tip.root], parent=tip.parent, depth=depth, cardinality=cardinality,
                    via_column=via_column, via_axis=via_axis, table=target, key_column=key_column, attributes=_attributes(target), join_path=join_path,
                )
                arrived = None
            else:
                target = matrices.get(pk)
                if target is None:
                    continue
                layout = target.array_indexing(str(via_axis))
                if layout is None:
                    continue  # from the other layout the same read is a scan of every byte: no hop rather than a slow one
                names = target.axis_names
                hop = JoinHop(
                    root=tip.root, index=counters[tip.root], parent=tip.parent, depth=depth, cardinality=tip.cardinality,
                    via_column=via_column, via_axis=via_axis, sparse_dataset=target, sparse_array=layout, key_axis=via_axis,
                    value_axes=tuple(name for name in names if name != via_axis),
                )
                crossed = True
                arrived = via_axis
            counters[tip.root] += 1
            hops[tip.root].append(hop)
            next_frontier.append(
                _Frontier(root=tip.root, key=key, parent=hop.index, cardinality=hop.cardinality, visited=tip.visited | {key}, join_path=hop.join_path, crossed_matrix=crossed, arrived_axis=arrived, order=order)
            )

        frontier = next_frontier

    return hops

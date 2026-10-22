"""The directed lineage walk: from one container or file, what it came from and what came of it.

The sibling of :func:`core.logic.graph.lineage_graph`, which answers a different question.
That one hands back the whole undirected *component* -- standing on a derived dataset it
climbs to the raw image and comes back down every other branch of it -- one ORM round trip per
generation, and it knows only derivation edges. This one is what a lineage *viewer* asks:

- **Directed.** ``up`` hops of ancestors and ``down`` hops of descendants, never a sideways
  turn, so a node's siblings are only there once someone expands its parent.
- **Files are nodes.** A :class:`~core.models.FileLink` is the edge between a file and the
  container it was ingested into (SOURCE) or exported as (RENDITION). It is no part of the
  coordinate graph, which is why ``lineage_graph`` cannot see it.
- **One recursive CTE per direction**, so the round trips do not grow with depth.

**The recursion is index-driven from the frontier.** Every step starts at a frontier node and
reaches only through indexed foreign keys (container -> its systems -> ``Transformation`` by
``input_id``/``output_id`` -> the other end's container by ``coordinate_system_id``). Nothing
org-wide is ever materialized, so the walk costs what the lineage costs, whatever else the
organization holds. The outer ``LIMIT`` carries no ``ORDER BY``/``DISTINCT``, so Postgres stops
evaluating the recursion once it has enough rows rather than walking everything and
discarding it.

**Generated, never hand-written.** Every container fragment is built from
:data:`~core.logic.graph.CONTAINERS` and :data:`~core.logic.graph._KEYING_ORDER`, so the SQL
cannot drift from :func:`~core.logic.graph.container_map` and
:func:`~core.logic.graph.is_derivation_edge`; ``tests/test_lineage.py`` pins the parity.
"""

from __future__ import annotations

import dataclasses
import json
from functools import cache

from django.db import connection

from core import enums, models
from core.logic.graph import _KEYING_ORDER, CONTAINERS, MODEL_BY_KEY
from core.models.array_dataset import selection_label
from core.scoping import organization_path

#: The file's node key. A file has no coordinate system and keys to no container, so it is
#: named here rather than in ``CONTAINERS``.
FILE = "file"

#: How a hop was made: a derivation edge or a file link. Short tags, since they ride every
#: row of the recursion.
VIA_TRANSFORMATION = "T"
VIA_FILE_LINK = "F"

MAX_DEPTH = 10
MAX_LIMIT = 1000
MAX_FANOUT = 200

#: Lenses listed per node. The whole lens and every lens an edge leaves from are always
#: listed; the rest fill up to this, oldest first. A dataset an agent has cut a crop out of
#: per annotation can hold hundreds, and a tile is not the place to list them.
MAX_LENSES = 16


def _q(name: str) -> str:
    return connection.ops.quote_name(name)


def _column(model: type, field: str) -> str:
    return model._meta.get_field(field).column


@dataclasses.dataclass(frozen=True)
class _Source:
    """One container table, as the SQL reads it: where its rows are, which space each lives in, whose node each is."""

    key: str
    #: FROM clause, aliased ``x`` (and ``l`` for a lens' base row).
    from_sql: str
    system: str
    root: str
    row_pk: str
    #: Position in ``_KEYING_ORDER``: a higher one wins a shared space.
    priority: int


def _source(container, priority: int) -> _Source:
    model = container.model
    table = _q(model._meta.db_table)
    if issubclass(model, models.Lens):
        # Multi-table inheritance: the space is on the base row, the container FK on the child.
        base = _q(models.Lens._meta.db_table)
        ptr = _q(model._meta.pk.column)
        root = _q(_column(model, container.root_field.removesuffix("_id")))
        return _Source(
            key=container.key,
            from_sql=f"{base} l JOIN {table} x ON x.{ptr} = l.{_q('id')}",
            system=f"l.{_q(_column(models.Lens, 'coordinate_system'))}",
            root=f"x.{root}",
            row_pk=f"l.{_q('id')}",
            priority=priority,
        )
    pk = _q(model._meta.pk.column)
    root = f"x.{pk}" if container.root_field == "pk" else f"x.{_q(_column(model, container.root_field.removesuffix('_id')))}"
    return _Source(
        key=container.key,
        from_sql=f"{table} x",
        system=f"x.{_q(_column(model, 'coordinate_system'))}",
        root=root,
        row_pk=f"x.{pk}",
        priority=priority,
    )


@cache
def _sources() -> tuple[_Source, ...]:
    return tuple(_source(container, priority) for priority, container in enumerate(_KEYING_ORDER))


@cache
def _file_link_columns() -> tuple[tuple[str, str], ...]:
    """``(container key, FileLink column)`` for every container a file can be linked to."""
    key_by_model = {model: key for key, model in MODEL_BY_KEY.items()}
    return tuple(
        (key_by_model[field.related_model], _q(field.column))
        for field in models.FileLink._meta.fields
        if field.is_relation and field.related_model in key_by_model
    )


def _key_of(system_expr: str) -> str:
    """The container a space keys to: :func:`container_map` for one space, as a LATERAL subquery.

    The dataset wins a space it lives in; otherwise the container latest in ``_KEYING_ORDER``
    does, and inside one container the lowest row -- the rule ``container_map`` gets by
    iterating in that order with later writes winning and rows written highest pk first.
    No row is a world (``("system", pk)``), and the join drops it.
    """
    arms = " UNION ALL ".join(
        f"SELECT '{s.key}'::text AS kind, {s.root}::bigint AS pk, {s.priority} AS prio, {s.row_pk}::bigint AS row_pk "
        f"FROM {s.from_sql} WHERE {s.system} = {system_expr}"
        for s in _sources()
    )
    return f"SELECT k.kind, k.pk FROM ({arms}) k ORDER BY k.prio DESC, k.row_pk ASC LIMIT 1"


def _systems_of(kind: str, pk: str) -> str:
    """Every space a container's own data lives in: the systems ``container_q`` matches for its key."""
    return " UNION ALL ".join(f"SELECT {s.system} FROM {s.from_sql} WHERE {kind} = '{s.key}' AND {s.root} = {pk}" for s in _sources())


def _own_system_of(kind: str, pk: str) -> str:
    """The one space a container's own row lives in -- where its derivation edges start, as ``derivation_edges`` reads them."""
    arms = []
    for key, model in MODEL_BY_KEY.items():
        arms.append(
            f"SELECT x.{_q(_column(model, 'coordinate_system'))} FROM {_q(model._meta.db_table)} x WHERE {kind} = '{key}' AND x.{_q(model._meta.pk.column)} = {pk}"
        )
    return " UNION ALL ".join(arms)


def _not_drawing_surface(kind: str, pk: str) -> str:
    """:func:`~core.logic.graph.drawing_surfaces` as a guard: this node is not a scene's drawing collection.

    Such a collection is drawn, not computed, so it is never a lineage child -- see
    :func:`~core.logic.graph.is_lineage_edge`, which the reporters use for the same rule.
    """
    model = models.AnnotationCollection
    key = next(container.key for container in CONTAINERS if container.model is model)
    return (
        f"NOT EXISTS (SELECT 1 FROM {_q(model._meta.db_table)} ac WHERE {kind} = '{key}' "
        f"AND ac.{_q(model._meta.pk.column)} = {pk} AND ac.{_q(_column(model, 'scene'))} IS NOT NULL)"
    )


_T = _q(models.Transformation._meta.db_table)
_FL = _q(models.FileLink._meta.db_table)


def _transformation_columns() -> dict[str, str]:
    return {name: _q(_column(models.Transformation, name)) for name in ("input", "output", "kind", "organization")}


def _file_link_arm(n: str, *, from_file: bool, direction: str) -> list[str]:
    """The file-link hops out of ``n``: from a file to its containers, or from a container to its files."""
    file_col = _q(_column(models.FileLink, "file"))
    org = _q(_column(models.FileLink, "organization"))
    dir_col = _q(_column(models.FileLink, "direction"))
    arms = []
    for key, column in _file_link_columns():
        if from_file:
            arms.append(
                f"SELECT '{key}'::text, fl.{column}::bigint, '{VIA_FILE_LINK}'::text, fl.{_q('id')}::bigint FROM {_FL} fl "
                f"WHERE {n}.kind = '{FILE}' AND fl.{file_col} = {n}.pk AND fl.{column} IS NOT NULL AND fl.{dir_col} = '{direction}' AND fl.{org} = %(org)s"
            )
        else:
            arms.append(
                f"SELECT '{FILE}'::text, fl.{file_col}::bigint, '{VIA_FILE_LINK}'::text, fl.{_q('id')}::bigint FROM {_FL} fl "
                f"WHERE {n}.kind = '{key}' AND fl.{column} = {n}.pk AND fl.{dir_col} = '{direction}' AND fl.{org} = %(org)s"
            )
    return arms


def _step(n: str, *, down: bool) -> str:
    """Every hop out of the frontier row ``n`` in one direction, as ``(kind, pk, via, via_id)``.

    Down: the containers whose derivation edges land in one of ``n``'s spaces
    (:func:`derived_containers`), the containers a file was ingested into, the files a
    container was exported as. Up: the mirror (:func:`derivation_edges`).
    """
    t = _transformation_columns()
    source = enums.FileLinkDirectionChoices.SOURCE.value
    rendition = enums.FileLinkDirectionChoices.RENDITION.value
    # The edges first, through the FK index from the frontier's spaces, and fenced (`OFFSET 0`)
    # so the planner cannot pull the key lookups below the join: those run once per edge
    # actually touching the frontier, never once per candidate.
    via_column, systems = (t["output"], _systems_of(f"{n}.kind", f"{n}.pk")) if down else (t["input"], _own_system_of(f"{n}.kind", f"{n}.pk"))
    edges = (
        f"SELECT t.{_q('id')} AS id, t.{t['input']} AS i, t.{t['output']} AS o FROM (SELECT DISTINCT * FROM ({systems}) u) sys(id) "
        f"JOIN {_T} t ON t.{via_column} = sys.id "
        f"WHERE t.{t['kind']} <> '{enums.TransformKindChoices.FIELD.value}' AND t.{t['organization']} = %(org)s OFFSET 0"
    )
    if down:
        derivation = (
            f"SELECT c.kind, c.pk, '{VIA_TRANSFORMATION}'::text, e.id::bigint FROM ({edges}) e "
            f"CROSS JOIN LATERAL ({_key_of('e.i')}) c "
            f"CROSS JOIN LATERAL ({_key_of('e.o')}) p "
            f"WHERE (c.kind, c.pk) <> (p.kind, p.pk) AND (c.kind, c.pk) <> ({n}.kind, {n}.pk) "
            f"AND {_not_drawing_surface('c.kind', 'c.pk')}"
        )
        links = _file_link_arm(n, from_file=True, direction=source) + _file_link_arm(n, from_file=False, direction=rendition)
    else:
        derivation = (
            f"SELECT p.kind, p.pk, '{VIA_TRANSFORMATION}'::text, e.id::bigint FROM ({edges}) e "
            f"CROSS JOIN LATERAL ({_key_of('e.o')}) p "
            f"WHERE (p.kind, p.pk) <> ({n}.kind, {n}.pk) AND {_not_drawing_surface(f'{n}.kind', f'{n}.pk')}"
        )
        links = _file_link_arm(n, from_file=False, direction=source) + _file_link_arm(n, from_file=True, direction=rendition)
    arms = " UNION ALL ".join(f"({arm})" for arm in [derivation, *links])
    return f"SELECT st.kind, st.pk, st.via, st.via_id FROM ({arms}) st(kind, pk, via, via_id)"


def _hops(n: str, *, down: bool, limit: str) -> str:
    """One row per neighbour of ``n`` -- its lowest-id hop -- the first ``limit`` of them by hop.

    A child is one hop however many edges reach it: a dataset's grid is shared by the dataset,
    its level-0 array and its whole lens, and a fusion names one parent through two lenses.
    """
    return (
        f"SELECT h.kind, h.pk, h.via, h.via_id FROM (SELECT DISTINCT ON (st.kind, st.pk) st.kind, st.pk, st.via, st.via_id FROM ({_step(n, down=down)}) st"
        f" ORDER BY st.kind, st.pk, st.via, st.via_id) h ORDER BY h.via, h.via_id LIMIT {limit}"
    )


@cache
def _walk_sql(down: bool) -> str:
    """One direction of the walk. Rows are ``(kind, pk, depth)`` and the union is ``UNION``, not ``UNION ALL``.

    That is what bounds it. A dataset's grid is shared by the dataset, its level-0 array and its
    whole lens, and a fusion names one parent twice: per row, a child is one hop however many
    edges reach it (``DISTINCT ON``), and across rows, a node reached by two paths at one depth is
    one row, so a diamond does not double and a cycle cannot outrun ``nodes x depth``.
    """
    return (
        "WITH RECURSIVE walk(kind, pk, depth) AS ("
        " SELECT %(root_kind)s::text, %(root_pk)s::bigint, 0"
        " UNION"
        " SELECT s.kind, s.pk, n.depth + 1"
        " FROM walk n"
        f" CROSS JOIN LATERAL ({_hops('n', down=down, limit='%(fanout)s')}) s"
        " WHERE n.depth < %(depth)s AND (s.kind, s.pk) <> (%(root_kind)s::text, %(root_pk)s::bigint)"
        ") SELECT kind, pk, depth FROM walk LIMIT %(limit)s"
    )


@cache
def _neighbours_sql() -> str:
    """Every node's first ``fanout + 1`` hops in both directions: the edges among the kept nodes, and whether there is more."""
    return (
        "SELECT n.kind, n.pk, true, s.kind, s.pk, s.via, s.via_id FROM unnest(%(kinds)s::text[], %(pks)s::bigint[]) AS n(kind, pk)"
        f" CROSS JOIN LATERAL ({_hops('n', down=True, limit='%(probe)s')}) s"
        " UNION ALL "
        "SELECT n.kind, n.pk, false, s.kind, s.pk, s.via, s.via_id FROM unnest(%(kinds)s::text[], %(pks)s::bigint[]) AS n(kind, pk)"
        f" CROSS JOIN LATERAL ({_hops('n', down=False, limit='%(probe)s')}) s"
    )


@dataclasses.dataclass
class LineageLens:
    pk: int
    label: str
    whole: bool
    #: (array lens) How many positions it keeps along each of its dataset's axes, in array
    #: order. None for any other kind of lens, and while its dataset's shape is unknown.
    shape: list[int] | None = None
    #: (array lens) Its stored, normalized slices: ``{axis, start, stop, step}``, one per
    #: narrowed axis, in array order. Empty for the whole lens and every other kind.
    slices: list[dict] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class LineageAxis:
    name: str
    type: str
    size: int | None = None
    #: The chunk extent along it: the store's inner chunk (the unit a reader decodes) when
    #: the store reports one, else the level's own ``chunk_shape``.
    chunk: int | None = None


@dataclasses.dataclass
class LineageNode:
    kind: str
    pk: int
    depth: int
    name: str = ""
    created_at: object = None
    task_id: str | None = None
    has_more_up: bool = False
    has_more_down: bool = False
    lenses: list[LineageLens] = dataclasses.field(default_factory=list)
    lens_count: int = 0
    #: (array dataset) Its axes at level 0, in array order, with their sizes and chunks.
    axes: list[LineageAxis] = dataclasses.field(default_factory=list)
    dtype: str | None = None
    #: (array dataset) Resolution levels it holds, level 0 included.
    levels: int = 0

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.pk}"


@dataclasses.dataclass
class LineageEdge:
    via: str
    via_id: int
    parent: tuple[str, int]
    child: tuple[str, int]
    kind: str = "DERIVATION"
    task_id: str | None = None
    transformation_kind: str | None = None
    value_relation: str | None = None
    annotation_id: int | None = None
    primary: bool = False
    #: The parent's lens this step was computed from: the one whose space a derivation lands
    #: in, the whole lens for an export. None when the parent is a file.
    source_lens: int | None = None

    @property
    def key(self) -> str:
        return f"{self.via}:{self.via_id}"


@dataclasses.dataclass
class Lineage:
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    truncated: bool


@cache
def _assert_direct_scoping() -> None:
    """Every table the walk filters by organization must carry it directly, or the filter would be the wrong one."""
    for model in (models.Transformation, models.FileLink, models.File, *MODEL_BY_KEY.values()):
        if organization_path(model) != "organization":
            raise LookupError(f"{model.__name__} is not scoped by a direct organization FK; the lineage walk would not scope it")


def _walk(cursor, root: tuple[str, int], *, down: bool, depth: int, limit: int, fanout: int, org: int) -> tuple[list[tuple], bool]:
    if depth <= 0:
        return [], False
    cursor.execute(
        _walk_sql(down),
        {"root_kind": root[0], "root_pk": root[1], "depth": depth, "limit": limit + 1, "fanout": fanout, "org": org},
    )
    rows = cursor.fetchall()
    return rows[:limit], len(rows) > limit


def _hydrate_nodes(nodes: dict[tuple[str, int], LineageNode], organization) -> None:
    """One query per node kind present. A node another organization owns is dropped, never named."""
    by_kind: dict[str, list[int]] = {}
    for kind, pk in nodes:
        by_kind.setdefault(kind, []).append(pk)

    for kind, pks in by_kind.items():
        model = models.File if kind == FILE else MODEL_BY_KEY[kind]
        query = model.objects.filter(pk__in=pks, organization=organization)
        has_task = any(field.name == "created_through" for field in model._meta.fields)
        has_store = not has_task and any(field.name == "store" for field in model._meta.fields)
        if has_task:
            rows = {row["id"]: row for row in query.values("id", "name", "created_at", "created_through__task_id")}
        elif has_store:
            rows = {row["id"]: row for row in query.values("id", "name", "created_at", "store__key")}
        else:
            rows = {row["id"]: row for row in query.values("id", "name", "created_at")}
        for pk in pks:
            row = rows.get(pk)
            if row is None:
                del nodes[(kind, pk)]
                continue
            node = nodes[(kind, pk)]
            node.name = row["name"] or row.get("store__key") or ""
            node.created_at = row["created_at"]
            node.task_id = row.get("created_through__task_id")


def _hydrate_arrays(nodes: dict[tuple[str, int], LineageNode]) -> None:
    """Every array dataset's axes, shape, chunks and dtype, from its level-0 array: two queries.

    The nodes are already scoped (:func:`_hydrate_nodes` dropped what another organization
    owns), so their arrays and axes are too.
    """
    pks = [pk for kind, pk in nodes if kind == "dataset"]
    if not pks:
        return
    arrays: dict[int, list[dict]] = {}
    for row in models.DataArray.objects.filter(dataset_id__in=pks).values(
        "dataset_id", "dataset__coordinate_system_id", "level", "shape", "chunk_shape", "store__chunks", "store__dtype"
    ):
        arrays.setdefault(row["dataset_id"], []).append(row)
    systems = {pk: rows[0]["dataset__coordinate_system_id"] for pk, rows in arrays.items() if rows[0]["dataset__coordinate_system_id"]}
    axes: dict[int, list[dict]] = {}
    for row in models.Axis.objects.filter(coordinate_system_id__in=set(systems.values())).order_by("order").values("coordinate_system_id", "name", "type"):
        axes.setdefault(row["coordinate_system_id"], []).append(row)

    for pk, rows in arrays.items():
        node = nodes[("dataset", pk)]
        base = min(rows, key=lambda row: row["level"])
        shape = base["shape"] if isinstance(base["shape"], list) else []
        chunks = base["store__chunks"] or base["chunk_shape"] or []
        node.levels = len(rows)
        node.dtype = base["store__dtype"]
        node.axes = [
            LineageAxis(
                name=axis["name"],
                type=axis["type"],
                size=shape[index] if index < len(shape) else None,
                chunk=chunks[index] if isinstance(chunks, list) and index < len(chunks) else None,
            )
            for index, axis in enumerate(axes.get(systems.get(pk), []))
        ]


def _lens_shape(node: LineageNode, slices: list[dict]) -> list[int] | None:
    """An array lens' shape: its dataset's, narrowed by its normalized slices."""
    if not node.axes or any(axis.size is None for axis in node.axes):
        return None
    by_axis = {entry.get("axis"): entry for entry in slices}
    shape = []
    for axis in node.axes:
        entry = by_axis.get(axis.name)
        if entry is None:
            shape.append(axis.size)
        else:
            shape.append(len(range(entry.get("start") or 0, axis.size if entry.get("stop") is None else entry["stop"], entry.get("step") or 1)))
    return shape


def _hydrate_lenses(nodes: dict[tuple[str, int], LineageNode], edges: list[LineageEdge], landed_in: dict[int, int], organization) -> None:
    """Every node's lenses, one query per container kind, and which of them each edge leaves from.

    A derivation lands in one of its parent's spaces: a sliced lens' own system, or the
    container's grid -- where its whole lens lives, and its levels, whose edges are the whole
    lens' too. An export is written from the whole container. ``landed_in`` is each derivation
    edge's output system, by edge id.
    """
    by_kind: dict[str, list[int]] = {}
    for kind, pk in nodes:
        if kind != FILE:
            by_kind.setdefault(kind, []).append(pk)

    rows_by_node: dict[tuple[str, int], list[dict]] = {}
    for kind, pks in by_kind.items():
        lens_model = models.Lens.KIND_BY_CONTAINER.get(MODEL_BY_KEY[kind])
        if lens_model is None:
            continue
        container = f"{lens_model.CONTAINER_FIELD}_id"
        selection = "slices" if issubclass(lens_model, models.ArrayLens) else "windows"
        query = lens_model.objects.filter(**{f"{container}__in": pks}, organization=organization).order_by("id")
        for row in query.values("id", "name", "coordinate_system_id", selection, container):
            rows_by_node.setdefault((kind, row[container]), []).append({**row, "selection": row[selection] or []})

    def whole_of(rows: list[dict]) -> dict | None:
        return next((row for row in rows if not row["selection"]), None)

    used: dict[tuple[str, int], set[int]] = {}
    for edge in edges:
        if edge.parent[0] == FILE:
            continue
        rows = rows_by_node.get(edge.parent, [])
        landed = landed_in.get(edge.via_id) if edge.via == VIA_TRANSFORMATION else None
        there = [row for row in rows if landed is not None and row["coordinate_system_id"] == landed]
        # Two lenses in one space are the whole lens and nothing else: a sliced lens has its own.
        chosen = whole_of(there) or (there[0] if there else None) or whole_of(rows)
        if chosen is not None:
            edge.source_lens = chosen["id"]
            used.setdefault(edge.parent, set()).add(chosen["id"])

    for key, rows in rows_by_node.items():
        node = nodes[key]
        node.lens_count = len(rows)
        wanted = used.get(key, set())
        whole = whole_of(rows)
        ordered = ([whole] if whole else []) + [row for row in rows if row is not whole and row["id"] in wanted] + [row for row in rows if row is not whole and row["id"] not in wanted]
        # Only an array lens selects by index slices; every other kind stores windows.
        sliced = key[0] == "dataset"
        node.lenses = [
            LineageLens(
                pk=row["id"],
                label=(row["name"] or "").strip() or ("Whole" if not row["selection"] else selection_label(row["selection"])),
                whole=not row["selection"],
                shape=_lens_shape(node, row["selection"]) if sliced else None,
                slices=list(row["selection"]) if sliced else [],
            )
            for row in ordered[: max(MAX_LENSES, 1 + len(wanted))]
        ]


def lineage(
    root: tuple[str, int],
    *,
    organization,
    up: int = 1,
    down: int = 3,
    limit: int = 300,
    fanout: int = 50,
) -> Lineage:
    """The directed lineage around ``root`` (a ``(container key | "file", pk)``), ``up`` hops of ancestors and ``down`` of descendants.

    A fixed number of round trips whatever the depth: the two walks, the neighbour pass, the
    edge detail, one per node kind present (names, then lenses), and two for the array
    datasets' axes, shapes and dtypes. ``truncated`` says the walk stopped at ``limit``; a node
    whose hops were cut off -- by depth, by ``fanout``, or by ``limit`` -- says so in
    ``has_more_up`` / ``has_more_down``, and asking again rooted there continues from it.
    """
    _assert_direct_scoping()
    up, down = max(0, min(up, MAX_DEPTH)), max(0, min(down, MAX_DEPTH))
    limit, fanout = max(1, min(limit, MAX_LIMIT)), max(1, min(fanout, MAX_FANOUT))
    org = organization.pk

    nodes: dict[tuple[str, int], LineageNode] = {root: LineageNode(kind=root[0], pk=root[1], depth=0)}
    with connection.cursor() as cursor:
        down_rows, down_cut = _walk(cursor, root, down=True, depth=down, limit=limit, fanout=fanout, org=org)
        up_rows, up_cut = _walk(cursor, root, down=False, depth=up, limit=limit, fanout=fanout, org=org)

        # Many paths can reach one node; it is drawn once, at its nearest.
        for rows, sign in ((down_rows, 1), (up_rows, -1)):
            for kind, pk, depth in rows:
                if depth == 0:
                    continue
                key = (kind, pk)
                node = nodes.get(key)
                if node is None:
                    nodes[key] = LineageNode(kind=kind, pk=pk, depth=sign * depth)
                elif abs(sign * depth) < abs(node.depth):
                    node.depth = sign * depth

        if len(nodes) > limit:
            # Both walks together may overshoot: keep the nearest, downstream first on a tie.
            kept = sorted(nodes.values(), key=lambda node: (abs(node.depth), node.depth < 0))[:limit]
            nodes = {(node.kind, node.pk): node for node in kept}
            down_cut = True

        cursor.execute(
            _neighbours_sql(),
            {"kinds": [kind for kind, _ in nodes], "pks": [pk for _, pk in nodes], "probe": fanout + 1, "org": org},
        )
        hops = cursor.fetchall()

    edges: dict[str, LineageEdge] = {}
    first_derivation: dict[tuple[str, int], int] = {}
    for kind, pk, downward, other_kind, other_pk, via, via_id in hops:
        here, there = (kind, pk), (other_kind, other_pk)
        if not downward and via == VIA_TRANSFORMATION:
            # Ordered by id, so the first up-hop by transformation is the primary parent.
            first_derivation.setdefault(here, via_id)
        if there not in nodes:
            node = nodes[here]
            if downward:
                node.has_more_down = True
            else:
                node.has_more_up = True
            continue
        parent, child = (here, there) if downward else (there, here)
        key = f"{via}:{via_id}"
        if key not in edges:
            edges[key] = LineageEdge(via=via, via_id=via_id, parent=parent, child=child)

    # A capped probe means more hops than were looked at, whether or not the ones seen were kept.
    counts: dict[tuple[tuple[str, int], bool], int] = {}
    for kind, pk, downward, *_ in hops:
        counts[((kind, pk), downward)] = counts.get(((kind, pk), downward), 0) + 1
    for (here, downward), count in counts.items():
        # A full probe may have stopped short: more hops than were looked at, kept or not.
        if count > fanout and here in nodes:
            if downward:
                nodes[here].has_more_down = True
            else:
                nodes[here].has_more_up = True

    _hydrate_nodes(nodes, organization)
    edges = {key: edge for key, edge in edges.items() if edge.parent in nodes and edge.child in nodes}

    transformation_ids = [edge.via_id for edge in edges.values() if edge.via == VIA_TRANSFORMATION]
    link_ids = [edge.via_id for edge in edges.values() if edge.via == VIA_FILE_LINK]
    transformations = (
        {row["id"]: row for row in models.Transformation.objects.filter(pk__in=transformation_ids, organization=organization).values("id", "kind", "value_relation", "annotation_id", "output_id")}
        if transformation_ids
        else {}
    )
    links = (
        {row["id"]: row for row in models.FileLink.objects.filter(pk__in=link_ids, organization=organization).values("id", "direction", "value_relation", "created_through__task_id")}
        if link_ids
        else {}
    )

    for edge in edges.values():
        if edge.via == VIA_TRANSFORMATION:
            row = transformations.get(edge.via_id, {})
            edge.kind = "DERIVATION"
            edge.transformation_kind = row.get("kind")
            edge.value_relation = row.get("value_relation")
            edge.annotation_id = row.get("annotation_id")
            # Who made the child is who made the edge: a derivation is written with its product.
            edge.task_id = nodes[edge.child].task_id
            edge.primary = first_derivation.get(edge.child) == edge.via_id
        else:
            row = links.get(edge.via_id, {})
            edge.kind = "INGEST" if row.get("direction") == enums.FileLinkDirectionChoices.SOURCE.value else "EXPORT"
            edge.value_relation = row.get("value_relation")
            # The link's own task when it records one; otherwise whoever made the child -- a
            # converter writes the dataset (and its link) under one task, and not every writer
            # stamps the link row too.
            edge.task_id = row.get("created_through__task_id") or nodes[edge.child].task_id

    _hydrate_arrays(nodes)
    _hydrate_lenses(nodes, list(edges.values()), {pk: row["output_id"] for pk, row in transformations.items()}, organization)

    ordered_nodes = sorted(nodes.values(), key=lambda node: (node.depth, node.kind, node.pk))
    ordered_edges = sorted(edges.values(), key=lambda edge: (edge.via, edge.via_id))
    return Lineage(nodes=ordered_nodes, edges=ordered_edges, truncated=down_cut or up_cut)


#: Roots one ``descendants`` call takes: a page of tiles, not an organization.
MAX_ROOTS = 100


@cache
def _descendants_sql() -> str:
    """The downward walk from many roots at once, each row tagged with the root it was reached from.

    :func:`_walk_sql` seeded from ``unnest`` instead of one root, so a page of tiles costs one
    recursion rather than one per tile. The root rides every row, which keeps two roots'
    lineages apart even where they meet (a fusion of two series), and ``UNION`` still bounds
    each root's walk to ``nodes x depth``. Hops are probed at ``fanout + 1`` and the walk one
    hop past ``depth``, so the caller can tell a cut-off lineage from a finished one without
    a second pass.
    """
    return (
        "WITH RECURSIVE walk(root_kind, root_pk, kind, pk, depth, parent_kind, parent_pk) AS ("
        " SELECT r.kind, r.pk, r.kind, r.pk, 0, NULL::text, NULL::bigint FROM unnest(%(kinds)s::text[], %(pks)s::bigint[]) AS r(kind, pk)"
        " UNION"
        " SELECT n.root_kind, n.root_pk, s.kind, s.pk, n.depth + 1, n.kind, n.pk"
        " FROM walk n"
        f" CROSS JOIN LATERAL ({_hops('n', down=True, limit='%(probe)s')}) s"
        " WHERE n.depth <= %(depth)s AND (s.kind, s.pk) <> (n.root_kind, n.root_pk)"
        ") SELECT root_kind, root_pk, kind, pk, depth, parent_kind, parent_pk FROM walk WHERE depth > 0 LIMIT %(limit)s"
    )


@dataclasses.dataclass
class Descendants:
    root: tuple[str, int]
    #: Everything made from the root, nearest first, each once.
    nodes: list[LineageNode]
    #: Whether something made from it was left out: past ``down``, past ``fanout`` or past the limit.
    more: bool = False
    #: What each node was made from on its nearest path: the root for the first hop.
    parents: dict[tuple[str, int], tuple[str, int]] = dataclasses.field(default_factory=dict)


def descendants(
    roots: list[tuple[str, int]],
    *,
    organization,
    down: int = 3,
    limit: int = 500,
    fanout: int = 20,
) -> list[Descendants]:
    """What was made from each of ``roots``, ``down`` hops deep, in three round trips whatever the number of roots.

    The summary :func:`lineage` would give per root with ``up=0``, without its edges, lenses or
    arrays: what a page of tiles needs to say "these were made from this one". A root another
    organization owns answers nothing. ``limit`` is shared by all roots; a root whose walk it
    cut short says so in ``more``.
    """
    _assert_direct_scoping()
    down = max(1, min(down, MAX_DEPTH))
    limit, fanout = max(1, min(limit, MAX_LIMIT)), max(1, min(fanout, MAX_FANOUT))
    roots = list(dict.fromkeys(roots))[:MAX_ROOTS]
    if not roots:
        return []

    # The roots are scoped like any node: one query per kind present.
    known = {root: LineageNode(kind=root[0], pk=root[1], depth=0) for root in roots}
    _hydrate_nodes(known, organization)
    roots = [root for root in roots if root in known]
    if not roots:
        return []

    with connection.cursor() as cursor:
        cursor.execute(
            _descendants_sql(),
            {
                "kinds": [kind for kind, _ in roots],
                "pks": [pk for _, pk in roots],
                "depth": down,
                "probe": fanout + 1,
                "limit": limit + 1,
                "org": organization.pk,
            },
        )
        rows = cursor.fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]

    found: dict[tuple[str, int], dict[tuple[str, int], LineageNode]] = {root: {} for root in roots}
    parents: dict[tuple[str, int], dict[tuple[str, int], tuple[str, int]]] = {root: {} for root in roots}
    more: dict[tuple[str, int], bool] = {root: truncated for root in roots}
    children: dict[tuple[tuple[str, int], tuple[str, int]], set] = {}
    for root_kind, root_pk, kind, pk, depth, parent_kind, parent_pk in rows:
        root, key = (root_kind, root_pk), (kind, pk)
        if depth > down:
            # The probe hop past the bound: there is more below.
            more[root] = True
            continue
        siblings = children.setdefault((root, (parent_kind, parent_pk)), set())
        if key not in siblings and len(siblings) >= fanout:
            # The probe hop past the fan-out cap: counted as more, not listed.
            more[root] = True
            continue
        siblings.add(key)
        node = found[root].get(key)
        if node is None:
            found[root][key] = LineageNode(kind=kind, pk=pk, depth=depth)
            parents[root][key] = (parent_kind, parent_pk)
        elif depth < node.depth:
            node.depth = depth
            parents[root][key] = (parent_kind, parent_pk)

    every = {key: node for nodes in found.values() for key, node in nodes.items()}
    _hydrate_nodes(every, organization)
    return [
        Descendants(
            root=root,
            nodes=sorted(
                (dataclasses.replace(every[key], depth=node.depth) for key, node in found[root].items() if key in every),
                key=lambda node: (node.depth, node.kind, node.pk),
            ),
            more=more[root],
            parents=parents[root],
        )
        for root in roots
    ]


#: The kinds a home page lists as roots. An annotation collection is drawn rather than
#: brought in, so it is not one of them.
ROOT_KINDS = (FILE, "dataset", "tabledataset", "meshcollection", "sparsedataset", "networkcollection")


@dataclasses.dataclass(frozen=True)
class RootFilter:
    """What narrows :func:`roots`. Every field is optional; one left as ``None`` does not narrow.

    ``acquired_after`` / ``acquired_before`` draw the same overlap line as
    ``ArrayDatasetFilter.acquiredAfter`` / ``acquiredBefore``. Only an array dataset carries an
    acquisition window, so a file is judged by the series converted out of it, and every other
    kind drops out while either is set. ``spec`` works the same way, on
    ``ArrayDatasetFilter.spec``'s line (every one of them); a file's acquisition window and spec
    are read off one series, so a file matches only when one of its series meets both. A file
    is in a collaboration when it, or a series converted out of it, was put there.
    ``search`` matches a name, ignoring case, anywhere in it; a file also matches by the name
    of a series converted out of it.
    """

    owner: str | None = None
    collaboration: int | None = None
    created_after: object = None
    created_before: object = None
    acquired_after: object = None
    acquired_before: object = None
    spec: tuple[str, ...] | None = None
    search: str | None = None

    @property
    def acquired(self) -> bool:
        return self.acquired_after is not None or self.acquired_before is not None


def _shape(filters: RootFilter) -> tuple[str, ...]:
    """Which of a filter's fields are set: the shape of the SQL, so its cache key."""
    return tuple(field.name for field in dataclasses.fields(filters) if getattr(filters, field.name) is not None)


def _has_field(model: type, name: str) -> bool:
    return any(field.name == name for field in model._meta.get_fields())


def _acquired_sql(alias: str, shape: tuple[str, ...]) -> str:
    """The overlap of ``alias``'s acquisition window with the asked one, on the derived columns."""
    clauses = []
    if "acquired_after" in shape:
        clauses.append(f"{alias}.{_q(_column(models.ArrayDataset, 'acquisition_end'))} >= %(acquired_after)s")
    if "acquired_before" in shape:
        clauses.append(f"{alias}.{_q(_column(models.ArrayDataset, 'acquisition_start'))} <= %(acquired_before)s")
    return " AND ".join(clauses)


def _name_sql(alias: str, model: type) -> str:
    """Whether ``alias``'s name holds the searched text, ignoring case (the pattern is built in :func:`roots`)."""
    return f"{alias}.{_q(_column(model, 'name'))} ILIKE %(search)s"


def _spec_sql(alias: str) -> str:
    """Whether ``alias`` carries every asked spec: ``stored_spec`` contains them, as the list filter asks."""
    return f"{alias}.{_q(_column(models.ArrayDataset, 'stored_spec'))} @> %(spec)s::jsonb"


def _in_collaboration_sql(model: type, pk_sql: str) -> str:
    """Whether the row whose key ``pk_sql`` reads is an item of the asked collaboration (of this organization)."""
    from arkitekt_service.collaborations.models import Collaboration, CollaborationItem
    from django.contrib.contenttypes.models import ContentType

    item, collab, ct = CollaborationItem._meta, Collaboration._meta, ContentType._meta
    return (
        f"EXISTS (SELECT 1 FROM {_q(item.db_table)} ci"
        f" JOIN {_q(collab.db_table)} c ON c.{_q(collab.pk.column)} = ci.{_q(_column(CollaborationItem, 'collaboration'))}"
        f" JOIN {_q(ct.db_table)} ct ON ct.{_q(ct.pk.column)} = ci.{_q(_column(CollaborationItem, 'content_type'))}"
        f" WHERE ci.{_q(_column(CollaborationItem, 'collaboration'))} = %(collaboration)s"
        f" AND c.{_q(_column(Collaboration, 'organization'))} = %(org)s"
        f" AND ct.app_label = '{model._meta.app_label}' AND ct.model = '{model._meta.model_name}'"
        f" AND ci.{_q(_column(CollaborationItem, 'object_id'))} = ({pk_sql})::text)"
    )


def _series_sql(x_pk: str, condition: str) -> str:
    """Whether a series converted out of the file ``x_pk`` names meets ``condition`` (on ``d``)."""
    link, dataset = models.FileLink._meta, models.ArrayDataset._meta
    return (
        f"EXISTS (SELECT 1 FROM {_q(link.db_table)} l"
        f" JOIN {_q(dataset.db_table)} d ON d.{_q(dataset.pk.column)} = l.{_q(_column(models.FileLink, 'dataset'))}"
        f" WHERE l.{_q(_column(models.FileLink, 'file'))} = {x_pk}"
        f" AND l.{_q(_column(models.FileLink, 'direction'))} = '{enums.FileLinkDirectionChoices.SOURCE.value}'"
        f" AND {condition})"
    )


def _root_arm_filters(key: str, model: type, shape: tuple[str, ...]) -> list[str] | None:
    """The extra conditions on one kind's arm, or ``None`` when the filter rules the kind out."""
    pk = f"x.{_q(model._meta.pk.column)}"
    clauses = []
    if "owner" in shape:
        if not _has_field(model, "creator"):
            return None
        user = models.ArrayDataset._meta.get_field("creator").related_model._meta
        clauses.append(
            f"x.{_q(_column(model, 'creator'))} IN (SELECT u.{_q(user.pk.column)} FROM {_q(user.db_table)} u WHERE u.sub = %(owner)s)"
        )
    if "collaboration" in shape:
        held = _in_collaboration_sql(model, pk)
        if key == FILE:
            held = f"({held} OR {_series_sql(pk, _in_collaboration_sql(models.ArrayDataset, f'd.{_q(models.ArrayDataset._meta.pk.column)}'))})"
        clauses.append(held)
    if "search" in shape:
        named = _name_sql("x", model)
        if key == FILE:
            named = f"({named} OR {_series_sql(pk, _name_sql('d', models.ArrayDataset))})"
        clauses.append(named)
    created = _q(_column(model, "created_at"))
    if "created_after" in shape:
        clauses.append(f"x.{created} >= %(created_after)s")
    if "created_before" in shape:
        clauses.append(f"x.{created} <= %(created_before)s")
    # What only an array dataset carries: a file is judged by one of its series meeting all of it.
    acquired = "acquired_after" in shape or "acquired_before" in shape
    if acquired or "spec" in shape:
        if key == "dataset":
            on = "x"
        elif key == FILE:
            on = "d"
        else:
            return None
        series = [*([_acquired_sql(on, shape)] if acquired else []), *([_spec_sql(on)] if "spec" in shape else [])]
        clauses.append(" AND ".join(series) if key == "dataset" else _series_sql(pk, " AND ".join(series)))
    return clauses


def _not_a_field_sql() -> str:
    """No edge names the dataset ``x``'s own space as its field."""
    field = _q(_column(models.Transformation, "field"))
    system = _q(_column(models.ArrayDataset, "coordinate_system"))
    return f"NOT EXISTS (SELECT 1 FROM {_T} f WHERE f.{field} = x.{system})"


@cache
def _roots_sql(kinds: tuple[str, ...], shape: tuple[str, ...] = ()) -> str | None:
    """The newest nodes with no up-hop, across ``kinds``, as ``(kind, pk, created_at)``.

    "No up-hop" is :func:`_step` upward, the very hop the lineage walk takes, so a root here
    is exactly a node the lineage page draws with nothing above it: no derivation edge into
    another container, no file it was converted from; for a file, no container it was
    exported from. Each kind's arm stops at ``offset + limit`` of its newest roots, which is
    all the outer order can use. ``shape`` names the :class:`RootFilter` fields that are set;
    their values ride as parameters. ``None`` when the filter rules out every kind.
    """
    arms = []
    for key in kinds:
        model = models.File if key == FILE else MODEL_BY_KEY[key]
        extra = _root_arm_filters(key, model, shape)
        if extra is None:
            continue
        table = _q(model._meta.db_table)
        pk = _q(model._meta.pk.column)
        created = _q(_column(model, "created_at"))
        org = _q(_column(model, "organization"))
        if key == "dataset":
            # A dataset whose space is another edge's FIELD -- the frame times a movie is placed
            # by, a warp field -- is part of that placement, not data of its own: reached from
            # what it places, never listed beside it.
            extra = [*extra, _not_a_field_sql()]
        narrowed = "".join(f" AND {clause}" for clause in extra)
        arms.append(
            f"(SELECT n.kind, n.pk, x.{created} AS created_at FROM {table} x"
            f" CROSS JOIN LATERAL (SELECT '{key}'::text AS kind, x.{pk}::bigint AS pk) n"
            f" WHERE x.{org} = %(org)s AND NOT EXISTS (SELECT 1 FROM ({_step('n', down=False)}) up LIMIT 1){narrowed}"
            f" ORDER BY x.{created} DESC, x.{pk} DESC LIMIT %(window)s)"
        )
    if not arms:
        return None
    return (
        f"SELECT r.kind, r.pk, r.created_at FROM ({' UNION ALL '.join(arms)}) r"
        " ORDER BY r.created_at DESC, r.kind, r.pk DESC OFFSET %(offset)s LIMIT %(limit)s"
    )


def roots(
    *,
    organization,
    kinds: list[str] | None = None,
    offset: int = 0,
    limit: int = 20,
    filters: RootFilter | None = None,
) -> list[LineageNode]:
    """The newest parentless data of an organization, whatever its kind: what a home page lists.

    Two round trips: the walk-backed root test for every kind at once, then the names. A
    file's converted series and a dataset's mask are not roots -- they are reached from
    their parent -- and neither is a file exported from a dataset. ``filters`` narrows it by
    creator, collaboration, creation time and acquisition time; see :class:`RootFilter`.
    """
    _assert_direct_scoping()
    wanted = tuple(key for key in ROOT_KINDS if kinds is None or key in kinds)
    limit, offset = max(1, min(limit, MAX_LIMIT)), max(0, offset)
    filters = filters or RootFilter()
    shape = _shape(filters)
    sql = _roots_sql(wanted, shape) if wanted else None
    if sql is None:
        return []
    params = {name: getattr(filters, name) for name in shape}
    if "search" in params:
        # A literal substring: the LIKE wildcards in what was typed match only themselves.
        escaped = params["search"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["search"] = f"%{escaped}%"
    if "spec" in params:
        params["spec"] = json.dumps(list(params["spec"]))
    with connection.cursor() as cursor:
        cursor.execute(sql, {**params, "org": organization.pk, "window": offset + limit, "offset": offset, "limit": limit})
        rows = cursor.fetchall()
    nodes = {(kind, pk): LineageNode(kind=kind, pk=pk, depth=0, created_at=created) for kind, pk, created in rows}
    _hydrate_nodes(nodes, organization)
    return [nodes[(kind, pk)] for kind, pk, _ in rows if (kind, pk) in nodes]

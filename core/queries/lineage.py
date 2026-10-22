__all__ = ["lineage", "descendants", "roots"]

from kante.types import Info
from strawberry_django.pagination import OffsetPaginationInput

from core import enums, models
from core.logic import lineage as lineage_logic
from core.logic.graph import MODEL_BY_KEY
from core.scoping import get_for_org
from core.types.lineage import Descendants, DescendantNode, Lineage, RootNode, LineageAxis, LineageEdge, LineageEdgeKind, LineageLens, LineageNode, LineageNodeKind, LineageRootInput, LineageSlice, RootFilter


def lineage(
    info: Info,
    start: LineageRootInput,
    up: int = 1,
    down: int = 3,
    limit: int = 300,
    fanout: int = 50,
) -> Lineage:
    """The directed lineage around one container or file; see :func:`core.logic.lineage.lineage`.

    The argument is `start`, not `root`: strawberry reserves a resolver parameter named `root`
    for the parent value and silently drops it from the schema.
    """
    kind = LineageNodeKind(start.kind).value
    model = models.File if kind == lineage_logic.FILE else MODEL_BY_KEY[kind]
    instance = get_for_org(model, info, id=start.id)

    result = lineage_logic.lineage(
        (kind, instance.pk),
        organization=info.context.request.organization,
        up=up,
        down=down,
        limit=limit,
        fanout=fanout,
    )

    return Lineage(
        root=f"{kind}:{instance.pk}",
        nodes=[
            LineageNode(
                key=node.key,
                kind=LineageNodeKind(node.kind),
                id=str(node.pk),
                name=node.name,
                depth=node.depth,
                created_at=node.created_at,
                task_id=node.task_id,
                has_more_up=node.has_more_up,
                has_more_down=node.has_more_down,
                lenses=[
                    LineageLens(
                        id=str(lens.pk),
                        label=lens.label,
                        whole=lens.whole,
                        shape=lens.shape,
                        slices=[LineageSlice(axis=entry["axis"], start=entry["start"], stop=entry["stop"], step=entry.get("step") or 1) for entry in lens.slices],
                    )
                    for lens in node.lenses
                ],
                lens_count=node.lens_count,
                axes=[LineageAxis(name=axis.name, type=enums.AxisType(axis.type), size=axis.size, chunk=axis.chunk) for axis in node.axes],
                dtype=node.dtype,
                levels=node.levels,
            )
            for node in result.nodes
        ],
        edges=[
            LineageEdge(
                key=edge.key,
                kind=LineageEdgeKind(edge.kind),
                source=f"{edge.parent[0]}:{edge.parent[1]}",
                target=f"{edge.child[0]}:{edge.child[1]}",
                task_id=edge.task_id,
                transformation_kind=enums.TransformKind(edge.transformation_kind) if edge.transformation_kind else None,
                value_relation=enums.ValueRelation(edge.value_relation) if edge.value_relation else None,
                annotation_id=str(edge.annotation_id) if edge.annotation_id else None,
                primary=edge.primary,
                source_lens=str(edge.source_lens) if edge.source_lens else None,
            )
            for edge in result.edges
        ],
        truncated=result.truncated,
    )


def descendants(
    info: Info,
    starts: list[LineageRootInput],
    down: int = 3,
    limit: int = 500,
    fanout: int = 20,
) -> list[Descendants]:
    """What was made from each of many roots; see :func:`core.logic.lineage.descendants`."""
    roots = [(LineageNodeKind(start.kind).value, int(start.id)) for start in starts]
    result = lineage_logic.descendants(roots, organization=info.context.request.organization, down=down, limit=limit, fanout=fanout)
    return [
        Descendants(
            root=f"{entry.root[0]}:{entry.root[1]}",
            nodes=[
                DescendantNode(
                    key=node.key,
                    kind=LineageNodeKind(node.kind),
                    id=str(node.pk),
                    name=node.name,
                    depth=node.depth,
                    parent="{}:{}".format(*entry.parents.get((node.kind, node.pk), entry.root)),
                )
                for node in entry.nodes
            ],
            more=entry.more,
        )
        for entry in result
    ]


def roots(
    info: Info,
    kinds: list[LineageNodeKind] | None = None,
    pagination: OffsetPaginationInput | None = None,
    filters: RootFilter | None = None,
) -> list[RootNode]:
    """The newest parentless data; see :func:`core.logic.lineage.roots`."""
    offset = pagination.offset if pagination and isinstance(pagination.offset, int) else 0
    limit = pagination.limit if pagination and isinstance(pagination.limit, int) and pagination.limit > 0 else 20
    result = lineage_logic.roots(
        organization=info.context.request.organization,
        kinds=[LineageNodeKind(kind).value for kind in kinds] if kinds else None,
        offset=offset,
        limit=limit,
        filters=lineage_logic.RootFilter(
            owner=filters.owner,
            collaboration=int(filters.collaboration) if filters.collaboration is not None else None,
            created_after=filters.created_after,
            created_before=filters.created_before,
            acquired_after=filters.acquired_after,
            acquired_before=filters.acquired_before,
            spec=tuple(spec.value for spec in filters.spec) if filters.spec else None,
            search=filters.search.strip() or None if filters.search else None,
        )
        if filters
        else None,
    )
    return [
        RootNode(key=node.key, kind=LineageNodeKind(node.kind), id=str(node.pk), name=node.name, created_at=node.created_at)
        for node in result
    ]

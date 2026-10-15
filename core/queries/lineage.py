__all__ = ["lineage"]

from kante.types import Info

from core import enums, models
from core.logic import lineage as lineage_logic
from core.logic.graph import MODEL_BY_KEY
from core.scoping import get_for_org
from core.types.lineage import Lineage, LineageAxis, LineageEdge, LineageEdgeKind, LineageLens, LineageNode, LineageNodeKind, LineageRootInput, LineageSlice


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

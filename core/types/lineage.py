"""The directed lineage around one container or file: :func:`core.logic.lineage.lineage`, as GraphQL."""

import datetime
from enum import Enum
from typing import List

import kante
import strawberry

from core import enums


@strawberry.enum(description="What a lineage node is: one of the containers, or a file")
class LineageNodeKind(str, Enum):
    """The container keys of ``core.logic.graph.CONTAINERS``, plus ``file``. The values are those keys."""

    DATASET = "dataset"
    TABLE_DATASET = "tabledataset"
    SPARSE_DATASET = "sparsedataset"
    MESH_COLLECTION = "meshcollection"
    NETWORK_COLLECTION = "networkcollection"
    ANNOTATION_COLLECTION = "annotationcollection"
    FILE = "file"


@strawberry.enum(description="What a lineage edge records")
class LineageEdgeKind(str, Enum):
    """A derivation edge between two containers, or a file link in one of its two directions."""

    DERIVATION = "DERIVATION"
    INGEST = "INGEST"
    EXPORT = "EXPORT"


@strawberry.input(description="The node a lineage walk starts from")
class LineageRootInput:
    kind: LineageNodeKind = strawberry.field(description="What the root is")
    id: strawberry.ID = strawberry.field(description="Its ID")


@kante.type(description="One narrowed axis of an array lens: its normalized slice")
class LineageSlice:
    axis: str = strawberry.field(description="The dataset axis it narrows")
    start: int = strawberry.field(description="The first index it keeps")
    stop: int = strawberry.field(description="The first index past the last it keeps")
    step: int = strawberry.field(description="Every how many it keeps")


@kante.type(description="One lens of a lineage node: a port data was computed from")
class LineageLens:
    id: strawberry.ID = strawberry.field(description="The lens' ID, as the lens queries and the lens' own structure take it")
    label: str = strawberry.field(description="Its name, else what it selects (`y[0:32], c[1:2]`), else `Whole`. Its container is not repeated: the node already names it")
    whole: bool = strawberry.field(description="Whether it selects all of its container")
    shape: List[int] | None = strawberry.field(description="(array lens) How many positions it keeps along each of its dataset's axes, in the node's `axes` order. Null for any other kind of lens")
    slices: List[LineageSlice] = strawberry.field(description="(array lens) What it narrows, one slice per narrowed axis in array order. Empty for the whole lens and any other kind")


@kante.type(description="One axis of an array dataset in a lineage, at its full resolution")
class LineageAxis:
    name: str = strawberry.field(description="The axis' name, e.g. 'z', 'c' or 'tau'")
    type: enums.AxisType = strawberry.field(description="What it samples")
    size: int | None = strawberry.field(description="Its extent at level 0, if known")
    chunk: int | None = strawberry.field(description="The chunk extent along it: the store's inner chunk (the unit a reader decodes) where reported, else the level's chunk shape")


@kante.type(description="One container or file in a lineage")
class LineageNode:
    key: strawberry.ID = strawberry.field(description="`<kind>:<id>`, unique within the lineage and stable across walks: what the edges' `source` and `target` name")
    kind: LineageNodeKind = strawberry.field(description="What this node is")
    id: strawberry.ID = strawberry.field(description="Its ID, as its own query takes it")
    name: str = strawberry.field(description="Its name; a mesh or network collection without one is named by its store's key")
    depth: int = strawberry.field(description="Hops from the root along the walk: positive downstream (made from the root), negative upstream (the root was made from it), 0 for the root. The nearest, when several paths reach it")
    created_at: datetime.datetime | None = strawberry.field(description="When it was created")
    task_id: strawberry.ID | None = strawberry.field(description="The task it was created through, if any: a rekuest task ID. Null for a mesh or network collection, which do not record one")
    has_more_up: bool = strawberry.field(description="Whether it was made from something this answer leaves out -- past the depth bound, past the fan-out cap or past the limit. Walk again rooted here to continue")
    has_more_down: bool = strawberry.field(description="Whether something made from it is left out of this answer, for the same reasons")
    lenses: List[LineageLens] = strawberry.field(
        description="Its lenses, the ports its data is used through: the whole lens first, then every lens an edge in this answer leaves from, then the oldest of the rest up to a cap. Empty for a file"
    )
    lens_count: int = strawberry.field(description="How many lenses it has in all, which may be more than `lenses` lists")
    axes: List[LineageAxis] = strawberry.field(description="(array dataset) Its axes in array order, with sizes and chunks at level 0. Empty for every other kind")
    dtype: str | None = strawberry.field(description="(array dataset) The level-0 array's dtype, where the store reports one")
    levels: int = strawberry.field(description="(array dataset) How many resolution levels it holds, level 0 included. 0 for every other kind")


@kante.type(description="That one node was made from another. Always parent to child, the way provenance reads")
class LineageEdge:
    key: strawberry.ID = strawberry.field(description="Unique within the lineage and stable across walks")
    kind: LineageEdgeKind = strawberry.field(description="A derivation between containers, an ingest from a file, or an export to one")
    source: strawberry.ID = strawberry.field(description="The parent's node key")
    target: strawberry.ID = strawberry.field(description="The child's node key")
    task_id: strawberry.ID | None = strawberry.field(description="The task that made this step, if any: a rekuest task ID. For a file link, the link's own task when it records one; otherwise, and for every derivation, the task that made the child")
    transformation_kind: enums.TransformKind | None = strawberry.field(description="(DERIVATION) The kind of the edge's map. UNMAPPABLE where the lineage is recorded but no geometry survived")
    value_relation: enums.ValueRelation | None = strawberry.field(description="What the step did to the values, if stated")
    annotation_id: strawberry.ID | None = strawberry.field(description="(DERIVATION) The shape the child was computed along or within, if the derivation named one")
    primary: bool = strawberry.field(description="(DERIVATION) Whether this is the child's primary parent: the first edge its creator declared, the one that places it")
    source_lens: strawberry.ID | None = strawberry.field(
        description="The parent's lens this step was computed from -- the one whose space a derivation lands in, the whole lens for an export -- always one of the parent's `lenses`. Null when the parent is a file"
    )


@kante.type(
    description=(
        "The directed lineage around one container or file: `up` hops of what it was made from and `down` hops of what was made from it, files included. Unlike "
        "`lineageGraph`, which returns the whole undirected derivation component, this never turns sideways -- a node's siblings appear once its parent is walked from"
    )
)
class Lineage:
    root: strawberry.ID = strawberry.field(description="The root's node key")
    nodes: List[LineageNode] = strawberry.field(description="Every node reached, the root included, nearest-upstream first")
    edges: List[LineageEdge] = strawberry.field(description="Every edge between two of `nodes`")
    truncated: bool = strawberry.field(description="Whether the walk stopped at `limit` before reaching its depth")

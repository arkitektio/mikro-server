"""GraphQL types for attribute plans: instructions, never attributes (RFC-7).

Computed types, not django types: a plan is derived from the graph at query time and reads
no store. The server names the array to sample, the axes to sample it on, the parquet to
query and the columns to select; a zarr+duckdb worker executes it with credentials it
already has (the stores' own ``accessGrant``). Anything that wants *values* runs the plan.
"""

from typing import Annotated, List

import strawberry

import kante
from datalayer.types import FabriksStore, KonnektionStore, ParquetStore, ZarrStore

from core import enums
from core.types.column_options import ColumnOptionJoinStep
from core.types.coords import CoordinateSystem, FieldTransformation, PlacementStep
from core.types.table_dataset import TableDataset, Column


@kante.type(
    description="One key binding of a lookup: the sampled or passthrough value named `axis` binds the parquet column `column`. For a depth-1 plan the two names coincide by construction (a coordinate column and its derived axis are the same fact), but the worker should always bind by this pair: values live under axis names, columns live in a file, and the plan is the bridge"
)
class PlanKeyColumn:
    """One key binding of a lookup step: an axis-named value bound to a parquet column."""

    axis: str = strawberry.field(description="The name the worker holds the value under: a passthrough axis of the sampled array (e.g. `t`) or an axis the sample produced (e.g. `i`)")
    column: Column = strawberry.field(description="The declared coordinate column this value binds, carrying the parquet column name and its dtype")


@strawberry.interface(
    description=(
        "The first half of a plan: where the id comes from. Two substrates implement it, and they differ only in where the answer was materialised -- per pixel in an array, or per "
        "geometry row in a mesh collection. Everything a worker needs to bind the lookup is here on the interface; only the store differs, so select it through an `... on "
        "ArraySample` / `... on MeshSample` fragment. Either way the plan never says what the id *is* -- the client owns that, because it already has it"
    )
)
class SampleStep:
    """Where a plan's id comes from: which space, which axes, what the value means."""

    system: CoordinateSystem = strawberry.field(description="The coordinate system whose contents are the map. Equal to the queried system when the thing's own contents are the map (a label mask, a mesh collection); a different, array-backed system when the map is a separate field. `consumes` is stated in this system's axis order")
    consumes: List[str] = strawberry.field(description="The axes the point is resolved against, in the field system's axis order, e.g. ['y', 'x'] -- what you index an array with, or what your pick resolved for a collection")
    produces: List[str] = strawberry.field(description="The axis names the resulting id produces, per-edge: two sibling edges off one mask may name their produced axis differently (`i`, `label_id`), so always zip the value against THIS edge's names, never a shared key set")
    passthrough: List[str] = strawberry.field(description="The axes the edge did not consume, e.g. ['t']: their coordinates pass through by name and join the produced values as lookup keys")


@kante.type(
    description="An array whose values are the map: sample it at the point's coordinates. The client that is already rendering the array reads the value from the chunk it already has; a headless worker fetches it through the store's access grant. Either way the plan never says what is in the array -- the client owns pixels"
)
class ArraySample(SampleStep):
    """Sample the field array at the point: which array, which axes, what the value means."""

    store: ZarrStore = strawberry.field(description="The zarr store holding the array (the level-0 store for an intrinsic system). Ask it for an accessGrant to actually read chunks -- credentials never appear in a plan")


@kante.type(
    description=(
        "A mesh collection whose geometry carries the ids. **Nothing is sampled at a coordinate here**: an id rides on the geometry row, so a client that picked a surface is already "
        "holding one and goes straight to the lookup -- the mesh case of the rule that makes a plan worth caching, that it never costs a round-trip. `consumes` names the axes that "
        "pick resolved rather than axes to index anything with. The store is named for a headless worker that did not do the picking and must read the object catalog itself"
    )
)
class MeshSample(SampleStep):
    """The collection whose geometry carries the id: which collection, which axes, what the id means."""

    store: FabriksStore = strawberry.field(description="The fabriks store holding the collection -- its manifest, both catalogs and every octree level. Ask it for an accessGrant; one grant covers the whole prefix")


@kante.type(
    description=(
        "A network collection whose geometry carries the ids -- `MeshSample`'s sentence over a wireframe. **Nothing is sampled at a coordinate here**: an OBJECT id (one per traced "
        "filament or arbor, never per node) rides on the geometry rows and the object catalog, so a client that picked a segment is already holding one and goes straight to the "
        "lookup. The store is named for a headless worker that did not do the picking and must read the object catalog itself"
    )
)
class NetworkSample(SampleStep):
    """The network collection whose geometry carries the id: which collection, which axes, what the id means."""

    store: KonnektionStore = strawberry.field(description="The konnektion store holding the collection -- its manifest, both catalogs and every octree level. Ask it for an accessGrant; one grant covers the whole prefix")


@kante.type(
    description=(
        "The lookup half of a hop: read the rows (TABLE) or the slice (SPARSE) the held value identifies. There is no statement here, deliberately -- a TABLE lookup is `keyColumns` "
        "and `attributes`, and the DuckDB statement is derived from them by the worker (`core/logic/plan_sql.py`, a standard-library-only module the client carries unchanged): "
        "`SELECT <attributes> FROM read_parquet(?) WHERE <key> = ? ...`, bound with the parquet path/URL first (from the worker's own access grant) and then the key values in "
        "`keyColumns` order; a MANY hop binds lists and selects the keys too. Do not assume one row per point: (t, i) uniqueness is a convention no unique index backs, so the "
        "worker gets rows, plural"
    )
)
class LookupStep:
    """Read the table's rows or the matrix's slice the held value identifies."""

    kind: str = strawberry.field(description="Which shape this lookup is: `TABLE` for a row of a parquet, `SPARSE` for a slice of a matrix. The fields of the other shape are null -- a flat discriminator rather than an interface, which over these two would carry nothing in common")

    store: ParquetStore | None = strawberry.field(default=None, description="(TABLE) The parquet store holding the rows. Ask it for an accessGrant to actually read it -- credentials and locations never appear in a plan")
    key_columns: List[PlanKeyColumn] = strawberry.field(default_factory=list, description="(TABLE) The key bindings, in bind order: each names the value the worker holds (by axis name, or by the parent hop's column or axis name) and the parquet column it binds")
    attributes: List[Column] = strawberry.field(default_factory=list, description="(TABLE) What the statement selects -- every declared non-coordinate column, never `*`. A column whose `references` names another table holds row ids of that table; the plan's later hops say where they lead")

    sparse_array: Annotated["SparseArray", strawberry.lazy("core.types.sparse_dataset")] | None = strawberry.field(
        default=None,
        description="(SPARSE) The layout to read. Ask its `store` for an accessGrant, open the group at its `path` -- both layouts of a matrix live in one prefix, so the store alone does not say which -- then make two reads: `indptr[i:i+2]` at the id, and the range those two offsets name in `indices` and `data`. There is no SQL and no database in the path",
    )
    key_axis: str | None = strawberry.field(
        default=None,
        description="(SPARSE) The axis the held id is bound to -- what `keyColumns` is for a table. **Always the axis that layout's `indptr` indexes**, which is what makes the read one contiguous range; a plan is published over a layout where that holds, or not at all",
    )
    key_held: str | None = strawberry.field(
        default=None,
        description="(SPARSE) The name the worker holds the value bound to `keyAxis` under -- what `keyColumns[].axis` is for a table. Equal to `keyAxis` on a landing, where the sample produced it under the axis' name; the parent row's column name on a hop into a matrix",
    )
    value_axes: List[str] = strawberry.field(
        default_factory=list,
        description=(
            "(SPARSE) What comes back is indexed by: every position along these axes that carries a value. **Not keys** -- the client supplies nothing for them and receives all of "
            "them, which is what makes this one object's whole profile. One axis at rank two, so a returned position is a single coordinate and a row of the table that axis "
            "references; two at rank three, where a position is raveled and unravels through `sparseArray.indexOrder` into one coordinate per entry here, in order"
        ),
    )


@kante.type(
    description=(
        "The schema fact one hop crosses. `column`: a `Column.references` hop -- the parent row's column whose values are row ids of the next table -- or, on a hop into a matrix, "
        "the parent table's INDEX column whose values are positions along `axis`. `axis`: the matrix axis crossed, in either direction. Whichever is set, its name is the name the "
        "hop's lookup binds under (`keyColumns[].axis` / `keyHeld`)"
    )
)
class HopVia:
    """Which declared reference a hop follows."""

    column: Column | None = strawberry.field(default=None, description="The column whose values are bound: the parent row's reference column, or its INDEX column when the hop enters a matrix")
    axis: str | None = strawberry.field(default=None, description="The matrix axis crossed: the parent slice's value axis when the hop leaves a matrix, the target's indexed axis when it enters one")


@kante.type(
    description=(
        "One step of a plan's chain through record-land. `hops[0]` is the landing -- the FIELD edge's own target, bound from `sample` -- and every later hop binds from the rows "
        "or slice its `parent` returned, under the name `via` states, and lands one declared reference further: a `Column.references`, a matrix axis a table identifies, or the "
        "same axis walked into the matrix. Execute in list order; a hop's parent always precedes it. `cardinality` says whether to bind a scalar or a list. The server describes "
        "the chain and reads nothing; the client runs it hop by hop with grants it already holds"
    )
)
class Hop:
    """Where a plan lands, or where it can go from there."""

    index: int = strawberry.field(description="This hop's position in `hops`, what a child names as its `parent`")
    parent: int | None = strawberry.field(description="The hop whose result this one binds from. Null only on `hops[0]`, which binds from `sample`")
    cardinality: enums.HopCardinality = strawberry.field(description="ONE: bind each key as a scalar. MANY: bind each as a list (every position a SPARSE parent returned) and expect the keys back per row. A floor: a ONE lookup may still return several rows")
    via: HopVia | None = strawberry.field(default=None, description="The declared reference this hop crosses. Null on `hops[0]`, whose crossing is the plan's `edge`")
    table: TableDataset | None = strawberry.field(default=None, description="The table this hop lands in: the home of its attributes and their `references`. One or the other with `sparseDataset`, never both")
    sparse_dataset: Annotated["SparseDataset", strawberry.lazy("core.types.sparse_dataset")] | None = strawberry.field(default=None, description="The matrix this hop lands in, when `lookup.kind` is SPARSE")
    lookup: LookupStep = strawberry.field(description="How to read what this hop lands in: the rows of a parquet or a slice of a matrix")
    join_path: List[ColumnOptionJoinStep] = strawberry.field(
        default_factory=list,
        description=(
            "The picker's name for this hop: the `(table, column)` reference steps from the landing table to here, exactly what a layer's `colorBys[].joinPath` stores -- so a "
            "stored colouring finds the hop that resolves it, and its key column, here. Empty on `hops[0]`, and empty once the chain has crossed a matrix, which no `joinPath` can name"
        ),
    )


@kante.type(
    description=(
        "One executable answer to 'what is under this point?': map the point along `path` if the plan is not rooted where you probed, sample the field array, then run the hops -- "
        "the landing first, then every declared reference reachable from it, each bound from the one before. Plans are discovered across the fact component -- probe a source image and "
        "the plans of the instance mask derived from it are found through the derivation edge -- but never through a registration: which claims compose is a scene's say-so, and this "
        "query has no scene. A plan takes no coordinate -- it is the same plan for every point, so fetch it once, cache it, and execute per hover locally with zero round-trips. "
        "attributePlans returns instructions, never attributes: anything that wants values runs the plan"
    )
)
class AttributePlan:
    """A coordinate-free recipe: map along the path, sample the field array, run the hops."""

    edge: FieldTransformation = strawberry.field(description="The FIELD edge this plan was built from. The plan's cache key is this edge's (id, version) together with every `path` step's transformation (id, version): the stores and columns of a table are written once, so a deleted or version-bumped edge -- the FIELD, or any step on the way to it -- is the only thing that can stale a cached plan")
    path: List[PlacementStep] = strawberry.field(
        description="The steps from the PROBED system to this plan's root (the FIELD edge's input system -- equal to `sample.system` when the mask's own pixels are the map). Empty when the plan is rooted where you probed. Compose in order, inverting the flagged steps, to map a probed-space point into the space `consumes` and `passthrough` are stated in -- the same contract as `pathToWorld`. The path crosses derivations, levels, lenses and physical spaces, never a registration"
    )
    sample: SampleStep = strawberry.field(description="Where the id comes from: an `ArraySample` to read at the (path-mapped) point, or a `MeshSample`/`NetworkSample` whose id the client already picked")
    hops: List[Hop] = strawberry.field(
        description="The chain, in execution order. `hops[0]` is the landing: the table or matrix the FIELD edge's id keys, bound from `sample`. Each later hop crosses one declared reference from a parent hop, up to the query's `maxJoinDepth`. A client that only wants the landing reads `hops[0]`"
    )


#: The implementations of ``SampleStep``, for the schema's ``types=[...]``. Reachable only
#: through the interface, so dropping one erases it from the SDL silently.
sample_step_types: list[type] = [ArraySample, MeshSample, NetworkSample]

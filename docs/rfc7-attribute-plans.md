# RFC-7: Attribute plans

**Status:** Implemented (July 2026), as amended below: `SampleStep` gained `system`, the
plan's cache key rides on `edge` plus the `path` steps, table→table maps were **rejected
as edges** and landed as `TableColumn.references` instead (see "References, not joins"),
and plans are **discovered across the fact graph** — probe a source image and the derived
mask's plans come back with a `path` (see "The walk", amended).
Worker-facing walkthrough: `docs/attribute-plans-api.md`. Implementation:
`core/logic/attribute_plans.py` (pure builder), `core/types/attribute_plans.py`,
`attributePlans` in `core/queries/coords.py`. Tests: `tests/test_attribute_plans.py`.
**Supersedes:** nothing. This is the read side that the FIELD edge
(`docs/field-transforms-api.md`) implies and does not answer.
**Unchanged:** the edge table, the placement walk, `is_traversable` / `_INVERTIBLE_KINDS`,
`coordinateGraph`, and every existing DuckDB path — this adds a query that reads no store.

> **Amendment (2026-09-02): the plan is a chain, and it carries no statement.** Two changes
> to the surface, both breaking, both argued below where the original decision was made.
>
> *`lookup` became `hops`.* A plan was one sample and one lookup, and "Depth > 1" sat under
> *what this deliberately cannot model*. It now carries the whole chain the schema allows
> from its landing -- `hops[0]` is the old `lookup` (with the old `table` / `sparseDataset`
> beside it), and every later hop crosses one declared reference from a parent hop: a
> `Column.references` into another table, a matrix axis a table identifies (a slice's
> positions, bound *plural* -- `cardinality: MANY`), or that same axis walked **into** the
> matrix from its table, wherever a layout indexes it. `maxJoinDepth` bounds it (default 1,
> cap 4, 0 for the landing alone). What did not change is the boundary this RFC drew: FIELD is
> still the only crossing from geometry into record-land, a hop is still a schema fact read
> back and never an edge, and the client still executes every step -- the server *describes*
> the chain, which is what it already did for one step. See "References, not joins" for the
> update, and `core/logic/join_walk.py` for the walk. A matrix every axis of which a table
> identifies -- refused at creation until now as unreachable -- is legal, because a hop reaches it.
>
> *`lookup.sql` is gone.* Rule 4 below is reversed: the statement was a second copy of
> `keyColumns` + `attributes`, free to drift, and the one field a non-duckdb consumer ignored.
> It is derived by the worker from the structured step -- `core/logic/plan_sql.py`, a
> standard-library-only module the client copies unchanged -- with the bind order this RFC
> pinned and a `MANY` form that binds lists and selects the keys. See "Resolved questions".

> **Amendment (2026-07-24, the migration that introduced it).** Rule 3's justification "because world
> is scene-owned" is stale: `CoordinateSystem.scene` was deleted and a world is
> never scene-owned — every SHARED space is ownerless ("hub" is retired as a word).
> The invariant itself — *never compose **to** world* — stands unchanged; its ground
> is that a world-relative answer is scene-adoption-relative state, not that the
> scene owns the system.

> **Amendment (2026-08-19): a plan may land in a sparse dataset, where an id selects a
> *slice* rather than a row.** The symmetric generalisation to the mesh-collection one below:
> that widened where an id comes *from*, this widens what it lands *in*.
>
> A `SparseDataset` is a matrix over two enumerated axes, one identified by a FIELD edge and
> one by a `references` to a table. Standing at a pixel yields an object id exactly as before;
> what differs is that the id names a contiguous run rather than a row, and what comes back is
> every position along the other axis that carries a value -- which is what "what is in this
> object" means for a matrix.
>
> `LookupStep` therefore gained a `kind` (`TABLE` / `SPARSE`) and, for the sparse shape,
> `sparseStore`, `keyAxis` and `valueAxis`. **Flat with a discriminator rather than an
> interface**, because an interface over these two would carry nothing in common -- one has SQL
> and key columns over a parquet, the other two axes over a zarr group and no database in the
> path at all -- and every client reading a plan would gain a fragment for the privilege.
> `AttributePlan.table` became nullable beside a new `sparseDataset`, one or the other.
>
> **`SampleStep` is unchanged, and that is the load-bearing part.** Its three lists --
> `consumes`, `produces`, `passthrough` -- each describe a value the worker already holds, and
> that is why the protocol works. A sparse target's other axis has no such source, so the
> temptation was a fourth list; it does not belong there. The client supplies nothing for that
> axis and receives every position along it, so it is the *index of the result*, which
> `valueAxis` names, not a key of the lookup.
>
> **A plan is published only from the layout whose `indptr` indexes the id.** From the other,
> the same question is a scan of every byte -- 1 777 ms against 2.2 ms, measured on a 16 um
> matrix. A plan for that is not a slow lookup, it is one nobody should execute, so a dataset
> holding only the wrong layout publishes no plan until the transposed one is registered.
>
> Unchanged: the server still reads no store, and there is still no zarr, numpy or duckdb
> import in `core/logic/attribute_plans.py`. A plan is instructions either way.

> **Amendment (August 2026): a mesh collection may root a plan.** A `FIELD`'s map was
> "the values of an array, sampled per pixel". It is now "the contents of what lives in
> this space", and a **mesh collection** satisfies that as squarely as a mask does: its
> ids ride on the geometry rows, so a client that picked a surface is already holding one.
> What earns FIELD its place as an edge was never the array — it is that *standing
> somewhere yields an id*, which is exactly the line `TableColumn.references` still sits on
> the far side of, because it needs a row first.
>
> Three consequences. `keyedBy` became a discriminated union over a two-member
> `KeyedBySourceKind` (`DATASET | MESH_COLLECTION`) — two, not `DerivationSourceKind`'s
> six, because a lens owns nothing to dereference and a table is already record-land.
> `assert_field_is_array_backed` is now `assert_field_is_dereferenceable`. And
> `AttributePlan.sample` became an **interface**: `ArraySample` (a `ZarrStore`, read at a
> coordinate) and `MeshSample` (a `FabriksStore`, read by nobody — the pick already
> happened). Everything a worker binds with is on the interface; only the store needs a
> fragment. This is the RFC's only breaking change to date.
>
> It does not replace the older route. A collection derived from a mask still reaches
> *that mask's* tables one derivation hop away, sampling the mask
> (`test_probing_a_mesh_collections_system_finds_the_source_masks_plans`); a collection
> that also keys its own table returns both plans, the local one first. Tables that hang
> off the mask and tables that hang off the meshes are different tables, and both are
> reachable. Tests: `tests/test_keyed_by.py`, `tests/test_mesh_layers.py`.

## The problem: the FIELD edge made a question askable that nothing answers

A `FIELD` edge records that a label mask's pixels *are* the map into a table of objects. So
"what is under this pixel, and what do we know about it?" became a question about the graph
rather than about client convention. Nothing answers it.

The obvious answer executes the chain server-side —
`attributesForCS(coord: {x, y, t}, cs)`: walk to the mask, read `mask[t,y,x] = 7`, look up row
7, return its attributes. Four things say no.

**The server cannot read arrays, and should not learn to.** There is no zarr, numpy, s3fs,
fsspec or tensorstore dependency in this repo. `ZarrStore.fill_info()` reads exactly one file,
`zarr.json`, via boto3 (`datalayer/datalayer.py:214`); no chunk key is ever constructed and no
codec is ever run. Clients get scoped temporary S3 credentials (`requestZarrAccess`,
`datalayer/mutations/zarr.py:22`) and mount the prefix themselves. Evaluating a FIELD
server-side means a new dependency, chunk addressing, decompression and a chunk cache — in
order to re-fetch a pixel **the client already has on screen**, because it is rendering that
mask as a label layer.

**The DuckDB path is degraded, and a new query would inherit all of it.** `core/duck.py:41` —
`get_current_duck()` returns a fresh `DuckLayer()` per call, so the `cached_property`
connection caches nothing: a new `duckdb.connect()` plus a new `CREATE SECRET`, never closed,
on every invocation. The `current_duckdb` ContextVar that `DuckExtension.on_operation` sets
(`mikro_server/schema.py:917`) is never read anywhere — the pooling this file was designed for
was never wired up. `RowFilter.clause` is concatenated raw into SQL
(`core/queries/rows.py:70`) on a connection holding S3 credentials. `Query.rows` returns `[]`
for every row (`core/queries/rows.py:11`, a diverged `parseRow` whose first statement discards
its own argument). `Query.instance_mask_view_label` calls `parquet_store.get_row`, which is
defined nowhere. None of it is covered: `tests/test_table_rows.py:45` mocks DuckDB out because
"the test stack has no DuckDB-reachable object store".

**Parquet has no point-lookup index.** `WHERE i = 7` is a scan. Row-group zone maps prune only
if the key is clustered — plausible for a freshly segmented `i`, not for an `instance_id`
after tracking. DuckDB is an analytical engine; this is an OLTP point lookup.

**And this module already refused this query, in prose.** `MeshCollection`
(`core/models/coords.py:428`) explains why it exposes no `meshes` field:

> "the client asks the datalayer for temporary read credentials and queries the Parquet
> directly (e.g. with DuckDB) ... It deliberately exposes no ``meshes`` field: a paginated
> list would look natural, someone would build a UI on it, and it would end up walking tens of
> millions of Parquet rows through GraphQL to feed a render loop."

Attributes-at-a-pixel is exactly the UI-on-a-GraphQL-parquet-read that paragraph refuses. The
answer is not to argue with it.

## The model: four rules

**Rule 1 — the server returns a plan; a worker executes it.** This is the first rule of
`core/models/coords.py` applied to values instead of geometry: *"Edges are facts, paths are
queries."* The plan names the array to sample, the axes to sample it on, the parquet to query
and the columns to select. A zarr+duckdb worker — in the browser, or anywhere — runs it with
credentials it already has.

**Rule 2 — a plan takes no coordinate.** The steps do not depend on the point: "sample the
mask, that gives `i`; look up `i` in that parquet" is the same plan for every pixel. So a
client fetches it once and executes it per hover, locally, with zero round-trips. A
coord-bearing query would be one request per pixel, and could never beat the client reading
its own already-rendered mask. This is the rule that makes the RFC worth writing: it is not a
concession to purity, it is faster than the alternative it replaces.

**Rule 3 — composition was never the objection.** The real invariant is *never compose **to
world***. `path_in_scene` (`core/logic/graph.py:1967`) concedes its answer is unique under
one-truth-per-space and still refuses to compose, because world is scene-owned. Meanwhile the
server *does* compose at query time when the destination is scene-independent —
`phasor.axis_scale` via `Lens.phasor` (`core/types/adataset.py:638`), justified in
`core/logic/phasor.py` as "real arithmetic [that] belongs on the server rather than in every
client that wants a lifetime". A table's space is scene-independent, so this query would be
*permitted* to compose. It composes nothing anyway, so the rule never comes up — but it should
be recorded that the wall has a door, and that this RFC is not standing outside it.

**Rule 4 — the plan carries its own SQL.** *(Reversed 2026-09-02; kept for the record.)* The
server emitted `SELECT "area", "mean_intensity" FROM read_parquet(?) WHERE "t" = ? AND "i" = ?`
— identifiers taken from validated `TableColumn` rows and quoted, values as `?` placeholders,
never interpolated. The worker bound and executed. The reasoning that stood -- identifiers
from validated columns, values only ever bound -- now lives in the builder the worker carries
(`core/logic/plan_sql.py`); what fell was the string riding on the plan, which restated
`keyColumns` and `attributes` and could disagree with them.y.

## The surface

```graphql
attributePlans(system: ID!, maxDepth: Int): [AttributePlan!]!

type AttributePlan {                     # as drafted; since 2026-09-02 `table` and `lookup` live
  edge: FieldTransformation!             # on `hops[0]`, and `hops` carries the chain past it
  table: TableDataset!
  path: [PlacementStep!]!                # probed system -> the FIELD edge's input system;
                                         # empty when rooted where you probed. pathToWorld's
                                         # contract: stored direction, inversions flagged,
                                         # composed by the client. Never crosses a registration
  sample: SampleStep!
  lookup: LookupStep!                    # singular, honestly: see "References, not joins"
}

type SampleStep {                        # zarr worker
  system: CoordinateSystem!              # the array being sampled; == the queried system
                                         # for a mask, a different array-backed system for
                                         # a separate field. `consumes` is in ITS axis order
  store: ZarrStore!                      # -> accessGrant / presignedUrl already exist
  consumes: [String!]!                   # ["y","x"] in the field system's axis order
  produces: [String!]!                   # ["i"] -- per-edge; siblings may differ
  passthrough: [String!]!                # ["t"] -- axes the edge did not consume
}

type LookupStep {                        # duckdb worker
  store: ParquetStore!                   # -> accessGrant / presignedUrl already exist
  keyColumns: [PlanKeyColumn!]!          # axis name -> column + dtype, in bind order
  attributes: [TableDatasetColumn!]!     # what to SELECT -- never *
  sql: String!                           # REMOVED 2026-09-02: derived by the worker from the
                                         # two fields above (core/logic/plan_sql.py). Bind order
                                         # unchanged: the parquet path/URL FIRST, then keyColumns
}
```

`SampleStep.system` was not in the draft. `store` alone under-identifies the array for a
*separate* scalar field, and `consumes` is defined in the field system's axis order — the
worker needs that system to interpret it. It also mirrors how `FieldTransformation.field`
already answers the null-means-self convention rather than exposing it.

`produces` is per-edge on purpose: two FIELD edges off one mask may name their produced axis
`i` and `label_id`. Zip the sampled value against *that edge's* `output_axes`, never a shared
key set.

**Do not assume one row per point.** `(t, i)` uniqueness is a convention; no unique index backs
it. The worker gets rows, plural.

## The running example

A timelapse `(t,c,y,x)`; a per-frame instance mask `(t,y,x)` whose values are object ids; a
table of per-object measurements keyed `(t, i)`; and a second table of intensity measurements
keyed the same way, hanging off the same mask.

    mask (t,y,x)
      ├── FIELD, field = self ──> objects (t,i)      area, mean_intensity
      └── FIELD, field = self ──> intensity (t,i)    integrated, background

`attributePlans(system: <mask intrinsic>)` returns **two** plans. Each carries one
`SampleStep` — read the mask's level-0 store, consume `(y,x)`, produce `i`, pass `t` through —
and one `LookupStep` naming that table's parquet, its `(t, i)` key columns and its attribute
columns.

The client, which is already rendering that mask, reads `mask[5, 100, 200] = 7` from the chunk
it already has, then runs both lookups with `(t=5, i=7)`. No round-trip, no server-side pixel
read, no composition.

## The walk: tables are leaves, and discovery crosses the fact graph

> *Amended (July 2026).* The draft's claim — "the walk is one level" — held for one release
> and then met the first real client: a scene renders the source image, the user hovers
> **it**, and the FIELD edges hang off the *derived instance mask*. The derivation edge
> (mask intrinsic → source lens space, IDENTITY or BY_DIMENSION,
> `value_relation: CATEGORIZED`) sat in the graph, reverse-traversable, and nothing
> followed it. Plans are now discovered across the **fact component**
> (`fact_paths`, `core/logic/graph.py`), each carrying `path: [PlacementStep!]!` — the
> steps from the probed system to the plan's root, the exact `pathToWorld` contract:
> stored direction, inversions flagged, composed by the client. `maxDepth` bounds it.

What the type system forces is narrower than the draft said, and still load-bearing:
**tables are leaves.**

- `assert_edge_rank` refuses the metric kinds (SCALE / TRANSLATION / AFFINE / ROTATION) over an
  INDEX axis, so **an affine edge can never land on a table's index space.** Only a FIELD can.
- FIELD is absent from `_INVERTIBLE_KINDS`, so a table can never reach a sibling table
  through the graph. (Between tables, the fact is `TableColumn.references` — see below.)

The discovery walk is fenced by three refusals, each already an existing predicate:

- **Registrations are never crossed, and a SHARED system is never even stood on** (either
  endpoint — one stray hub-out edge would otherwise flood the walk with everything
  registered there). Which claims compose is a scene's say-so, and this query has no scene.
- **UNMAPPABLE never walks**, in either direction (`is_traversable`).
- **A rank-changing derivation refuses the backward hop** (`is_reverse_traversable`): a
  (y,x) mask embedded in a (z,y,x) volume is honestly unreachable from the volume, and a
  (t,y,x) mask of a (t,c,y,x) timelapse is reachable exactly when its derivation is the
  BY_DIMENSION shape that names the kept axes — the edge's own stored axes are what the
  rank test reads.

Consequently probing mask A can return sibling mask B's plans through their shared parent —
deliberate: the question is "what corresponds to this point", not "what belongs to this
dataset", and the fences bound the answer to grids that honestly correspond. Local plans
sort first; a local-only client filters `path.length == 0`.

FIELD edges are **payload, never connectivity**: they are collected at each reached system,
not walked. That exclusion happens *before* `fact_edges`' primary election on purpose — the
election is kind-blind over cross-container edges, and a FIELD edge into a table is
cross-container, so left in it could beat a derivation edge out of connectivity.

Filter plans on **`input`**, not `field`: a self-dereference stores `field` as NULL (read
through `Transformation.effective_field`), and edges that point *at* a warp field via
`field` must not be followed — their outputs are pixel grids, not tables. The
`table_dataset_id is None` guard covers that.

Two per-plan derivations worth naming. `passthrough` is computed off the **FIELD edge's
input system**, never the probed one — a (t,c,y,x) image's (t,y,x) mask passes `t` through
and must not invent a `c`; this was latent while the two systems were always the same.
`SampleStep.store` resolves `system.data_array` → that `DataArray.store`,
`system.intrinsic_of` → the level-0 store; a **lens-owned** field system owns no array
(`Lens`: "A selection over a dataset. Nothing else.") and is refused rather than guessed —
and a refusal anywhere in the component fails the whole query, because a modelling error's
blast radius grows with discovery and a subset that looks complete is worse than an error.

`LookupStep.keyColumns` come from `table.columns_by_role(COORDINATE)`, matched by axis name.
That mapping is an invariant rather than a guess: `TableColumn`'s docstring says name, type and
unit on a coordinate column "are the same fact as the derived `Axis`".

## References, not joins: table→table maps are schema, not geometry

The draft asked whether a FIELD should name *which parquet column* is the value, so
`objects(t,i) → tracks(instance_id)` becomes expressible (`value_column`, Gap 2 of the FIELD
work). The answer is **no — rejected, not deferred**, and the reason is a boundary principle
worth recording:

> **Transformations relate spaces. FIELD is the single crossing from geometry into
> record-land — it earns its place as an edge because it consumes spatial axes. Once inside
> record-land, a relation between tables does no coordinate work: no walk can use it, no
> metric applies, no placement follows from it. It is a foreign key, and it lives where
> schema facts live — on the column.**

Modelling the hop as an edge would have dragged along a wagon of machinery that is
meaningless for a join — `validity` (is a foreign key "INFERRED"?), `value_relation`,
wrapper semantics, traversability checks — and when most of a model's fields are nonsense
for a new case, the case does not belong in the model. It also dissolves the draft's wart
(zarr sub-selection is a node, parquet sub-selection would have been an edge field): there
is no edge field, so the asymmetry never materializes.

What landed instead is one nullable FK :

```python
TableColumn.references -> TableDataset   # on_delete=PROTECT, related_name="referenced_by"
```

*"`instance_id` is an id into `tracks`"* — the mental model every user of parquet already
has, snapping into the roles that already exist (`ID`, `TRACK_ID` say what kind of key;
the FK says key *into what*). Design decisions, each argued once:

- **The target is the table, never one of its columns.** Which column carries the target's
  row identity is already declared there — its single INDEX coordinate column, the same
  fact as its derived axis. An FK to that column would restate a derivable fact: the
  two-copies-of-one-truth pattern this codebase kills wherever it appears (the dropped
  `kind` column, the removed `DISPLACEMENTS`/`COORDINATES` split). It is also SQL's own
  cleanest form: `REFERENCES tracks`, key column implied by the declaration.
- **Column equivalency was considered and refused.** "These two columns hold the same ids"
  is satisfiable by pairs that identify nothing, and then no lookup can be chained over
  it. Where equivalency is true (`objects.i` and `intensity.i` off one mask), the graph
  already says so — both index axes are produced by sibling FIELD edges — and storing it
  again would be the second copy.
- **Declared at creation, immutably**, in `TableColumnInput.references`; the target is
  created first, which is the natural order (the tracker writes `tracks`, then the table
  whose column references it). Validations in `create_table_dataset`: refused on a
  COORDINATE column (a coordinate places the row; it does not point elsewhere); the target
  must be keyed by **exactly one INDEX axis** (a composite-keyed table cannot be
  identified by a single value) **backed by a real coordinate column** (the degenerate
  no-coordinate table has a single INDEX axis too, but it is synthetic row enumeration
  with nothing to bind in a WHERE).
- **PROTECT**, for the same reason a warp field is PROTECTed: deleting `tracks` out from
  under a column keying it would orphan the meaning of every value in that column.
- **Discovery is on the table types** (`TableDatasetColumn.references`,
  `TableDataset.referencedBy`), where a person looking at tables is already standing.
  `coordinateGraph` stays purely geometric and never grows edges no walk can use.

The plan surface is unchanged by all of this: `lookup` is **singular, honestly** — a chain
is `sample → lookup`, and following a `references` from a returned attribute column is the
client's choice, one more (trivially constructed) lookup away. Multi-table *breadth* was
never blocked: it is **sibling fan-out**, several FIELD edges sharing one `input` mask,
and the implementation returns one plan per sibling.

**Non-goals, loudly:** composite references (columns `(t, i_neighbor)` jointly keying a
composite-keyed table — needs a through-model with explicit column pairs, which is where
column-level FKs legitimately reappear), self-reference (`parent_id` into its own table —
the table does not exist while its columns are validated, and null-means-self is taken),
and server-side lookup chaining. No workload demands any of them; all three are additive
later.

**Update (Aug 2026), on the third of those.** A workload arrived: a layer's colour and filter
pickers -- a mesh layer's first, a label layer's alongside them -- which could name only the
table the ids land in and so could not offer `tracks.mean_velocity` one `references` hop away.
What landed is the *narrow* form — a stored, validated `joinPath` on the picker entry, and an
options query per source (`colorByOptions` over a collection, `labelColorByOptions` over a lens)
that enumerates the candidates by walking `references`. The non-goal named here stood
untouched at the time: `attributePlans` still returned one sample and one lookup, and the
join was still executed by the client. What changed is that the server would now record one
and refuse a broken one, rather than leaving both to a convention.

**Update (2026-09-02): the plan carries the chain.** The next workload was the hover itself:
a client holding a nuclei row wanted the track, and holding a cell's expression slice wanted
the genes -- and then the pathways those genes are in, through a second matrix. Every one of
those is a reference already declared (`Column.references`, `SparseAxisReference`), and the
client was rebuilding the hop from the type, guessing the target's key column from a
docstring. So a plan now carries `hops`: the landing, then one hop per declared reference
reachable from it, each with its own lookup and the name it binds under
(`core/logic/join_walk.py`). The boundary principle above is untouched -- a hop is a schema
fact read back, never an edge; no coordinate walk consults it; the server still composes
and executes nothing. What the non-goal actually forbade was the server *running* the chain,
and it still does not. Two things it took: a matrix can be entered from a table (only along
an axis a layout indexes -- from the other layout the read is a scan), and a matrix every axis
of which a table identifies is no longer refused at creation, since it is reachable now.

## What this deliberately cannot model

- **A pixel read.** The plan says which array and which axes; it never says what is in them.
  The client owns pixels, because the client already has them.
- **An answer.** `attributePlans` returns instructions, never attributes. Anything that wants
  values runs the plan.
- **A scene.** The query is scene-independent by construction, like `coordinateGraph`. The
  moment it takes a `scene:` argument it has become `pathToWorld` with extra steps.
- ~~**Depth > 1.**~~ *Modelled since 2026-09-02*, as `hops`: one sample, then a chain of
  lookups the schema's declared references allow, across tables and matrices, bounded by
  `maxJoinDepth`. Still executed by the client, one hop at a time. What remains unmodelled is
  a hop into a **product-space** table (one keyed by a pair of ids), which no single held value
  can address.

## Current gaps

1. **The parquet point lookup is a scan.** The real fix is clustering/sorting on `(t, i)` at
   write time so row-group zone maps prune — a write-path change
   (`core/mutations/table_dataset.py`) plus a docs note so ingest tooling sorts. Nothing in
   this RFC helps.
2. **`core/duck.py` is broken**: leaked connections per call, a dead pooling ContextVar, and a
   hardcoded `ENDPOINT 'minio:9000'` despite settings deriving `AWS_S3_ENDPOINT_URL`. And
   `RowFilter.clause` (`core/queries/rows.py:70`) is an injection sink on a credentialed
   connection. **None are prerequisites — which is the strongest argument for this shape.**
   They remain real bugs and want their own ticket.
3. **`traverse()` does not follow `field` FKs**, so a warp field's array never appears as a node
   in `coordinateGraph`. Arguably a bug the FIELD work introduced by making fields nodes.
4. `core/logic/tables.py`'s `row_values` / `row_count` / `columns` are typed against the
   **legacy** `models.Table`, not `TableDataset`, and `core/queries/rows.py::parseRow` is
   broken — neither is reusable here.

## Resolved questions

- **Is the SQL string the right call? — It was kept, then removed (2026-09-02).** The
  case for keeping it was that a non-duckdb consumer could ignore it and read `keyColumns`
  and `attributes` instead -- which is the admission that those two fields already say
  everything the string says. Two statements of one fact, one of them free to drift, and
  the moment a `MANY` form was needed the string would have had to grow a variant or the
  worker would have had to rebuild it anyway. It is now built by the worker from the step
  (`core/logic/plan_sql.py`, standard library only, copied into the client; tested against a
  real parquet). Everything the draft pinned still holds there: **bind order is the parquet
  path first** (the `read_parquet(?)` argument, supplied by the worker from its own access
  grant so credentials and locations never appear in a plan), then the key values in
  `keyColumns` order; identifiers quoted from validated columns, values only ever `?`; a
  table whose every column is a coordinate falls back to selecting the key columns; a
  `MANY` lookup selects the keys as well, so a row says which value it answers. Never
  `SELECT *`.
- **Should a plan carry the edge's version? — It already does, with no new field.**
  `version` lives on the `Transformation` interface, so `plan.edge.version` is reachable
  and `(edge.id, edge.version)` is the cache key. The draft's analysis stands: the edge is
  the only staleness vector, because a table's store and columns are written once.
- **Is `attributePlans` the right altitude? — Yes.** `coordinateGraph` following `field`
  FKs (gap 3) remains worth doing for *discovery*, but it could never carry the SQL, the
  key bindings or the store resolution — a plan is a recipe, not a subgraph.

## Verification, as built (`tests/test_attribute_plans.py`, `tests/test_schema.py`)

- The walk is pure over `Transformation` rows — so, **unlike every other parquet path in this
  codebase, it is testable for real**: a plan reads nothing, so there is nothing to mock and no
  object store to be unreachable. No test in the file mocks anything.
- Covered: sibling fan-out to two tables with differently named produced axes (`i` vs
  `label_id`, each plan zipped against its own edge's names); a FIELD onto a pixel grid
  skipped (the warp-field case — ablate the `table_dataset_id is None` guard and it
  reappears as a bogus plan); a FIELD onto the degenerate table skipped (its synthetic
  `object` axis has no column to bind); a lens-owned field refused; an array with no zarr
  store refused.
- The SQL builder is a pure `(columns) -> sql` function, asserted with no database: a
  hostile column name (`a"; DROP TABLE rows; --`) comes back as a quoted identifier with
  its embedded quote doubled, and the string contains exactly one `?` per binding. That is
  the injection regression test.
- Discovery: probing the (t,c,y,x) source image finds the (t,y,x) mask's plan through its
  BY_DIMENSION derivation with a one-step inverted `path` and `passthrough == ["t"]`
  (ablation: compute passthrough off the probed system and a bogus `c` appears); the mask
  probed directly answers `path: []`; siblings correspond through their parent (forward
  then inverted steps, local plans first); a rank-changing (2→3) embedding and an
  UNMAPPABLE derivation are not walked; a sliced-lens hop appears as two inverted steps;
  two datasets registered into one scene's world do **not** reach each other (ablation:
  drop the SHARED-side exclusions in `fact_paths` and the foreign plan appears); a warp
  FIELD edge is payload, never a road; `maxDepth: 1` stops short of the sibling.
- `references`: the happy path (a `TRACK_ID` column referencing `tracks`, readable from
  both ends), plus every refusal (COORDINATE column, composite-keyed target, degenerate
  target) and the PROTECT delete guard.
- SDL: `type AttributePlan` and friends, and `references`/`referencedBy` on the table
  types, asserted in `tests/test_schema.py` — the only thing that catches a computed type
  silently dropping out of the schema.

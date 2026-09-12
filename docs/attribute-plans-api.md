# Attribute plans: what is under this pixel?

A label mask's pixels are the map into a table of objects — that is what a `FIELD` edge
records (`docs/field-transforms-api.md`). This guide is the read side: how a client asks
*"what is under this pixel, and what do we know about it?"* and answers it **itself**, with
DuckDB and zarr, using a plan the server hands it once.

The contract in one sentence:

> **The server returns a plan; a worker executes it.** The plan names the array to sample,
> the axes to sample it on, the parquet or matrix to read and the columns to select — and
> never a value, a credential, a coordinate, or a statement.

Why no coordinate? Because "sample the mask, that gives `i`; look up `i` in that parquet"
is the same plan for **every** pixel. Fetch it once, cache it, and run it per hover,
locally, with zero round-trips — against mask chunks you are already rendering. A
coord-bearing query would be one request per pixel and could never compete.

Why no statement? Because it would be a second copy of `keyColumns` and `attributes`, free
to drift, and the one field a non-DuckDB consumer ignored. The statement is *derived* from
the structured step by the worker — `core/logic/plan_sql.py`, a standard-library-only
module you copy into your client unchanged (the `mikro_next` package carries it).

## The cast

A timelapse `(t, c, y, x)`; a per-frame instance mask `(t, y, x)` whose values are object
ids; and two measurement tables hanging off the same mask:

    mask (t,y,x)
      ├── FIELD, field = self ──> nuclei morphology (t, i)        area, mean_intensity
      └── FIELD, field = self ──> nuclei intensity (t, label_id)  integrated

Note the differently named produced axes — `i` and `label_id`. That is allowed, per-edge,
and it is why you always zip the sampled value against *the plan's own* names.

## Fetch the plans

```graphql
query Plans($system: ID!) {          # $system = the mask's intrinsic system
  attributePlans(system: $system) {
    edge { id version }              # the cache key: refetch when either changes
    sample {
      __typename                     # ArraySample | MeshSample | NetworkSample -- see below
      system { id }                  # where the map lives (== $system for a mask)
      consumes                       # ["y","x"]  resolve the point here
      produces                       # ["i"]      what the id means, per-edge
      passthrough                    # ["t"]      unconsumed axes join the key by name
      ... on ArraySample   { store { id } }   # a ZarrStore: read chunks
      ... on MeshSample    { store { id } }   # a FabriksStore: you already have the id
      ... on NetworkSample { store { id } }   # a KonnektionStore: likewise
    }
    hops {                           # hops[0] is the landing; later hops are joins, see below
      index parent cardinality
      via { column { name } axis }
      table { id name }
      sparseDataset { id name }
      lookup {
        kind                         # TABLE or SPARSE -- the other shape's fields are null
        store { id }                 # ask it for an accessGrant to read the parquet
        keyColumns { axis column { name dtype } }
        attributes { name dtype references { id } }
        sparseArray { path store { id } } keyAxis keyHeld valueAxes
      }
    }
  }
}
```

For the mask above this returns **two** plans (sibling fan-out: one per attached table).
The morphology one, abridged, showing only its landing hop:

```json
{
  "sample": {"consumes": ["y", "x"], "produces": ["i"], "passthrough": ["t"]},
  "hops": [{
    "index": 0, "parent": null, "cardinality": "ONE", "via": null,
    "table":  {"name": "nuclei morphology"},
    "lookup": {
      "kind": "TABLE",
      "keyColumns": [{"axis": "t", "column": {"name": "t"}},
                     {"axis": "i", "column": {"name": "i"}}],
      "attributes": [{"name": "area"}, {"name": "mean_intensity"}]
    }
  }]
}
```

## Build the statement

```python
from plan_sql import build_lookup_sql      # core/logic/plan_sql.py, copied verbatim

build_lookup_sql(hop["lookup"])
# SELECT "area", "mean_intensity" FROM read_parquet(?) WHERE "t" = ? AND "i" = ?

build_lookup_sql(hop["lookup"], cardinality="MANY")
# SELECT "t", "i", "area", "mean_intensity" FROM read_parquet(?) WHERE "t" IN (SELECT unnest(?)) AND "i" IN (SELECT unnest(?))
```

Three rules the builder encodes and a worker must not improvise around:

- **Bind order.** The parquet path/URL first (it is the `read_parquet(?)` argument, from
  your own access grant — credentials and locations never appear in a plan), then the key
  values in `keyColumns` order. Identifiers are quoted from validated declared columns;
  values are only ever `?`.
- **Cardinality.** `ONE` binds each key as a scalar. `MANY` binds each as a **list** and
  selects the key columns too, so every row says which value it answers. It is a floor: a
  `ONE` lookup can still return several rows, and a client holding several parents may run a
  `ONE` step as `MANY`.
- **Rows, plural.** `(t, i)` uniqueness is a convention; no unique index backs it. Handle
  zero rows (an id the table never measured) and several (a re-run appended).

And one to know about: `read_parquet(?)` as a prepared-statement parameter needs a
reasonably recent DuckDB — pin your worker's version rather than string-formatting the
path in, which would defeat the one place the plan is deliberately parameterized.

## Execute it: the hover loop (browser)

You are already rendering the mask, so the sample step costs nothing — read the value from
the chunk on screen. Then bind the landing **by name**, never by position in your own head:

```js
const plans = await fetchPlansOnce(maskSystemId);     // cache against edge {id, version}

function onHover(t, y, x) {
  for (const plan of plans) {
    const value = renderedMask.valueAt(t, y, x);      // SampleStep, free: your own chunk
    if (value === 0) continue;                        // background

    const held = { t, [plan.sample.produces[0]]: value };
    const landing = plan.hops[0];
    const params = [
      parquetUrlFor(landing.lookup.store),            // read_parquet(?) binds FIRST
      ...landing.lookup.keyColumns.map((k) => held[k.axis]),
    ];
    const rows = await duck.query(buildLookupSql(landing.lookup), params);
    show(landing.table.name, rows);                   // rows, PLURAL
  }
}
```

## Execute it: a headless worker (Python)

A worker that is not rendering anything runs both halves itself, with credentials from the
stores' own access grants — a plan never carries them:

```python
import duckdb
import zarr
from plan_sql import build_lookup_sql

# -- SampleStep: the zarr half -------------------------------------------------
grant = request_zarr_access(plan.sample.store)        # scoped, temporary S3 credentials
mask = zarr.open(mount(grant), mode="r")              # axis order = sample.system's axes
value = int(mask[t, y, x])                            # consumes ["y","x"], t passes through

# -- hops[0]: the duckdb half ---------------------------------------------------
landing = plan.hops[0]
grant = request_parquet_access(landing.lookup.store)
con = duckdb.connect()
con.execute(create_secret_from(grant))

held = {"t": t, plan.sample.produces[0]: value}       # zip against THIS plan's names
params = [grant.url, *[held[k.axis] for k in landing.lookup.key_columns]]
rows = con.execute(build_lookup_sql(landing.lookup), params).fetchall()
```

## Hops: the chain past the landing

`hops[0]` is where the FIELD edge's id lands. Every later hop crosses **one declared
reference** from a parent hop and lands one container further — and the server describes
the whole chain, up to `maxJoinDepth` (default 1, at most 4, `0` for the landing alone):

| the hop crosses | `via` | what you hold | `cardinality` | lands in |
|---|---|---|---|---|
| a `Column.references` | `column` = the parent row's reference column | that column's value | inherited | a table, keyed by its INDEX column |
| a matrix axis a table identifies, **leaving** the matrix | `axis` = the slice's value axis | every position the slice returned | `MANY` | that table |
| the same axis, **entering** the matrix from its table | `column` = the table's INDEX column, `axis` = the matrix axis | the row's key | inherited | the matrix, sliced at that position (`keyHeld` says the name) |

The name a hop binds under is always the via's own name: `keyColumns[].axis` (or `keyHeld`
for a matrix) is the parent row's column name, or the parent slice's axis name. Execute in
list order — a parent always precedes its children:

```js
const results = { 0: await run(plan.hops[0], heldFromSample) };
for (const hop of plan.hops.slice(1)) {
  const parent = results[hop.parent];                 // rows, or a slice's positions
  const held = valuesUnder(parent, hop.via);          // by column name or axis name
  results[hop.index] = await run(hop, held, hop.cardinality);   // MANY binds lists
}
```

`run` for a `TABLE` hop is the statement above; for a `SPARSE` hop it is the two reads in
the next section, at the position held under `keyHeld`. A worked chain — mask → expression
matrix → genes table → pathway-membership matrix → pathways table — is
`tests/test_plan_hops.py::test_a_plan_walks_mask_to_matrix_to_table_to_matrix_to_table`.

A table→table hop also carries `joinPath`: the `(table, column)` steps a layer's
`colorBys[].joinPath` stores, so a stored colouring finds the hop that resolves it — and the
key column of its target, which is what a renderer needs to join and used to have to guess.
It is empty once a chain has crossed a matrix; no picker entry can name that.

Not every reference becomes a hop. A table enters a matrix only along an axis one of its
layouts indexes (from the other layout the same read is a scan of every byte); a hop never
revisits a container its own branch already stands in, the landing included; and a
product-space table (one keyed by a pair of ids) is not a hop target. `maxJoinDepth` bounds
the rest.

## Probing through the graph: hovering the source image

In a scene you rarely hover the mask — you hover the **image**, and the mask is a separate,
derived dataset. Plans are discovered for you: probe the image's system and every plan in
its *fact family* comes back, found through derivation edges (and pyramid levels, lenses,
calibrations — never through a registration: what shares a scene's world does not
correspond by that fact alone). Each discovered plan carries a `path`:

```graphql
query Plans($system: ID!) {          # $system = the IMAGE's intrinsic system this time
  attributePlans(system: $system) {
    path { transformation { id version } inverted }   # empty for locally-rooted plans
    sample { system { id } consumes produces passthrough }
    hops { table { name } lookup { keyColumns { axis column { name } } } }
  }
}
```

For a `(t,c,y,x)` timelapse whose `(t,y,x)` instance mask was derived with the usual
BY_DIMENSION-on-kept-axes edge, the mask's plan arrives with a one-step path — the
derivation edge, `inverted: true` (it is stored mask→image; your probe walks it
backwards). The hover loop gains exactly one line:

```js
function onHover(t, c, y, x) {                       // image-space coordinates
  for (const plan of plans) {
    const p = applyPath(plan.path, { t, c, y, x }); // compose steps in order, inverting
                                                    // the flagged ones -- pathToWorld's
                                                    // contract. Here: drop c, keep t,y,x
    const value = maskChunks.valueAt(p);            // then everything as before
    const held = { ...passthroughOf(plan, p), [plan.sample.produces[0]]: value };
    ...
  }
}
```

Three things to rely on, and one to know:

- **`passthrough` is stated in the plan's own space** (the FIELD edge's input system),
  never yours: the image's `c` will not appear in the mask plan's key.
- **Local plans sort first.** A client that only wants what is rooted where it probed
  filters `path.length === 0` and reads a stable prefix.
- **The cache key grew:** a plan is stale when its FIELD edge *or any path step* is
  deleted or version-bumped — cache against all of their `(id, version)` pairs.
- **Unreachable is honest.** A mask whose derivation drops an axis without naming the kept
  ones (a rank-changing edge), or whose relation is UNMAPPABLE, is simply absent — there
  is no path a worker could compose, so none is invented. `maxDepth` bounds the walk if
  you want only near neighbours.

## The reverse direction needs no plan at all

"Highlight every pixel of the row I clicked" is the same correspondence read the other
way, and the graph refuses to walk it (an object is a *set* of pixels — there is no point
to return). You do not need it walked: the plan already told you the mask's values live on
the table's `i` axis, so the highlight is a predicate on pixels you already have:

```js
highlight = (pixel) => pixel === clickedRow.i;        // a shader, not a query
```

## Caching

A plan names columns and stores, never values — so the parquet's contents changing does not
stale it (returning new values is the point), and its schema cannot move (a table's store
and columns are written once). The staleness vectors are the **edges**: the FIELD edge the
plan was built from, and every `path` step on the way to it — deleted, or refined with a
`version` bump. Cache plans keyed on the full set of `(id, version)` pairs (the FIELD edge
plus each `path.transformation`); refetch on a miss.

## Declaring the references a hop follows

A hop is only ever a declared fact read back. Between tables the fact is `references`,
stated as a TABLE `identifiedBy` on the column — this is how tracking looks: segmentation
writes the mask and per-frame objects; tracking writes a `tracks` table first, then the
objects table whose `instance_id` column points at it:

```graphql
mutation {
  createTableDataset(input: {
    name: "tracks"
    data: "<parquet store id>"
    columns: [
      {name: "instance_id", dtype: "BIGINT", axisType: INDEX},
      {name: "duration",      dtype: "DOUBLE", role: ATTRIBUTE},
      {name: "mean_velocity", dtype: "DOUBLE", role: ATTRIBUTE}
    ]
  }) { id }
}
```

```graphql
mutation {
  createTableDataset(input: {
    name: "per-frame nuclei"
    data: "<parquet store id>"
    columns: [
      {name: "t",           dtype: "BIGINT", axisType: TIME},
      {name: "i",           dtype: "BIGINT", axisType: INDEX},
      {name: "instance_id", dtype: "BIGINT", role: TRACK_ID, identifiedBy: [{kind: TABLE, table: "<tracks id>"}]},
      {name: "area",        dtype: "DOUBLE", role: ATTRIBUTE}
    ]
  }) { id }
}
```

Now the plan for the mask carries two hops: the nuclei row at `(t = 5, i = 7)`, and the
track its `instance_id` names — *"what track is under my cursor, and how fast is it
moving?"*, both local, zero server round-trips after the plans are cached. For a matrix the
same fact is a TABLE `identifiedBy` on the axis (`createSparseDataset`), and it is read in
both directions.

Why is this a column FK and not another `FIELD` edge? Because `FIELD` is the single
crossing from geometry into record-land — it earns its place as a transformation by
consuming spatial axes. Between tables, the relation does no coordinate work; it is a
foreign key, and it lives where schema facts live. (The long version, including why the
FK targets the *table* and never a column: `docs/rfc7-attribute-plans.md`. Worked through a
nuclei/cells/tracks experiment, including the case where the two mechanisms legitimately
disagree: `docs/field-vs-references.md`.)

Rules of the road for `references`:

| declaration | outcome |
|---|---|
| a data column (`ID`, `TRACK_ID`, `ATTRIBUTE`, ...) referencing a single-INDEX-keyed table | accepted; readable from both ends (`column.references`, `table.referencedBy`), and a hop in every plan that lands in the table |
| a `COORDINATE` column with `references` | refused — a coordinate places the row in this table's own space; it does not point elsewhere |
| target keyed `(t, i)` (composite) | refused — a single value cannot identify a row there |
| target with no coordinate columns | refused — its `object` axis is synthetic row enumeration; there is no column to look a value up in |
| deleting a referenced table | refused (`PROTECT`) while any column keys into it — delete the referencing tables first |

## What `attributePlans` refuses, and why

| situation | outcome |
|---|---|
| a `FIELD` edge onto a pixel grid (a warp field's registration) | skipped — a registration, not a dereference; nothing to look up |
| a `FIELD` edge onto the degenerate no-coordinate table | skipped — its synthetic `object` axis has no column to bind in a WHERE |
| the field is a lens-owned system | error — a lens is a selection over a dataset and owns no array; resolving through to the dataset would silently ignore the crop |
| the field's array has no zarr store | error — a plan that cannot name its array is not a plan |
| a map out of a *table* written as a `FIELD` edge | error — that relation is `TableColumn.references`, not geometry |

## Three kinds of sample

`sample` is an interface. Everything you bind the landing with — `system`, `consumes`,
`produces`, `passthrough` — is on the interface and reads the same either way; only the
store differs, so it needs a fragment.

- **`ArraySample`** carries a `ZarrStore`. Read the array at the point's coordinates; the
  value *is* the id. This is a label mask.
- **`MeshSample`** carries a `FabriksStore`. **Nothing is sampled**: an id rides on the
  geometry row, so a client that picked a surface already holds one and goes straight to
  the lookup. `consumes` names the axes that pick resolved, not axes to index anything
  with. The store is there for a headless worker that did not do the picking and must read
  the object catalog itself.
- **`NetworkSample`** carries a `KonnektionStore` and reads exactly as `MeshSample` does,
  over a wireframe: the id is the traced object's, never a node's.

Probing a collection that was *derived from* a mask can return both kinds: its own
`MeshSample` plan (`path: []`, tables keyed by the meshes) and the mask's `ArraySample`
plan one forward step away (tables keyed by the mask). They are different tables and both
are real; local plans sort first.

## Performance note, honestly

The parquet point lookup is a scan: `WHERE i = 7` prunes row groups only if the key is
clustered. Sort your parquet by its key columns (`t`, then the id) at write time and
DuckDB's zone maps do the rest; nothing in the plan can compensate for an unsorted file.
For interactive hover, debounce and reuse one DuckDB connection — the plan is designed so
that everything except the binds is constant.

## A SPARSE lookup: two reads, no SQL

When `lookup.kind` is `SPARSE` the hop lands in a matrix rather than a table, and the id
names a *slice* rather than a row. There is no statement to build -- `store`, `keyColumns`
and `attributes` are all null -- and instead:

```
lookup.sparseArray        the layout; ask its store for an accessGrant and open the group at its path
lookup.keyAxis            the axis the held id binds to, always the one that layout's indptr indexes
lookup.keyHeld            the name you hold that id under (== keyAxis on a landing; the parent
                          row's column name on a hop into a matrix)
lookup.valueAxes          what comes back is indexed by
```

Two reads, with the id `i` in hand:

```js
const [lo, hi] = await read(store, "indptr", i, i + 2);
const positions = await read(store, "indices", lo, hi);   // positions along valueAxes
const values    = await read(store, "data",    lo, hi);   // the value at each
```

That is one contiguous range, which is the whole reason `keyAxis` is guaranteed to be the
indexed one: a plan is never published over the layout that would make this a scan.

What you get back is every position along `valueAxes` that carries a value -- one object's
whole profile. A position is a row of the table that axis references, and the plan's next
hop (`via.axis`, `cardinality: MANY`) is exactly that lookup, over all of them at once.

# Keying a table off a label mask: the call sequence

> Every mutation below is exercised end-to-end by
> `tests/test_field_transforms.py::test_the_documented_sequence_runs_end_to_end`, so this
> page cannot rot into fiction without a red test.

A `FIELD` edge is a map given by the **values of an array** rather than by a formula. The
array is a `field`: a coordinate system, and so a node of the graph — not a payload on the
edge. This is what lets a label mask key a table of per-object measurements, and it is the
same kind that carries a displacement field.

Everything below is lineage. None of it is about rendering: a table whose only coordinate
is an `INDEX` axis cannot reach a layer, because both layer paths filter coordinate columns
to `SPACE`.

## The shape of the thing

```
stack (c,y,x)
  │  BY_DIMENSION + CATEGORIZED     drops c; "the values became labels"
  ▼
mask (y,x)                          a node: its own lineage, provenance, placement
  │  FIELD, field = the mask itself  consumes (y,x), produces i
  ▼
objects (i)                         a table whose axis IS its id column
```

Two edges, and they say different things. The first is geometric — the mask sits on the
stack's pixel grid. The second is not geometric at all, and before `FIELD` existed it could
only be written `UNMAPPABLE`, which asserts the opposite of what is true.

---

## 1. The stack

Declare the axes in canonical order: **`c,y,x`, not `x,y,c`**. Channel outranks space, and
the ordering is enforced at ingest (`assert_axis_type_order`) rather than merely conventional
— `resolve_render_axes` derives x/y/z from spatial *position*, so an out-of-order declaration
does not fail, it goes quietly wrong.

```graphql
mutation {
  createADataset(input: {
    name: "raw"
    data: "<zarr store id>"      # the shape comes from the store, not from the input
    scales: []                   # extra pyramid levels, if any
    axes: [
      {name: "c", type: CHANNEL},
      {name: "y", type: SPACE},
      {name: "x", type: SPACE}
    ]
  }) {
    id
    intrinsicSystem { id }       # you need this id in step 2
  }
}
```

### 1b. A lens over it

`derivedFrom` in step 2 names a **lens**, not the dataset — and `createADataset` does not
mint one for you, so this call is not optional. An unsliced lens owns no coordinate system:
its space simply *is* the dataset's intrinsic space.

```graphql
mutation {
  createLens(input: { dataset: "<stack id>", slices: [] }) { id }
}
```

## 2. The mask

The segmentation drops `c`, so the edge is **`BY_DIMENSION`, not `IDENTITY`**. `IDENTITY`
is refused the moment the axis names differ — it would be a rank change wearing an identity's
clothes. Name the axes you keep; the one you don't name is the one that goes.

`valueRelation: CATEGORIZED` says the values became labels. That is the premise step 3 builds
on: `CATEGORIZED` states *that* the pixels are ids, `FIELD` states *which space* those ids
live in.

```graphql
mutation {
  createADataset(input: {
    name: "nuclei labels"
    data: "<zarr store id>"
    scales: []
    axes: [{name: "y", type: SPACE}, {name: "x", type: SPACE}]
    derivedFrom: [{
      kind: LENS                  # which *sort of source* this names, not the map
      lens: "<raw lens id>"
      transform: {                # the map itself, nested
        kind: BY_DIMENSION
        inputAxes: ["y", "x"]
        outputAxes: ["y", "x"]
      }
      valueRelation: CATEGORIZED
    }]
  }) {
    id
    intrinsicSystem { id }   # this is BOTH the input and the field in step 4
  }
}
```

## 3. The table

Declare the id column as a **`COORDINATE` with `axisType: INDEX`**. The table's space is then
the space of object ids, and its axis *is* `i`.

This is consistent with the existing rule rather than an exception to it: a coordinate
column's values are always its coordinates — `x` in nanometres, `i` in object ids.

```graphql
mutation {
  createTableDataset(input: {
    name: "nuclei morphology"
    data: "<parquet store id>"
    columns: [
      {name: "i",              dtype: "BIGINT", axisType: INDEX},
      {name: "area",           dtype: "DOUBLE", role: ATTRIBUTE},
      {name: "mean_intensity", dtype: "DOUBLE", role: ATTRIBUTE}
    ]
  }) {
    id
    coordinateSystem { id residents { __typename } axes { name type unit } }
    # -> residents: [{__typename: "TableDataset"}]
    #    axes:      [{name: "i", type: INDEX, unit: null}]
  }
}
```

Note there is **no `derivedFrom` here**. The lineage is the `FIELD` edge in step 4, which
says strictly more than the `UNMAPPABLE` edge you would otherwise write. If you want the
provenance recorded *as well* — so `lineageGraph` shows the table hanging off the mask
without anyone reading a FIELD edge — add
`derivedFrom: [{kind: DATASET, dataset: "<mask id>"}]`, which is UNMAPPABLE by default and
claims no geometry. See `docs/derivation-api.md`.

> **Contrast — `role: ID`.** Declared as a plain `ID` role, `i` is *data*, and the table
> degenerates to a single `object` axis enumerating **rows**. That is a different enumeration
> from the ids: row 3 may hold `i=42`. It is still the right shape for a genuine measurement
> table (one row per object, nothing keying it), which now takes an honest `UNMAPPABLE` edge.

> An INDEX coordinate column **must not carry a `unit`**. There is nothing to measure between
> object 3 and object 4.

## 4. The dereference

The mask's own system is both the `input` and the `field`. That coincidence is what a label
mask *is*: the array being mapped is the array doing the mapping.

```graphql
mutation {
  createTransformation(input: {
    kind: FIELD
    input:  "<mask intrinsic system id>"     # (y, x)
    output: "<table coordinate system id>"   # (i)
    field:  "<mask intrinsic system id>"     # the same id: its pixels ARE the map
    inputAxes:  ["y", "x"]                   # consumed by the lookup
    outputAxes: ["i"]                        # produced by the value
    validity: VALIDATED
    name: "nuclei labels -> morphology"
  }) {
    id
    kind
    ... on FieldTransformation { field { id name axes { name type } } }
  }
}
```

### The rank rule

```
output_axes(system) == (input_axes(system) − consumed) ∪ produced
```

The axes you do not consume **pass through by name**. This is checked, not assumed:

| input | consumed | produced | ⇒ output must be |
|---|---|---|---|
| `(y,x)` | `y,x` | `i` | `(i)` |
| `(t,y,x)` | `y,x` | `i` | `(t,i)` |
| `(t,i)` | `t,i` | `instance_id` | `(instance_id)` |

A `(t,y,x)` mask consuming `(y,x)` into a bare `(i)` table is **refused**: `t` survives
because the edge did not name it. Deliberately not `BY_DIMENSION`'s one-for-one rule — a
`FIELD` is many-to-one on purpose.

---

## Timelapse: the composite key

`i` is per-frame — object 3 at `t=0` is not object 3 at `t=1` — so the key is `(t, i)`, and
the table declares **two** coordinate columns. Axis ordering does real work here for free:
`TIME` ranks before `INDEX`, so `(t, i)` is accepted and `(i, t)` is refused.

```graphql
mutation {
  createTableDataset(input: {
    name: "per-frame nuclei"
    data: "<parquet store id>"
    columns: [
      {name: "t",    dtype: "BIGINT", axisType: TIME},
      {name: "i",    dtype: "BIGINT", axisType: INDEX},
      {name: "area", dtype: "DOUBLE", role: ATTRIBUTE}
    ]
  }) { coordinateSystem { id axes { name type } } }
}
```

```graphql
mutation {
  createTransformation(input: {
    kind: FIELD
    input:  "<mask system>"      # (t, y, x)
    output: "<table system>"     # (t, i)
    field:  "<mask system>"
    inputAxes:  ["y", "x"]       # t is not named, so t passes through
    outputAxes: ["i"]
  }) { id }
}
```

## Displacement fields: the same kind, a different field

A warp field is the case where the field is **not** the input: it is indexed by the input
space but is a separate array. Its value axis is what says its numbers are offsets rather
than absolute positions — the fact that used to be the `DISPLACEMENTS` vs `COORDINATES` kind
split, now stated once, on the array.

```graphql
mutation {
  createTransformation(input: {
    kind: FIELD
    input:  "<atlas system>"
    output: "<intrinsic system>"
    field:  "<warp field's system>"   # its axes: (y, x, d) where d is DISPLACEMENT
    inputAxes:  ["y", "x"]
    outputAxes: ["y", "x"]
  }) { id }
}
```

- value axis typed `DISPLACEMENT` → the values are **offsets**
- value axis typed `COORDINATE` → the values are **absolute positions**
- **no value axis** → the array is scalar, and a scalar value is one absolute position.
  This is the mask case, and it is why a mask stays a plain `(y,x)` array rather than
  acquiring a phantom length-1 dimension. A scalar field claiming to produce two axes is
  refused.

The difference that matters for deletion: a **separate** field array is `PROTECT`ed — you
cannot delete a warp field out from under a registration that reads it, per the same rule
that refuses cascading a shared space in use. A **self** field is not: the dereference is a fact
about the mask, so deleting the mask takes the edge with it. (Internally a self-dereference
stores `field` as null and reads it back as the input — the same idiom as a level-0
`DataArray` owning no system. You never see this: always pass `field` explicitly, and the
API always answers with it.)

## What is refused, and why

| call | refused because |
|---|---|
| `FIELD` with no `field` | the map *is* the array; without it the edge claims a correspondence it cannot produce |
| `IDENTITY` (or any formula kind) with a `field` | its map is in its parameters; an array would be a second, silent answer |
| `SCALE` / `TRANSLATION` / `AFFINE` / `ROTATION` touching an INDEX axis | object 3 × 2 = object 6. Not a wrong number — a meaningless one. The rank check cannot catch this: `scale: [2.0]` has one entry per axis, which is all it ever asked |
| INDEX coordinate column with a `unit` | nothing to measure between object 3 and object 4 |
| a `FIELD` walked backwards | an object is a set of pixels; the reverse asks for a point where there is a set. `pathToWorld` will never return one inverted |

That last one is not a limitation to work around. It is why a table can never accidentally
place itself in a scene through its mask: reaching world that way needs the `FIELD` walked
in reverse, and it never is.

## What reads this

The edge states the map; nothing here executes it. The read side is **`attributePlans`**
(RFC-7): the server hands a client a coordinate-free recipe — sample this array, look the
value up in this parquet — and a zarr+duckdb worker runs it locally. Walkthrough with
worker code: `docs/attribute-plans-api.md`; design and its resolved questions:
`docs/rfc7-attribute-plans.md`. Relations *between* tables (an `instance_id` column keying
a table of tracks) are deliberately **not** FIELD edges — they are declared foreign keys,
`TableColumn.references`; the boundary principle is argued in RFC-7's "References, not
joins", and worked through a nuclei/cells/tracks experiment in
`docs/field-vs-references.md` — which is the page to read if you are deciding which of the
two a relation you have in hand should be.

# FIELD or `references`? The same sentence, two substrates

> The refusals below are the tested ones:
> `tests/test_attribute_plans.py::test_a_table_cannot_be_a_field`,
> `::test_a_composite_keyed_table_cannot_be_referenced`,
> `tests/test_keyed_by.py::test_keyed_by_refuses_a_table_with_two_id_axes`, and the two
> mechanisms coexisting on one table in
> `tests/test_keyed_by.py::test_a_second_object_space_is_a_reference_not_an_axis`. The
> narrative around them is explanation, not contract.

Two mechanisms say what sounds like the same thing:

- a **`FIELD` edge** (authored by `createTableDataset(keyedBy:)` or `createTransformation`) —
  *the contents of this thing identify rows of that table*
- **`TableColumn.references`** — *the values of this column identify rows of that table*

They **are** the same relation. What separates them is one primitive:

> **Do you need a *place*, or do you need a *row*?**

A `FIELD` needs somewhere to stand and answers from there. A `references` needs to already be at
a row. Everything else on this page follows from that, including the parts that look arbitrary.

> **A FIELD's *target* has two shapes too (2026-08-19).** Everything below is written about
> landing in a table, where an id names a row. It may also land in a `SparseDataset`, where the
> same id names a *slice* -- a contiguous run of (position, value) pairs along the matrix's
> other axis. The relation is unchanged and the edge is the same edge; what differs is only
> whether the thing at the end of it is indexed by rows or by runs.

**A `FIELD` has two substrates**, and they differ only in where the answer was materialised:

| | the map is | the client gets the id by |
|---|---|---|
| a **label mask** | pixel values in a zarr array | sampling the chunk it is already rendering |
| a **mesh collection** | ids on the geometry rows of its fabriks parquet | reading them off the surface it just picked |

Both stand somewhere and get an id back, so both are `FIELD`s and both are keyed the same way —
`keyedBy: {kind: DATASET, …}` or `keyedBy: {kind: MESH_COLLECTION, …}`. The plan they produce
differs only in its sample step (`ArraySample` vs `MeshSample`); a `MeshSample` samples nothing,
because the pick already happened. Everything below is written about a mask, and reads the same
about a collection unless it says otherwise.

---

## The worked example: nuclei, cells, tracks

One timelapse of a confluent monolayer. DAPI and a membrane marker, 60 frames.

```
dapi (t,y,x) ──segmentation──> nuclei mask (t,y,x)      pixels are nucleus ids
memb (t,y,x) ──segmentation──> cell mask   (t,y,x)      pixels are cell ids

nuclei mask ──FIELD──> nuclei (t, i)        area, mean_dapi, cell_id, track_id
cell mask   ──FIELD──> cells  (c)           area, perimeter        [persistent cell ids]

nuclei.cell_id  ──references──> cells       "this nucleus was assigned to that cell"
nuclei.track_id ──references──> tracks      "this nucleus belongs to that track"
tracks (track)                              duration, mean_velocity, displacement
```

`nuclei` is per-frame — `(t, i)`, a fresh id each frame. `cells` and `tracks` are not: their ids
are persistent, so they carry no `t`. That is not decoration, it is forced, and the next section
is why.

Four relations. Two are `FIELD`, two are `references`, and the reason is not that some are
"geometric" in the everyday sense — it is that **only two of them were painted into pixels.**

### Why nuclei-mask → nuclei is a FIELD

You point at pixel `(t=17, y=402, x=311)`. The mask array holds `42` there. That number *is* the
map, evaluated at that position. Nothing else had to exist for the question to be answerable —
no join, no lookup, just an array read.

That is what earns it an edge in the coordinate graph: it **consumes spatial axes**. `(y,x)` goes
in, a nucleus id comes out.

### Why nuclei → tracks is a `references`

You point at the same pixel and ask *"what track is this?"*. The mask does not know. It holds
nucleus ids; nobody painted track ids into it. The answer lives in the `track_id` column of
nucleus 42's row, and to get there you must first *be* at that row.

There is no position to sample. So there is no `FIELD`, and the relation is a foreign key.

### The point that makes it click

**Paint a second mask whose pixel values are track ids, and the same relation becomes a `FIELD`.**
That is a perfectly ordinary thing to produce — a tracking step can emit one — and then:

```
track mask (t,y,x) ──FIELD──> tracks (t, track)      pixels are track ids
```

The relation "this pixel belongs to that track" did not change meaning. What changed is that
somebody **materialised the answer per pixel** instead of per row.

So the distinction is not semantic, and it is not about how the sentence reads in English. It is
about substrate:

| | `FIELD` | `references` |
|---|---|---|
| answer stored | per pixel, in a zarr array | per row, in a parquet column |
| you need | a coordinate | a row |
| server gives you | an executable plan (store, axes, SQL) | a table id, and you write the query |
| lives in | the coordinate graph, as a `Transformation` | the schema, on the column |

---

## The difference you will actually hit: arity

This is where the two stop being interchangeable even in principle.

**A `FIELD` arrives holding more than the sampled value.** The axes it does not consume *pass
through by name*. Hovering the nuclei mask you already know `t = 17` — it is where you are
standing — so the edge lands on a `(t, i)` table and binds **both** keys:

```sql
SELECT "area", "mean_dapi", "cell_id", "track_id"
FROM read_parquet(?) WHERE "t" = ? AND "i" = ?
--                              ▲          ▲
--                    from where you   from the pixel
--                       are standing      value
```

**A `references` column carries one number and no context.** `nuclei.track_id` holds `17` and
nothing else. Which frame? The column cannot say. So a reference target must be keyed by
*exactly one* `INDEX` axis:

> *"a reference target must be keyed by exactly one INDEX axis … A composite-keyed table cannot
> be identified by a single value."* — `core/mutations/table_dataset.py`

Which is why `tracks` and `cells` above are keyed `(track)` and `(c)`, never `(t, …)`. Try it the
other way and the server stops you:

> *"Column 'cell_id' references table 'cells', but a reference target must be keyed by exactly
> one INDEX axis (its axes are [t:TIME, c:INDEX])."*

**Same relation. Different available arity. Entirely because one has a position to draw on.**

### The same constraint, read forwards

Suppose your cell segmentation really is per-frame — ids restart every frame, so `cells` is
genuinely `(t, c)`. Then `nuclei.cell_id` **cannot exist**, and this is not a limitation to work
around; the column would be a lie. `42` in a per-frame id space means nothing without the frame,
and a column carries one number.

What you do instead is sample: put a `FIELD` off the cell mask onto `(t, c)`, and hover answers
"which cell is at this pixel" with `t` supplied by where you are standing. Two `FIELD` edges off
two masks, two plans, no reference at all — the sibling fan-out shape.

So the arity rule is not bureaucracy. It is the model telling you that a per-frame association is
only answerable *at a position*, and a cross-frame one is answerable *from a row*. Which mechanism
you get is decided by whether the id space has a `t` in it.

For the `cells (c)` version above — persistent ids, painted the same in every frame — both work,
and the next section is about what happens when they disagree.

### The mirror image

Both refuse the *degenerate* table — one with no `COORDINATE` columns, whose axis is a synthetic
`object` enumerating rows. Neither can bind a value to a column that does not exist. `keyedBy`
says so at write time; `references` says so in `_resolve_reference_target`. Where the two agree,
they agree for the identical reason, which is a good sign the split is real and not arbitrary.

---

## A subtlety worth knowing: FIELD and `references` can disagree, on purpose

`nuclei.cell_id` says *"this nucleus was **assigned** to cell 8"* — the output of whatever
assignment step ran, stored in the row.

Sampling the **cell mask** at a nucleus pixel says *"cell 8 is **painted** at this pixel"*.

In a sparse field these always agree. In crowded tissue they do not: a nucleus whose edge pixels
fall under a neighbour's membrane, an assignment done by centroid where the centroid lands in the
wrong cell, a nucleus the cell segmentation missed entirely (`cell_id` null, cell mask reads `0`).

Both facts are true and they are *different facts*. The `FIELD` answers **"what is here"**; the
reference answers **"what did the pipeline conclude"**. That is the strongest argument that these
are two mechanisms rather than one with two spellings — collapsing them would force you to pick
which of two true statements to keep.

---

## What is refused, and where

| you write | outcome |
|---|---|
| a `FIELD` whose `field` is a **table's** coordinate system | refused **at write** — `assert_field_is_dereferenceable`, naming `TableColumn.references` |
| a `FIELD` whose `field` is a **lens'** system | refused at write — a lens is a selection and owns no array |
| a `FIELD` whose `field` is a **bare space** — nothing lives in it | refused at write — nothing there carries ids, so standing in it dereferences nothing |
| a `FIELD` whose array has **no zarr store yet** | allowed at write, refused when a plan is built — a store is attached after its array row exists |
| `keyedBy` a table with **two `INDEX` axes** | refused — one place holds one id, so one source supplies one |
| `keyedBy` a **lens**, a **table** or a bare **coordinate system** | not expressible — `KeyedBySourceKind` has two members, so the schema refuses it before any resolver runs |
| `references` a table keyed `(t, i)` | refused — a single value cannot identify a composite-keyed row |
| `references` on a `COORDINATE` column | refused — a coordinate places the row; it does not point elsewhere |

The first row used to be a read-time refusal only: `createTransformation` would write the edge and
it would fail the day somebody asked for plans. It is now checked where the edge is written, by
the same function the read path calls, so the two cannot drift.

---

## Neither one composes

Worth stating plainly, because it is the one place they are genuinely identical.

- `FIELD` is absent from `_INVERTIBLE_KINDS` and excluded from `fact_paths`' frontier, from
  `is_derivation_edge` and from `lineageGraph`. It is *payload, never connectivity*.
- `references` is not an edge at all. No *coordinate* walk consults it. Since the picker options
  (`core/logic/column_options.py`, Aug 2026) one **schema** walk does — to enumerate the columns a
  layer may be coloured or filtered by, and to check a stored `joinPath` against them. That walk
  never touches the coordinate graph past its first table, which is exactly the separation this
  document is about.

So neither one composes *as geometry*, and a table is a leaf of the coordinate graph. A plan
is one sample and then a chain of lookups -- `hops` (since 2026-09-02): following
`nuclei.track_id` into `tracks` is the plan's second hop, carrying the target's store, its
`INDEX` column and the name to bind under, so you no longer read those off the type and guess.
The chain crosses matrices too, in both directions. What has not changed is who runs it: the
server *describes* every hop and executes none, exactly as it always described the one.

A layer's `colorBys`/`filterBys` -- a mesh layer's, and since Aug 2026 a label layer's -- may
*store* such a chain as a `joinPath`, validated hop by hop at the mutation boundary, and a plan's
table→table hop carries the same `joinPath` so the two meet.

---

## Choosing, in one question

> **Could a worker answer this from where it is standing — by reading what it already drew?**

Yes → `FIELD`. The pixels, or the geometry, are the map; the server can hand out a plan that
resolves it.

No, it needs a row first → `references`. It is a foreign key, and it lives on the column.

And if the answer is *"not today, but we could paint it"* — that is a real design choice, not a
modelling accident. Painting it buys per-pixel lookup with no join; leaving it in a column keeps
one row per object and lets the association disagree with the pixels where the pipeline says it
should.

## See also

- `docs/field-transforms-api.md` — the call sequence for authoring a `FIELD`
- `docs/attribute-plans-api.md` — executing a plan, and the "one hop further" reference follow
- `docs/rfc7-attribute-plans.md` — "References, not joins", where the split was argued and
  `value_column` (a `FIELD` naming a parquet column) was rejected rather than deferred

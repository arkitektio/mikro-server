# Visualising a sparse dataset

A `SparseDataset` has no geometry of its own. It is a matrix over two or more
identified axes — objects × features — and *where those objects are* is not
something it knows. So "draw this matrix" is never answered by the matrix; it is
answered by whatever identifies its object axis.

That is the whole of this document, and the reason there is **no sparse layer
kind**.

## Where the geometry comes from

A `SparseAxis` is `identified_by` one of three things (`core/enums.py`,
`IdentificationKind`), and each names a thing that already knows how to be drawn:

| identified by | the geometry | the layer that draws it |
|---|---|---|
| `DATASET` | a mask's pixels — the array whose values are the object ids | `LabelLayer` |
| `MESH_COLLECTION` | the collection's surfaces, keyed by object id | `MeshLayer` |
| `TABLE` | the table's `COORDINATE` columns — a position per row | `PointLayer` |

In every case the matrix supplies **values** and the identification supplies
**positions**. A colouring is then one slice: `at: [{axis, value}]` names a
position along each axis the matrix identifies itself by, and what comes back is
one value per object, which is exactly what a colouring is.

## Why there is no "matrix layer"

It is tempting to add a layer that renders a matrix directly — a lattice of bins
as an image on a plane, say. Do not; it would duplicate the label path and be
worse at it.

The case that motivates it is a regular lattice, and a regular lattice of object
ids **is a mask**. The Visium HD ingest already uploads its bins that way
(`testing/visium_hd_sparse_mock.py`: *"the bin lattice, as a label mask: the
array whose values are the ids"*), so the lattice case is the label case. And
the label path draws it better than a textured plane could: it streams through
the brick octree with view culling, prefetch margins, coarse-to-fine priority
bands and real level of detail, none of which a single plane has.

If a lattice ever arrives with no mask, rasterising it into one is the cheaper
answer than a second renderer — it buys all of the above for free.

## What a client has to do

The server never opens a chunk (`datalayer/sporadik.py`: *"What this module
deliberately does not do: open a chunk"*). A colouring is read by the client,
directly from the store:

1. **Pick the layout.** A slice along an axis is one contiguous read only from
   the layout whose `indptr` indexes *that* axis. Ask the other one and there is
   no range to read at all, only a scan — measured at 1 777 ms against 2.2 ms on
   a 16 µm matrix. `_build_sparse_color_by` refuses a colouring the server knows
   would scan, rather than publishing it and letting it be slow.
2. **Read two ranges.** `indptr[i:i+2]` gives the run; `indices[lo:hi]` and
   `data[lo:hi]` are the run. Nothing else is fetched. `indptr` is small — one
   entry per position, ~150 KB for a transcriptome — so a client holds it and
   every later slice is two chunk reads.
3. **Take the metadata from GraphQL, not from the store.** `SparseLayout`
   publishes `path`, `indexedAxis`, `indexOrder`, `nnz`, `dtype`, `chunks` and
   `rangeReadable` — everything a reader would otherwise recover by fetching five
   `zarr.json` objects per layout.

`SparseStore.bucketKey` is `"zarr"`, so a general zarr grant already covers every
sparse store; no separate credential is needed.

## Positions are names, not indices

`at` takes a **row index**, not a gene name. The names live in the table the axis
references (`SparseAxisReference.references`), keyed by its single `INDEX`
coordinate column.

This is deliberate and it is what keeps a picker usable: `labelColorByOptions`
offers **one option per matrix, never one per position**, so a 19 059-feature
matrix is one row in a dropdown rather than 19 059. Finding the gene is a query
against the referenced table, which the client already holds a grant for.

## Rank three and above

A rank-two matrix identifies itself along one axis; a rank-three one (cell ×
metabolite × adduct) along two, and `at` must name **every** one of them — an
`at` naming a different set is refused.

At rank three a slice's `indices` are the remaining axes *raveled together*, and
`indexOrder` says in which order. That is the one fact in the format that cannot
be recovered from the bytes: reading it wrong does not fail, it reads a different
cell. Unravel through it and keep only the entries matching the other named
positions.

## What a colouring is not

A **filter** cannot read a matrix. `LabelFilterByInput.table` and `column` are
non-null, so there is no sparse arm to send; a rule names a column of a table.
The options query offers sparse candidates to both surfaces, so a picker must
narrow them out in filter mode rather than trusting the offer.

A sparse colouring is also **always measured** — a slice is a value per object,
so it takes a colormap over its range and never a qualitative palette. Nothing
stores categories sparsely, because the zeros would be a category too.

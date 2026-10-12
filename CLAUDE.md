# mikro

Notes for whoever changes this service, person or agent.

## Lenses

A lens is a selection over a container that lives in a coordinate system, one row per
(container, normalized selection), and every container has its whole lens from creation
(`core.logic.coordinate_system.whole_lens`). `ArrayLens` selects by slices and, when sliced,
owns the crop's space and the edge back into the grid; `TableLens`, `SparseLens`, `MeshLens`,
`NetworkLens` and `AnnotationLens` select by value windows in the container's own space and
own nothing, but a window *bounds*: a windowed lens has an extent on the axes it windows and
is silent elsewhere (absence, never an infinity, is how a box says nothing about an axis),
so `overlapsAnnotation`, `placeableIn` and `inView` answer for every kind. A lens carries
`creator`, `created_at` and task provenance (the first to ask for a selection is its creator;
a whole lens carries its container's) and is owned by its maker or its container's owner.
**Every layer and chart layer draws through a lens**, whatever its kind; a layer mutation
takes the lens or the container (its whole lens), and a point, track or series layer hands
its lens' windows on as `windowFilters`, a collection layer as `clip` -- derived, never
stored. **A layer narrows nothing outside its lens**: there is no channel index anywhere. An
intensity, label or phasor layer's lens pins one channel (`createLens(lens:, slices:)` cuts
it; `assert_single_channel` refuses otherwise), an RGB layer names three single-channel
component lenses cut from its `lens`, and a channel or phasor node of an image layer's render
graph names the single-channel lens it samples (`Layer.channel_lenses` indexes them for the
delete guard). Scene bootstrap cuts one sub-lens per channel (`create_sub_lens`), so the
lenses a dataset's channels are drawn through are rows shared by every scene; `Lens.scenes`
therefore answers for every selection within a lens (`lens_ids_within`). The same rule holds
off the array: a SPARSE colour or filter picker entry names a `SparseLens` whose windows pin
one position on every axis the matrix identifies itself by (`min == max`), never a dataset
plus positions; `_resolve_sparse_slice` checks the lens, and a lens a picker names is held by
the delete guard like a lens a layer draws through. Every create-lens mutation composes over a lens (slices compose, windows intersect),
and `createLens` also cuts from a `box` in any space the dataset reaches or from an
`annotation`; the result is always the container's row for what is selected. A lens is
reached across a polymorphic foreign key: prefetch it (`Lens.objects.with_sources()`), never
`select_related` it, or you get the base row. Every kind is hosted *and announced*
(`mikro_server/contract.py`, `mikro_server/service.py`), described in its container's
vocabulary by `core/descriptors.py`; `@mikro/is_whole` tells a crop or a window from the
whole lens a container is born with. **A lens states the spec of what it selects**, in its
container's vocabulary (`ArrayDatasetSpec`, `TableDatasetSpec`, `SparseDatasetSpec`), as `spec`
and as the `@mikro/spec` descriptor. One rule
for every kind -- an axis the selection holds to one position does not count -- which for an
array is the shape its slices cut out and for a windowed lens a window whose `min == max`; a
window that only bounds changes nothing, because nobody scans the rows. It is
computed by the kind's `derive_spec` and **materialized on `Lens.stored_spec` when the row is
made** (`coordinate_system._mint`, the one place a lens is written) -- safe because a lens and
its container are immutable, and stored because `LensFilter.arraySpec` / `tableSpec` /
`sparseSpec` read it in SQL. A lens made any other way (a test's ORM row) has no spec until
`merge_duplicate_lenses` gives it one. `ArrayDataset.stored_spec` and
`TableDataset.stored_spec` are the same pattern (the point/track gates read the table's); a
sparse dataset's is derived, having no filter.
The pure arithmetic -- normalizing, composing, boxing,
cutting -- lives in `core/logic/coords.py` and is oracle-tested in `tests/test_selection_math.py`
and `tests/test_lens_geometry.py`.

## Migrations and jobs

A container of this image only serves. An installer (konstruktor) prepares the database with
`arkitekt-service run migrate` once per build — before the first start, and in an update before
anything is recreated — and runs everything else as a job the image offers by name
(`konstruktor job run mikro <job>`). Nothing is migrated, seeded or repaired at start.

**Read the rules before changing a model, a management command or `mikro_server/contract.py`:**
<https://github.com/arkitektio/arkitekt-service/blob/main/docs/migrations-and-jobs.md>. The ones that are broken most easily:

- A model change and its migration are one commit.
- A `RunPython` step sits alone in its migration, between the schema migration that adds what it
  writes and the one that drops what it read. Postgres will not alter or index a table in the
  transaction that just rewrote its rows under deferred constraints, and Django defers index
  creation to the end of a migration -- so a data step beside any `AddField` or `RemoveField`
  fails on a populated hub and passes on an empty one. The lens migrations (`core/0003`-`0007`,
  `chart/0002`-`0003`) are the pattern.
- Within a major, a migration leaves a schema the previous release still runs on: a failed
  update, and a rollback, start the old build on the migrated database. What cannot do that
  is a `feat!:`.
- A `manage.py` command an operator should be able to run on a hub is declared in `jobs=` in
  `mikro_server/contract.py`; one nobody runs is deleted. Any job is safe to run again.
- What `setup=` names runs for every build, after the migrations: it changes nothing the
  second time and needs nothing but the database and the config.
- Existing data is rewritten by a data migration when that needs only the database (it runs
  once, with the service stopped), and by a re-runnable job in `setup=` when it needs the
  service's code or its storage. There is no upgrade step, and nothing is keyed to a version.

What this service declares:

- Setup, in order: `ensureadmin`, `merge_duplicate_lenses`. The second normalizes stored lens
  selections, gives every container of every kind (array, table, sparse, mesh, network,
  annotation collection) its whole lens, folds duplicate array lenses onto one row and gives
  every lens the spec its selection denotes (`Lens.stored_spec`); the
  database refuses duplicates since the lens tables were split, so it is a guard for rows
  written by hand. It changes nothing the second time and reports what it leaves in place.
- Other jobs: `purge_orphaned_stores`, `respec_datasets`, `backfill_parquet_schemas`. The backfill is safe
  beside a serving release and reports what is still owed; it is not in the setup, so an
  operator runs it.

`tests/test_prepared.py` holds the contract to this: migrations committed, every job a command
of this service, the setup run twice. In `deployments/next` the service runs
`arkitekt-service standalone --debug`, which migrates and then serves: restart its container
to apply a new migration.

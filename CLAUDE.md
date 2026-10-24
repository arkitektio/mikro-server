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
`annotation` (over the dataset, or within a `lens`: what that lens selects under the shape's
box); the result is always the container's row for what is selected. A cut to an annotation
is recorded beside the lens (`LensCut`, read as `Annotation.derivedLenses`), and data computed
from a shape names it as an ANNOTATION source of `derivedFrom` (`Transformation.annotation`,
read as `Annotation.derivedResidents`): both a record of the act, never a relation in space. A lens is
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

## Edges and collections

**A composite edge is one row.** A SEQUENCE (what the ingest writes for a stepped lens and a
downsampled pyramid level) carries its ordered steps in `Transformation.steps` as
`{kind, params}` dicts, read everywhere as `step_pairs`; a BY_DIMENSION carries its **parts** in
`Transformation.parts`, side by side (`{kind, input_axes, output_axes, params[, field]}`, one
`PartKind` each, disjoint on both sides, as OME-NGFF's `byDimension`), and its row
`input_axes`/`output_axes` are the parts' axes in part order. **Nothing passes through
implicitly**: an axis no part names is one the edge says nothing about, and a pass-through is an
IDENTITY part. FIELD is a part kind, not an edge kind: a FIELD part's array is linked through
`TransformationField` (PROTECT; a mask's self field is `"self"` and links nothing), and one whose
values land on an INDEX axis is a **key** (`graph.is_dereference`, `dereference_q`) that places
nothing. There are no child rows, no `parent`, and every row joins two systems. A step is not an
edge: it has no id and no history, `SequenceTransformation.steps` reads it and nothing refines
it; a BY_DIMENSION publishes its `parts` and is refined part by part (`updateTransformation(part:)`),
so `version` and `transformVersion` move on the row a client reads. A part over an INDEX axis is
not a metric claim: it is a relabelling the product writes (`cell_id = cell + 1`).
`tests/test_composite_edges.py` and `tests/test_by_dimension_parts.py` hold this; the model is
`docs/by-dimension-parts.md`.

**A collection has a name, optionally, and a label always.** `MeshCollection` and
`NetworkCollection` are identified by their store and labelled by `name` or, failing that, the
store's key (`label`); the server writes the label wherever it names a collection (its own
system, a derivation edge, a refusal). `specVersion` is read off the store's manifest, never
stored on the collection. `Axis.long_name` stays beside `description` on purpose: a label and
a prose field.

**A fixture is hardware around the sample** -- a coverslip, a dish, a holder -- as one binary
kulisse model (`KulisseLike`, a `KulisseStore` from arkitekt-datalayer: one self-contained binary
glTF, read and checked when `finishKulisseUpload` runs) in a unit-carrying space of its own:
`Fixture`, `FixtureLens`, `FixtureLayer`. The box is the store's, never declared or re-read here.
10.2.0 shipped it as a "CAD model"; nothing of that name is left.

## Metrics

A **metric** (`metric/`, an app like `chart/`; core never imports it) is one value recorded about
an annotation or a dataset -- a quantity (`magnitude` in a pint `unit`) or an opaque `code` a
process emitted, never both (a check constraint), under a producer-namespaced `key`
(`ais_suite.measure_profile/threshold_distance`, `user:<sub>/spine_count`). It is an
observation: what it *means* is kraph's, which reads every metric. Metrics are **immutable**: a
correction `supersedes` (PROTECT), and there is no update. The object a metric is about is a
`MetricTarget`, made on its first metric (never by the object's own creation paths), with one
foreign key per targetable structure; deleting the object cascades to the target and stops at
`Metric.target` (PROTECT): an object something was measured about cannot be deleted while a
metric of it stands, cascades included.

Every write records a feed entry **in its own transaction** (`arkitekt_service.feed`,
`@mikro/metric`), which kraph reads with its instance key at `POST _feed` (`metric/feed.py`;
only the readers named there may read). The feed is what a reader relies on; the
`@mikro/metric` signal is a nudge, sent for `createMetric` only: `createMetrics` (at most 5000,
one transaction) is a bulk insert and announces nothing. Keep each write's transaction short --
the feed hands out nothing past the oldest transaction open in the whole Postgres cluster.

## Datalayer

S3-backed stores (`MediaStore`, `ZarrStore`, ... ) come from the shared `arkitekt-datalayer`
package (`arkitekt_datalayer`, app label `datalayer`), not from this repository. mikro wires
its mutations in `mikro_server/schema.py`, declares the stores in `mikro_server/service.py`,
and narrows the config in `MikroDatalayerSettings`. A change to a store, a grant or the purge is
a change to that package. Its migration history and every app's here restart at `0001` with a
breaking release; `core/0001` carries the `cube` and `vector` extensions by hand.

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
  fails on a populated hub and passes on an empty one.
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

- Setup, in order: `ensureadmin`, `restore_descriptors`, `merge_duplicate_lenses`. The second
  stores the descriptors of every row that misses a declared key (rows from before the key
  existed), announcing nothing; `--all` describes every row again after a describer changed what
  a key means. The third normalizes stored lens
  selections, gives every container of every kind (array, table, sparse, mesh, network,
  annotation collection) its whole lens, folds duplicate array lenses onto one row and gives
  every lens the spec its selection denotes (`Lens.stored_spec`); the
  database refuses duplicates since the lens tables were split, so it is a guard for rows
  written by hand. It changes nothing the second time and reports what it leaves in place.
- Other jobs: `purge_orphaned_stores` (from `arkitekt_datalayer`), `respec_datasets`, `backfill_parquet_schemas`. The backfill is safe
  beside a serving release and reports what is still owed; it is not in the setup, so an
  operator runs it.

`tests/test_prepared.py` holds the contract to this: migrations committed, every job a command
of this service, the setup run twice. In `deployments/next` the service runs
`arkitekt-service standalone --debug`, which migrates and then serves: restart its container
to apply a new migration.

## Descriptors

Every descriptor is stored: each described model is a `Described`
(`arkitekt_service.service.described`) with a GIN index on `stored_descriptors`, recomputed on
every save. Signals and the `descriptors` field read the stored value, and every list filter of a
described structure takes `requires` (`core.logic.descriptor_filter`, one indexed condition).
Nothing is computed when asked. The vocabulary is `mikro_server/vocabulary.py`, the describers
`core/descriptors.py`.

What a save of the row cannot see is restored by whoever writes it (`core.descriptors.restore`):

- a container's axes, arrays, columns and layouts are written after its row: `whole_lens`
  restores the container when it mints the lens every creation ends with;
- a space's axes: the axis writers in `core/logic/graph.py`;
- a dataset's `@mikro/value_kind`: every save or delete of a `Transformation` restores the
  datasets whose grid it leaves, and their array lenses (`core/signals.py`);
- `@mikro/n_annotations`: every save or delete of an `Annotation` restores its collection and
  that collection's lenses; `createAnnotations` stamps its bulk insert and restores once.

A restore inside the transaction that created the row adds no UPDATED: its CREATED reads what is
stored at the commit. A test that seeds rows by hand gets their descriptors through the same
saves, so seed through the writers above.

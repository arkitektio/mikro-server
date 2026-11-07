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
**A lens has no name of its own to edit**: it is named by the positions it pins (`coords.pins`:
a slice keeping one index, a window with `min == max`) -- the labels at its container's anchors
whose every axis it holds to the same position, most specific only, a person's `PositionLabel`
(`setPositionLabel`, guarded by the container's owner) over ingest's `ChannelLabel` -- and, for
a crop cut to a shape, by that shape's name. `Lens.name` caches the result for search; its
inputs change, so their writers refresh it in the same transaction (`core/logic/lens_names.py`,
wired in `core/signals.py`; `_mint` names a lens as it is made) and `merge_duplicate_lenses`
repairs drift. The container's name is never in it; `Lens.label` puts it in front.
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
read as `Annotation.derivedResidents`): both a record of the act, never a relation in space. A shape
made from a shape (refined, converted, split, merged) is neither: it names its sources in
`createAnnotation(s)`'s `derivedFrom` and is an `AnnotationDerivation` row (`core/logic/annotation_lineage.py`,
read as `Annotation.derivedFrom` / `derivedAnnotations`), **never an edge** -- a refinement inside one
collection would be a space's edge to itself, and the placement walks would take it for the
collection's primary parent. The `lineage` walk reaches a shape only from a shape (`kind: ANNOTATION`
roots and SHAPE_DERIVATION hops; its pks are text, because a shape's is a UUID). A lens is
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

## Axes, tables, scenes and charts (RFC-11, RFC-12)

**A coordinate system assumes nothing; its consumers check what they need.** Each axis is
*measured* (`coords.METRIC_AXIS_TYPES`: SPACE, TIME, MICROTIME, SPECTRUM) or *enumerating*
(`ENUMERATING_AXIS_TYPES`: CHANNEL, INDEX, CATEGORY), and may declare a `metric_group` -- the
axes that share one distance. A SPACE axis is in `"space"` unless it says otherwise, **in code**
(`coords.effective_metric_group`), so an older space needs no migration; groups only on
measured axes, one dimension per group. A ROTATION (whole edge or part) needs the axes it
*mixes* to share a group (`graph.assert_mixed_axes_share_metric`; a passed-through axis needs
nothing); AFFINE, SCALE and TRANSLATION need only measured axes. A CATEGORY (positions are
values) has no unit, is never on a grid, and takes no arithmetic: a BY_DIMENSION passes it with
an IDENTITY part.
**A table's space is its frame**: its coordinate columns (SPACE, TIME, INDEX, CHANNEL) or a
synthetic `object`; its measurements stay columns, as an array's intensity is a value and not an
axis. A long-form table declares a CHANNEL coordinate (`(cell, c, mean)`), unitless, and each
data column what it `varies_over`; a key from a mask leaves the channel to the reader
(`graph.reader_selected_axes`, no attribute plan) or passes it through by name from a source
with a `c`. A **gate** is a table lens window on a numeric column (`area >= 50`,
`coordinate_system.windowable_names`, which `merge_duplicate_lenses` normalises against too).
**A scene shows where, a chart shows how much**: in a scene every screen axis is a world axis
and values are appearance; in a chart values are position and at most one axis comes from the
graph. Every chart has a world -- the domain its layers' rows live in -- and every layer is
placed into it (`Chart.kind`): an AXIS chart draws its world, one measured axis (TRACE, SERIES,
ANNOTATION, laid along it by edges); a VALUES chart's world is whose rows (a table's space, not
drawn), its POINTS (`x/y/z_column`) and DISTRIBUTION (`value_column`, `group_column`, `bins`)
layers placed into it and binding columns by name (`chart.assert_value_layer`: placed, numeric,
neither kinds nor units need agree -- `Chart.layout` STACKED/SHARED/OVERLAY, remembered with the chart, lays layers in other units beside each other and overlays only those that measure the same thing, and a VALUES chart refuses OVERLAY). A chart layer is retuned as a scene layer is: `updateChartLayer` restyles any kind, and what a layer reads (lens, columns, grouping, bins) is its kind's own update (`updateTrace/Series/Points/DistributionChartLayer`, `chart.logic.update_layer`), held to the creation rules on the merged state, re-placed only when the lens changes. Same world, same rows: a derived PCA table draws
beside its source. A chart is never both. A PCA or UMAP is a derived table (IDENTITY over the frame, a TABLE key back,
parameters in provenance), not an edge. **A grid shows which** (`grid/`): its world is one or
two enumerating axes -- rows, columns: members with no distance -- and its HEATMAP (an array or
sparse lens leaving those axes free) and CELLS (a table lens, coloured by `value_column`) layers
are placed into it and cover its axes (`grid.logic.grid`). `docs/rfc11-tables-and-charts.md` is the model,
`docs/rfc12-compositions.md` the plan for grids, diagrams and boards; `tests/test_axis_structure.py`,
`test_table_space.py`, `test_chart_values.py` and `test_table_plot.py` hold it.

## Deletes follow lineage

Nothing is deleted while something made from it stands. A delete of any `lineage` node kind
(array/table/sparse dataset, mesh/network/annotation collection, fixture, file, annotation)
walks what is downstream -- the same `descendants` walk the lineage viewer uses
(`core/logic/deletion.py`) -- and is refused, naming it, unless its input says `cascade: true`;
then every casualty is held to its own owner and delete guards before anything goes, and all of
it is deleted in one transaction. `preflightDelete<Model>(id)` (one query per kind, one
`DeletePreflight` type) answers the same without deleting: casualties, blockers (not yours, a
guard, a PROTECT/RESTRICT reference from Django's `Collector`) and what else cascades (layers,
lenses). A closure deeper than 10 hops or over 1000 rows is refused even with `cascade`. An
annotation collection's own shapes go with it by CASCADE; what was made from them elsewhere is a
casualty. A sliced lens something was derived from is refused (`assert_lens_deletable`).
`AnnotationDerivation.source` is RESTRICT as the backstop. Deleting a derivation edge
(`deleteTransformation`) or a file link edits lineage and deletes no data: those are not guarded.
`deleteDerived(kind, id)` is the same closure with the object kept: every casualty goes (each
held to its own owner and guards), the object itself is neither deleted nor checked;
`preflightDeleteDerived(kind, id)` answers it (`preflight(..., keep_target=True)`).

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
  every lens the spec its selection denotes (`Lens.stored_spec`) and the name its pinned
  positions and its cut give it (`Lens.name`, announcing nothing); the
  database refuses duplicates since the lens tables were split, so it is a guard for rows
  written by hand. It changes nothing the second time and reports what it leaves in place.
- Other jobs: `purge_orphaned_stores` (from `arkitekt_datalayer`), `respec_datasets`, `backfill_parquet_schemas`. The backfill is safe
  beside a serving release and reports what is still owed; it is not in the setup, so an
  operator runs it. `prune_organizations` (from arkitekt-service, `-- --only <org> --yes` or
  `--keep`) deletes everything organizations own -- datasets, lenses, shapes, metrics, stores
  and their bytes at once -- and keeps the organizations; a metric it deletes gets its DELETED
  feed entry from the `pruning` signal (`metric.logic.record_pruned`), so kraph hears of it.

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

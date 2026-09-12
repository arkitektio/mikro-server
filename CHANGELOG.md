<!-- Frozen. Releases are tag-only since the move to tag-only semantic-release,
so this file is no longer generated; entries below stop at the last release that
predates the switch. Current release notes live on the GitHub Releases page. -->

# CHANGELOG


## v2.0.0-rc.35 (2026-09-02)

### Features

- Update attribute plans
  ([`6a03073`](https://github.com/arkitektio/mikro-server-next/commit/6a0307360d9b1f34e5f7745de5f898413b8c9ec7))


## v2.0.0-rc.34 (2026-09-01)

### Features

- Vector and other layers
  ([`3bc8a61`](https://github.com/arkitektio/mikro-server-next/commit/3bc8a61bddafe8299a3fae7dfdae6da235553dae))


## v2.0.0-rc.33 (2026-08-28)

### Bug Fixes

- Surface annotations
  ([`27aadcf`](https://github.com/arkitektio/mikro-server-next/commit/27aadcf7dac39b2e1b5db61bd148e6dc624da76d))

- With by_transofrmation childen
  ([`2b962e1`](https://github.com/arkitektio/mikro-server-next/commit/2b962e1773e364c8abf14959bd6ec6471d3865c5))

### Features

- Intensity laayer
  ([`7a24866`](https://github.com/arkitektio/mikro-server-next/commit/7a248667e4ee7a623e6b12407f1176cac75a3be4))


## v2.0.0-rc.32 (2026-08-24)


## v2.0.0-rc.31 (2026-08-21)

### Features

- Place per-index registrations end to end, and derive two duplicated facts
  ([`ced4055`](https://github.com/arkitektio/mikro-server-next/commit/ced405519e83ecf23ae39e1688f561552efe4841))

Two duplicated facts, and one defect seen from two sides.

**`Transformation.version` is gone; the number is counted from provenance.** The column recorded
  what `ProvenanceField` already records: every save writes a history row, and the counter had to be
  remembered separately by every writer. Only `updateTransformation` remembered, so any other write
  left a chain that had moved reading as though it had not. `Transformation.version` stays a GraphQL
  field -- the `(id, version)` cache key in `docs/attribute-plans-api.md` is unchanged, and a fresh
  edge still reads 1 -- and `transform_version` sums the rows along a chain in one query. A rename
  now moves it too, which errs towards recomputing a bounding box that did not need it rather than
  trusting one that did.

**No axis type ordering is required any more.** `assert_axis_type_order` held array-backed systems
  to RFC-5's time-then-channel-then-space MUST. Nothing reads it: `resolve_render_axes` finds the
  time, channel and phasor axes by type, and only the relative order of the SPACE axes matters.
  `create_table_axes` had already reasoned this out and skipped the check, recording that `x, y, t`
  was refused while `t, x, y` was accepted though both derive the same answer. For arrays it refused
  `(z, c, y, x)` and `(c, z, y, x)` -- how acquisitions are ordinarily written. What still holds is
  that an axis' `order` is the store's dimension order, which `assert_axes_describe_the_store`
  checks.

**UNMAPPABLE is claimed only when the data reaches nowhere.** `_blocked_by_unmappable` returned true
  if *any* lineage edge was unmappable, so a fusion with one unmappable parent was badged impossible
  though registering its other parent places it -- and whoever read the badge was told not to look
  for the gap they could have closed. It now requires that no traversable edge leave the source at
  all, and `SceneGraph.placement_state` asks the same question through the same traversal, so
  creation-time refusal and query-time state cannot drift apart.

**The selector is plumbed through the readers that were blind to it.** `adjacency_of` was the only
  reader of `selector_admits`; every other consumer walked with no `at`, which dropped scoped edges
  from the graph entirely. So a dataset registered per channel was invisible to `is_placeable_in` --
  with the scoped hop mid-chain, `createLayer` refused the very layer the feature exists to allow --
  reported UNREGISTERED / UNKNOWN / NONE from `placement`, `placementValidity` and
  `placementInvariance`, and never appeared in `inView`.

Existence and position are different questions: whether data has a place does not depend on where
  the asker stands, only which place does. `adjacency_of` takes `admit_scoped` for the existence
  question, `PlacementState.CONDITIONAL` and `ExtentState.CONDITIONAL` say "placed, but ask again
  with `at`", and `placement`, `placementValidity`, `placementInvariance` and `inView` all take the
  `at` that `pathToWorld` and `asAffine` already did. Nothing composes a map from a scoped edge
  without fixing its coordinate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

Claude-Session: https://claude.ai/code/session_017d1bWpdSg8CQtCtCDqV4Gk


## v2.0.0-rc.30 (2026-08-21)

### Bug Fixes

- Add
  ([`5939e8e`](https://github.com/arkitektio/mikro-server-next/commit/5939e8e406a1623ab6dcaa6675920dc0b90fb187))


## v2.0.0-rc.29 (2026-08-20)

### Bug Fixes

- Coordinate transform update
  ([`7e872ad`](https://github.com/arkitektio/mikro-server-next/commit/7e872ad84a39f20bbc93e9ff7573da37e1da481b))

- Coordinate trenasofmr update
  ([`2fcca48`](https://github.com/arkitektio/mikro-server-next/commit/2fcca48cf634472206cd16f631d5b87f7843bcb0))


## v2.0.0-rc.28 (2026-08-19)

### Features

- New sporadik sparse datasets
  ([`039df1a`](https://github.com/arkitektio/mikro-server-next/commit/039df1a13851175a9ee77f4f2c7ed7ec724ea5a8))


## v2.0.0-rc.27 (2026-08-19)

### Bug Fixes

- Toaffine
  ([`a890846`](https://github.com/arkitektio/mikro-server-next/commit/a890846d6d95c9062a965d70ed57aec661e9d27f))

### Features

- With sparse array concept
  ([`4a9b6ad`](https://github.com/arkitektio/mikro-server-next/commit/4a9b6ad0ff49a495388556f26f662f8452b7a181))


## v2.0.0-rc.26 (2026-08-18)


## v2.0.0-rc.25 (2026-08-18)

### Bug Fixes

- Guards for the tableset and mesh collections
  ([`d7febe2`](https://github.com/arkitektio/mikro-server-next/commit/d7febe2bdd24629e58f4e468b8a76cd56dae2a7c))

### Features

- New color by and filter by
  ([`4207443`](https://github.com/arkitektio/mikro-server-next/commit/420744396658ad1c22a077fc8c6a79bf5041c78e))


## v2.0.0-rc.24 (2026-08-18)

### Features

- Add mesh layer color options
  ([`6cf3bdc`](https://github.com/arkitektio/mikro-server-next/commit/6cf3bdc2aa2327c1b30c6feb2747c7c217ecb28f))


## v2.0.0-rc.23 (2026-08-17)


## v2.0.0-rc.22 (2026-08-17)

### Bug Fixes

- Datalayer fixes (unsigend requests)
  ([`ec34ce1`](https://github.com/arkitektio/mikro-server-next/commit/ec34ce19f88edfcb11ede7e458a359fd34acaeda))


## v2.0.0-rc.21 (2026-08-16)

### Bug Fixes

- Attribute plans
  ([`02768e2`](https://github.com/arkitektio/mikro-server-next/commit/02768e2878d9fff3703d07e8f511dd8b964c9b2c))

### Features

- Removal of old schema
  ([`2e478e6`](https://github.com/arkitektio/mikro-server-next/commit/2e478e6bc5890d74f40fa12403c44a98f519dd24))

- Remove of old types
  ([`835dd10`](https://github.com/arkitektio/mikro-server-next/commit/835dd107fe79e290c0bf0876ed86b331e4207539))


## v2.0.0-rc.20 (2026-08-15)

### Bug Fixes

- Fabirks support
  ([`85ac9cd`](https://github.com/arkitektio/mikro-server-next/commit/85ac9cd878a4a9c77704be5426d77e0f111ebc70))


## v2.0.0-rc.19 (2026-08-11)


## v2.0.0-rc.18 (2026-08-11)

### Bug Fixes

- Add label layers
  ([`eac8bf7`](https://github.com/arkitektio/mikro-server-next/commit/eac8bf7e6f15b6e690a2b970e796ad3f112a33b3))


## v2.0.0-rc.17 (2026-08-11)

### Bug Fixes

- With folder
  ([`9b8cf49`](https://github.com/arkitektio/mikro-server-next/commit/9b8cf49c09b81048fa16e2655c46fb09913d8afe))


## v2.0.0-rc.16 (2026-08-11)

### Bug Fixes

- Add dataset into folder
  ([`d6836bf`](https://github.com/arkitektio/mikro-server-next/commit/d6836bfb4d042613eb3e8ee535666a3ef9c354c2))


## v2.0.0-rc.15 (2026-08-11)

### Bug Fixes

- Rename to folder
  ([`77b1a3e`](https://github.com/arkitektio/mikro-server-next/commit/77b1a3e7838f63020d6625d560b7154f7f1c8960))

- Rename to folder
  ([`bed76c4`](https://github.com/arkitektio/mikro-server-next/commit/bed76c4b1dd3d1c5873aef44b5e090b135f65382))


## v2.0.0-rc.14 (2026-08-04)

### Bug Fixes

- More stuff on tables
  ([`140a212`](https://github.com/arkitektio/mikro-server-next/commit/140a2129941936a754ac8ca0b8718aa859c3736c))


## v2.0.0-rc.13 (2026-08-04)

### Bug Fixes

- With proper filters
  ([`79d2a9f`](https://github.com/arkitektio/mikro-server-next/commit/79d2a9fbfede63ce114f1fce5ade06fa2be27532))


## v2.0.0-rc.12 (2026-08-04)

### Bug Fixes

- Add exportOf and sourceFile to lik datasets with files
  ([`4e310fa`](https://github.com/arkitektio/mikro-server-next/commit/4e310fa33e5b07353b25097108258677bc7f9c81))

- Orphaned dataset + cron remover
  ([`6289451`](https://github.com/arkitektio/mikro-server-next/commit/6289451c1784a6b029474794651dccdfc04566b2))


## v2.0.0-rc.11 (2026-08-02)


## v2.0.0-rc.10 (2026-08-02)

### Documentation

- More at the coordinate system (fields vs references)
  ([`5566af4`](https://github.com/arkitektio/mikro-server-next/commit/5566af40eb5061dab065f57e994422b0c4e216fc))

- The two derivation call sequences, and a FIELD is not a derivation
  ([`693ba51`](https://github.com/arkitektio/mikro-server-next/commit/693ba51b4fb600f510b79d887e0bb81ff1e56c9a))

`docs/derivation-api.md` walks both directions end to end: an SMLM localization table and the
  reconstruction rendered from it (table -> image, a real SCALE, so registering the table places the
  render), and a stack, its segmentation and its measurement table (image -> mask -> table,
  BY_DIMENSION then UNMAPPABLE, so the lineage is recorded and no geometry claimed). Both are
  executed by `test_the_documented_sequences_run_end_to_end`, the same guarantee
  `field-transforms-api.md` already has: a doc that names a field the schema does not have is worse
  than no doc, because it reads as verified.

Writing it caught a regression from the container-keying change. A FIELD edge points mask -> table,
  so under container keys `derivedFrom` on a mask with a dereference reported the *table* as the
  thing the mask was derived from. It was hidden before by accident rather than by rule: the
  dataset-keyed predicate resolved the output through the table's own derivation edge back to the
  mask, compared equal, and dropped it. `is_derivation_edge` now excludes FIELD outright, which is
  the line RFC-7 already draws for the attribute-plan walk -- FIELD edges are payload, never
  connectivity. A FIELD is a lookup; the table's provenance is its own separate edge.

`field-transforms-api.md` was stale in two ways and is corrected: its `derivedFrom` entry predated
  the transform union (the map is nested under `transform` now, and the entry's own `kind` is the
  *source* discriminator), and it selected `coordinateSystem { kind }`, which RFC-9 retired in
  favour of `residents`.

594 green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### Features

- Lineagegraph walks the derivation edges out from one container
  ([`0430205`](https://github.com/arkitektio/mikro-server-next/commit/0430205db280185fe8391515c93911309481c06b))

`derivedFrom` and `derivedResidents` each answer one hop. Nothing answered "where did this come
  from, and what came out of it" transitively, and `coordinateGraph` cannot stand in: it crosses
  every edge touching a space, so a registration drags in every other dataset registered into the
  same world. That is a neighbourhood, not a provenance.

`lineageGraph(coordinateSystem:, maxDepth:)` crosses derivation edges only (`is_derivation_edge`,
  the same predicate `derivedFrom` uses), in both directions -- asking a source what came out of it
  and asking a product what went into it are the same graph read from two ends. Nodes come back as
  *containers* rather than spaces, because a dataset's grid, its levels and its lenses are one node
  in a provenance story rather than three.

Kind-blind, like `derivation_edges` and unlike `lineage_ancestors`: that one walks the spatial
  lineage and stops at an UNMAPPABLE primary, since data whose geometry did not survive inherits no
  placement. This is the historical lineage, where the UNMAPPABLE hop is the point -- it is how a
  measurement table hangs off the mask it was measured from. Each edge carries its kind, so a client
  wanting only the placing chain filters on it.

Fixes a bug in the previous commit, which nothing would have caught: a container key's first half
  was written by `container_map` as "dataset" and read back through `ADataset.__name__.lower()` as
  "adataset", so every dataset silently vanished from `derivedResidents` and from any reverse
  lookup. The key now lives on `CONTAINERS` beside the model, with `MODEL_BY_KEY` for the reverse,
  and `test_the_wider_field_reports_dataset_children_too` pins it -- a test with only a table child
  passes either way, which is why the original slipped through.

593 green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


## v2.0.0-rc.9 (2026-08-02)

### Bug Fixes

- Lightpath
  ([`4749622`](https://github.com/arkitektio/mikro-server-next/commit/47496226b7d0f826dabd7919351ac8b390b6def5))

### Features

- A derivation runs between containers, whichever kind they are
  ([`bce250d`](https://github.com/arkitektio/mikro-server-next/commit/bce250d0cc50a60d7f76bc8a788dbde558d35cb4))

"This data was computed from that data" was only ever expressible between two array datasets.
  `createADataset(derivedFrom:)` named a `Lens` and nothing else; `createTableDataset`,
  `createMeshCollection` and `createAnnotationCollection` named a bare `coordinateSystem`, so a
  caller had to look the source's *system* id up by hand and no collection could be a source at all.
  A parameter table could not say which instance map its rows came from, and an image reconstructed
  from a table of SMLM localizations could not say so in either direction.

One `DerivedFromInput` union now, keyed by source kind -- the third `@unionElementOf` instance,
  after TransformInput and OpticalElementInput. Six members (LENS, DATASET, TABLE_DATASET,
  MESH_COLLECTION, ANNOTATION_COLLECTION, COORDINATE_SYSTEM), each declaring the parent's common
  fields, resolved through the `resolve_source_system` registrations already use. All four creators
  take a priority-ordered list of them and share one writer, `write_derivation_edges`.

An omitted `transform` now means **UNMAPPABLE**, not IDENTITY. Naming a source records the lineage
  and claims no geometry -- the truth for a measurement table whose rows are not anywhere, and the
  principle `createTableDataset` already stated while the other three broke it. Placement is
  inherited only across a transform the caller stated.

Breaking, three distinct ways: - `coordinateSystem` on the three collection creators is replaced by
  `derivedFrom`, and their `derivedFrom` read fields are lists. - the omitted-transform default
  flips, so derived data stops inheriting placement until its caller says how the spaces relate. - a
  multi-parent call whose first entry omits `transform` while a later one states it now *raises*,
  because an UNMAPPABLE primary may not hide a mappable parent. - `axes` is required on the two
  collections. It used to default to a copy of the source's, justified by "an identity into a system
  with different axes is not an identity, and the rank check would say so" -- which dies with the
  IDENTITY default, since `assert_edge_rank` returns early for an UNMAPPABLE.

`ADataset.derivedDatasets` stays honestly narrow; `derivedResidents` is the wider question, because
  a field called derivedDatasets returning a table would be a field whose name lies.

Also fixed: `createTableDataset` was not atomic, so an edge whose rank its axes refused left an
  orphan table behind -- the same bug the two collections had.

No migration: a derivation was already just a Transformation edge. 590 green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

### Refactoring

- The fact tree is keyed by container, not by dataset
  ([`8356bbf`](https://github.com/arkitektio/mikro-server-next/commit/8356bbf77870ce2c89c2a9297798b5401698687c))

`residence_map` knew only about datasets, so a mesh, table or annotation collection was simply
  absent from every placement structure and each caller patched the hole its own way. That made a
  non-ADataset unnameable as a derivation parent -- not refused, but silently dropped:
  `derivation_edges` resolved an edge's output to an `ADataset` and discarded the edge when it could
  not, so a dataset derived from a table would have read back with no parent at all.

`container_map` replaces it. A collection keys to itself, a dataset's grid, lenses and levels all
  key to the dataset, and a resident-less space keys to itself -- which is what tells a registration
  from a lineage. One predicate, `is_derivation_edge`, now answers "is this a derivation" for
  `derivation_edges`, `collection_derivation_edge` and `edge_universe`, so the three cannot drift
  apart about it. Both halves are load-bearing: the output must land in a container *and* in a
  different one.

Two bugs fall out, both pre-existing:

- `collection_derivation_edge` took the earliest edge out of a collection's system, kind-blind and
  order-blind. A freestanding collection later registered with `createTransformation` reported that
  *registration* as its `derivedFrom`. - `EdgeUniverse` had to fetch a collection's derivation edge
  separately and re-file it under the dataset it landed in, because the collection had no key of its
  own. A collection is an ordinary bucket now and all of that is gone, together with the
  `collection_systems` parameter that existed to feed it.

The six-container list was hand-written in six places with six shapes; it is `CONTAINERS` once. Two
  orders are kept and both are load-bearing: presentation (outermost first, which `residents`
  returns) and keying (dataset last, so a space its dataset lives in resolves to the dataset).

No API change and no migration: 583 tests green before and after.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


## v2.0.0-rc.8 (2026-08-01)


## v2.0.0-rc.7 (2026-07-10)

### Bug Fixes

- Assert dataset
  ([`920ad3e`](https://github.com/arkitektio/mikro-server-next/commit/920ad3e0706504f2caa0eb2150dbe87b2d13735e))

- Attribute plans + scene bootstrap read residents; 408/494
  ([`b0fb25f`](https://github.com/arkitektio/mikro-server-next/commit/b0fb25fd7c1557dbd8c148fd8f187e2ed5f612d6))

- Attribute plans read table residents; 346/494
  ([`67ac43d`](https://github.com/arkitektio/mikro-server-next/commit/67ac43d7fca850760769898800d91c4bcd34f792))

- Drop the last owner-FK select_relateds; 261/494
  ([`dfec21a`](https://github.com/arkitektio/mikro-server-next/commit/dfec21abb089bddf2baced93843d3854231e5a5f))

- Elipsis and spheres
  ([`0ba447f`](https://github.com/arkitektio/mikro-server-next/commit/0ba447f81ceea56bb72d708e2355cd22a521c713))

- Input uniotons
  ([`96201f4`](https://github.com/arkitektio/mikro-server-next/commit/96201f4ad75a9c3c7301b8534e52080ae0c5388b))

- Less coord systems
  ([`16fe92f`](https://github.com/arkitektio/mikro-server-next/commit/16fe92f7d6ff19c4e6966fe7cb19beb984fa6607))

- Less coordinate system
  ([`1b06052`](https://github.com/arkitektio/mikro-server-next/commit/1b06052ef5e1fd7cdb2f8f2079d73f962e965ded))

- Make corodinate system less "mirrored"
  ([`3e11727`](https://github.com/arkitektio/mikro-server-next/commit/3e11727fee4b12fd0e2f1c6f4aea0b614c2fc7a5))

- More omengfness
  ([`f5c2251`](https://github.com/arkitektio/mikro-server-next/commit/f5c225180dde9ad2095ef88fc75629cbc76ac29b))

- More omnegffness
  ([`ad1acdc`](https://github.com/arkitektio/mikro-server-next/commit/ad1acdc1ff40a7da61be04cea13b3ae8e6b5cbb3))

- More scene
  ([`a8e1c8d`](https://github.com/arkitektio/mikro-server-next/commit/a8e1c8dd3bb7b35a4a58b9389e04bd3197dc57a9))

- More stuff
  ([`696f366`](https://github.com/arkitektio/mikro-server-next/commit/696f366dce8e74aa4aaacbb3886c7976d51af081))

- New graphs
  ([`ca95d12`](https://github.com/arkitektio/mikro-server-next/commit/ca95d1212853d456f0acf46dbcf50732e6b67260))

- Parquetlike
  ([`94ede38`](https://github.com/arkitektio/mikro-server-next/commit/94ede38d1b3a525e0bc923cadfedbd930d39f76f))

- Placement validit<
  ([`d345a97`](https://github.com/arkitektio/mikro-server-next/commit/d345a97c3c6f26c55afce03bd9d1e9704ec2f4a0))

- Purge old data
  ([`768c7fd`](https://github.com/arkitektio/mikro-server-next/commit/768c7fd3b09fbba129c6bddc3d4112714652d357))

- Relaxed constraints and made coordinate system first class
  ([`db87ddd`](https://github.com/arkitektio/mikro-server-next/commit/db87ddd37aaa5edd319b59f9117e84c1c4e9b7d3))

- Renaming issues
  ([`0fe1c32`](https://github.com/arkitektio/mikro-server-next/commit/0fe1c32ade956a4ed7e5766eb23fcf3514d42a1c))

- Residence map tolerates being read during construction; 341/494
  ([`770a876`](https://github.com/arkitektio/mikro-server-next/commit/770a876e86b2913bda66828a879c04a5eedacea5))

- Roi optimiztazion
  ([`30fbcba`](https://github.com/arkitektio/mikro-server-next/commit/30fbcba927d1a7493b3dfe2386549d9effaece01))

- Scene bootstrap reads residents; every space is adoptable
  ([`1eccdb5`](https://github.com/arkitektio/mikro-server-next/commit/1eccdb5d660a3a79e71abd37ed11383980829950))

- Scene now walks coordinate system
  ([`0040ab8`](https://github.com/arkitektio/mikro-server-next/commit/0040ab89744048bda3ecb01bd1856f18b25d779d))

- Seed the residence map before it is first read; 329/494
  ([`65a57fb`](https://github.com/arkitektio/mikro-server-next/commit/65a57fbbf99f32060a82dc60bb80a52d7af3c8bf))

- Shared-space guard reads residents; 347/494
  ([`9c70a2a`](https://github.com/arkitektio/mikro-server-next/commit/9c70a2a7377dd0900415bbf21b657b599abec60e))

- Strip owner FKs from every select_related; 318/494
  ([`9b1576e`](https://github.com/arkitektio/mikro-server-next/commit/9b1576ebcbdd0bbde09d81857fb06bfe8f54fcaf))

- The bootstrap follows one edge back to find a calibrated space's data
  ([`35b88f9`](https://github.com/arkitektio/mikro-server-next/commit/35b88f9289c726cb9af543272fb5b4b612810ca6))

A calibrated space has no residents, so `system_dataset` cannot answer 'which dataset is this a view
  of' -- under ownership that space carried a dataset FK and it was a column read. `dataset_behind`
  is the inverse of `calibrated_neighbours`: residents first, one hop upstream only for a frame
  nothing lives in. Caught by test_a_calibrated_dataset_registers_through_its_physical_system.

- With animation
  ([`5861ab4`](https://github.com/arkitektio/mikro-server-next/commit/5861ab4a3410367610659f178e3e5445579a3b0a))

- With more attribute plans
  ([`c1e9194`](https://github.com/arkitektio/mikro-server-next/commit/c1e91946b983aa20625ac9b25830d901e287b0bc))

- With stuff
  ([`44cf7b6`](https://github.com/arkitektio/mikro-server-next/commit/44cf7b676a5372b8472c33e02dc5fe86ff14a16e))

- With table datset
  ([`cb3b067`](https://github.com/arkitektio/mikro-server-next/commit/cb3b067c3f8c9d072d98d3f8b1373071f9b1e9d8))

### Documentation

- Rfc-9 records what the suite rewrite pinned
  ([`e407677`](https://github.com/arkitektio/mikro-server-next/commit/e407677b03662971e7dd68c7626920d4a7c0e358))

- Rfc-9, residence
  ([`86e365b`](https://github.com/arkitektio/mikro-server-next/commit/86e365b544f97337da3542ac69fd3152e4bf4c1c))

Records what ownership was carrying, why none of it survived, and the directional insight that makes
  the flip cheap rather than dear: residence asks data -> space, which is a local column, where
  ownership asked space -> data. Also records what is designed but not built.

### Features

- In between
  ([`3f37a77`](https://github.com/arkitektio/mikro-server-next/commit/3f37a77974a0afa218572e583978ac81e45b8bc6))

- New stuff
  ([`3dd16eb`](https://github.com/arkitektio/mikro-server-next/commit/3dd16eb71dc39bbaf80534efc876413a99f88862))

- Reject unplaced layers instead of auto-registering; bootstrap always authors the mirror edge
  ([`08b68ec`](https://github.com/arkitektio/mikro-server-next/commit/08b68ecf39e62256661efd7f1c7f3ccc44e998e2))

A scene is membership + render graph only: the transform between two coordinate systems is authored
  exactly once, explicitly, via createTransformation / addRegistrationToScene -- never fabricated as
  a side effect of a layer mutation.

BREAKING CHANGE: createTableDataset no longer accepts `scene`; all create*Layer mutations reject a
  source with no traversable path to the scene's world, distinguishing UNMAPPABLE (nothing can ever
  place this) from UNREGISTERED (author the edge first); updateLayer applies the same gate when
  rebinding scene/lens; createMeshLayer now takes meshCollection instead of the legacy mesh;
  legacy-table point/track layers require coordinateSystem; ensure_registered and its assumed-edge
  fabrication are removed. bootstrapScene always authors exactly one identity registration for the
  staged dataset -- from its calibration (VALIDATED, "(mirror)") or its intrinsic pixels (UNKNOWN,
  "(assumed)") -- including derived and UNMAPPABLE-derived datasets, whose dedicated world mirrors
  their own axes. Adds a stored `description` to Axis and TableColumn, threaded through the
  axis/column inputs and GraphQL types (migration 0023).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

### Performance Improvements

- Batch the last two per-row residence reads
  ([`70b6e30`](https://github.com/arkitektio/mikro-server-next/commit/70b6e30364dff5c69ab4f0a79cc93b43d7c7aeac))

scenes_by_sole_dataset asked per registration and again per scene world. Both were column reads
  under ownership and became queries under residence; the sole-occupancy map now resolves both in
  the same batch. All three query-count suites are green again.

- Batch the two per-source queries residence introduced
  ([`494200a`](https://github.com/arkitektio/mikro-server-next/commit/494200aa720a2a28515c497956a793f55912456e))

placeable_system_ids_in called system_dataset per registration, and _fetch_collection_edges asked
  each layer's space what lived in it. Both are now one batched read -- a residence_map over the
  registration inputs, and the layer's own collection FK. The query-count tests were the only thing
  that would have caught either.

- Prefetch residents in the scene's reachable systems; suite green at 491
  ([`8602b5d`](https://github.com/arkitektio/mikro-server-next/commit/8602b5dadc4f3b1b5ea266dc445a9e2d88a0d8d9))

Scene.coordinateSystems returns a plain list too, so selecting residents paid six reverse queries
  per space -- and a scene reaches more spaces as it gains layers, which is the growth the flatness
  test forbids.

### Refactoring

- Creation paths write residence; 260/494 green
  ([`026fb40`](https://github.com/arkitektio/mikro-server-next/commit/026fb400c9d4dc3519353b7255a6db1124b7d1d7))

- Graph.py speaks residence, not ownership
  ([`d4645ef`](https://github.com/arkitektio/mikro-server-next/commit/d4645ef41d8886fc3b107ae61fe052c7d2c013db))

Deletes the fact/claim machinery outright -- is_registration_target, fact_edges, claim_root,
  _assert_one_claim_per_space -- since every one of its consumers goes with it, and deletes
  create_calibration: a calibrated space is now an ordinary space with an edge into it.

Ownership readers are inverted rather than ported. residence_map() reads coordinate_system_id off
  the data rows (three batched IN queries, flat in both spaces and residents) instead of traversing
  back from a space, which is the direction the residence model makes cheap.

_SHARED_SIDE_MIRROR becomes _UNINHABITED: 'a space nothing lives in', which is a better rule than
  the one it replaces because it names what a world is rather than how its edges were made.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Residence reaches the graph modules, types and filters
  ([`9aeca92`](https://github.com/arkitektio/mikro-server-next/commit/9aeca92dff15dc5d62f8a3b2aee503a36acc832f))

scene_graph/space_graph thread a residence map instead of reading owner columns; space_graph now
  fetches the *residents* of the candidate spaces rather than the spaces and their seven FKs, which
  is the direction the model makes cheap.

kind, isAdoptableWorld, CoordinateSystemOwner, ADataset.calibrations and the calibration mutations
  are gone. CoordinateSystem.residents replaces kind: what a space is follows from what lives in it,
  and 'nothing lives here' is the only distinction the four-value label really carried.

calibrated_neighbours() replaces dataset.calibrations for the scene bootstrap, the phasor bin width
  and the calibrated filter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v2.0.0-rc.6 (2026-07-10)

### Bug Fixes

- Unique together
  ([`4d99afb`](https://github.com/arkitektio/mikro-server-next/commit/4d99afb5c3c47b5ddcd7839817c5f9739717877a))

- Wrongly assuemd UUID for lightports
  ([`6409a45`](https://github.com/arkitektio/mikro-server-next/commit/6409a451913546d781c72e831cdcf57fb649bd92))


## v2.0.0-rc.5 (2026-07-10)

### Bug Fixes

- Massive updates to the knne scalars and input types
  ([`266a194`](https://github.com/arkitektio/mikro-server-next/commit/266a1942930c4beeb9696b9a50cdc72244fd57b3))


## v2.0.0-rc.4 (2026-07-07)

### Bug Fixes

- More mutations
  ([`6b175a5`](https://github.com/arkitektio/mikro-server-next/commit/6b175a5dad7c289217051189dc9f29592441776d))


## v2.0.0-rc.3 (2026-07-07)

### Bug Fixes

- New layers plus kanne_scalars
  ([`9eeed5b`](https://github.com/arkitektio/mikro-server-next/commit/9eeed5b0f3a6502fb3998bba3998f476064425ac))

- Tests
  ([`980e962`](https://github.com/arkitektio/mikro-server-next/commit/980e962781c1c6dede4a55623f56d616d0883ae0))


## v2.0.0-rc.2 (2026-06-30)

### Bug Fixes

- Removal of scope filtering
  ([`8d4e5e0`](https://github.com/arkitektio/mikro-server-next/commit/8d4e5e0096a89f726fec5a2848db170e718aa9e6))


## v2.0.0-rc.1 (2026-06-29)


## v1.0.0 (2026-06-25)


## v1.0.0-rc.5 (2026-06-29)

### Bug Fixes

- Authentikate update
  ([`efaeee9`](https://github.com/arkitektio/mikro-server-next/commit/efaeee92055ea138310a4405a721b42aaced996e))


## v1.0.0-rc.4 (2026-06-26)

### Features

- Removal of stale migrations
  ([`17a8dc3`](https://github.com/arkitektio/mikro-server-next/commit/17a8dc3ce2bc8384f02de13a7830cb92048b175d))


## v1.0.0-rc.3 (2026-06-26)

### Features

- With white noise and optimized Dockerfile
  ([`736c2e6`](https://github.com/arkitektio/mikro-server-next/commit/736c2e6136a10430c61d9efce21d62b26436e950))


## v1.0.0-rc.2 (2026-06-26)

### Bug Fixes

- With CONFIG.md
  ([`a29e94d`](https://github.com/arkitektio/mikro-server-next/commit/a29e94d136898a75b0f0b3938fbd91226b3c5c9b))


## v1.0.0-rc.1 (2026-06-25)

### Bug Fixes

- Add datalayer attributes
  ([`1b83741`](https://github.com/arkitektio/mikro-server-next/commit/1b837417cb61947cb464beea1678f30a18a361c1))

- Add filter type
  ([`2b2503d`](https://github.com/arkitektio/mikro-server-next/commit/2b2503d4968f64b97fbb54766a20742833d13558))

- Add lightpath_view
  ([`b5182d5`](https://github.com/arkitektio/mikro-server-next/commit/b5182d5639247b9247ab2c2efae95bd4cad9eb27))

- Add more
  ([`e9d91cd`](https://github.com/arkitektio/mikro-server-next/commit/e9d91cd87a9305df66e36f80974a7d23bfb7ff99))

- Add organization
  ([`f2f9ec1`](https://github.com/arkitektio/mikro-server-next/commit/f2f9ec16446cf336c6d7536601c4b3a81ca8bddd))

- Add upload grant
  ([`d1faee5`](https://github.com/arkitektio/mikro-server-next/commit/d1faee58165449de5e34a8585cf896e9210a577b))

- Base_color issue
  ([`652bcf3`](https://github.com/arkitektio/mikro-server-next/commit/652bcf3d59f4abf047b5e7e2e157bf2fd85f4e5c))

- Clean all pyflakes-level issues and gate them in CI
  ([`d3a9a59`](https://github.com/arkitektio/mikro-server-next/commit/d3a9a59971d330d560785860412556ac890ad19b))

- delete core/mutations/anchor.py: dead near-duplicate of view.py that was never imported and
  referenced an undefined ViewInput - fix real bugs surfaced by the sweep: missing datetime import
  in queries/rows.py, info not threaded into _create_instance_mask_view_from_partial, duplicated
  ValueHistogramInput/Model defs, dead first Slice class and shadowed Image.views field in types.py
  (SDL verified unchanged) - drop unused assignments while keeping side-effecting calls; remove the
  stale __all__ in core/mutations/__init__.py (listed mutations that no longer exist) - ruff: move
  config to [tool.ruff.lint], ignore F401/F403/F405 in package __init__ re-export files; autofix
  unused imports repo-wide - CI: new lint job gating ruff F,E9 (ANN/D1 stay local-only for now; mypy
  1.15 crashes on django-stubs and is not gated yet)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Config hardening and repo hygiene
  ([`4828cf7`](https://github.com/arkitektio/mikro-server-next/commit/4828cf7648352582fd8849089523995c6071c4df))

- settings.py: DEBUG and ALLOWED_HOSTS now come from config.yaml (django.debug already existed there
  but was ignored); refuse to start with the 'changeme' secret key when debug is false - delete
  tmanage.py (only difference from manage.py was the settings module; pytest sets it via pyproject)
  - replace remaining print() debugging with module loggers (types, adataset, datalayer) and drop
  the bare print(id) lines in schema.py - drop deprecated aioredis dependency (nothing imports it;
  channels-redis ships its own client) - move demo_*.py scripts to examples/; rename
  untsructured_meta.py -> unstructured_meta.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Contexts
  ([`2c8b7e3`](https://github.com/arkitektio/mikro-server-next/commit/2c8b7e36bd7dc6f1bfc5ec9d0573cab9030b5433))

- Correct import statements for strawberry_django
  ([`6c0f7b4`](https://github.com/arkitektio/mikro-server-next/commit/6c0f7b4c0a3903bb6713c6ddfb438e5e27ebcb51))

- Correct key name for chunk shape in ZarrStore model
  ([`76b0f4c`](https://github.com/arkitektio/mikro-server-next/commit/76b0f4caa0ddd2f51f57de3253c7ba7f6c2f6379))

- Datalayer
  ([`b756437`](https://github.com/arkitektio/mikro-server-next/commit/b7564379aec5dfc5c6f6f2d8915f0e985d5b0b7c))

- Default dataset fixes
  ([`e6f160d`](https://github.com/arkitektio/mikro-server-next/commit/e6f160d4e8a92cfea62f4570fbe79b69f333afcf))

- Docker next building
  ([`20088a6`](https://github.com/arkitektio/mikro-server-next/commit/20088a6baec6ce4ed65a94f5f15d46c68abdb8c8))

- Enforce organization scoping on mutations, single-object queries and subscriptions
  ([`13c1772`](https://github.com/arkitektio/mikro-server-next/commit/13c1772f20ff316446164035f3129083d326d435))

- add core/scoping.py (for_org/get_for_org/aget_for_org) resolving each model's path to its
  organization; unscopable models are an explicit list - route all by-id fetches in mutations,
  queries, schema.py and subscriptions through the scoped helpers - stamp organization on
  camera/objective/mesh creates (non-null FK was missing -> IntegrityError) and scope
  ensure_*/update_or_create lookups - subscriptions: verify the parent object is visible before
  joining rooms, org-prefix the global rooms, centralize room names in core.channels, fix wrong
  rooms/channels and the dict-vs-signal message handling - settings_test: second static token in
  another org; add cross-org regression tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Filters
  ([`deb0f89`](https://github.com/arkitektio/mikro-server-next/commit/deb0f89ead81979bc8222335191042e5ea55a044))

- Initial tests
  ([`b860e49`](https://github.com/arkitektio/mikro-server-next/commit/b860e4983f2f8704e4c537a217c1472d3ebecf57))

- Keep model = input.to_pydantic() validation lines; make F841 advisory in CI
  ([`421b822`](https://github.com/arkitektio/mikro-server-next/commit/421b822ecd4881549d549846840abc212bcaf672))

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Long types
  ([`4d97832`](https://github.com/arkitektio/mikro-server-next/commit/4d978321e2d05fdb94f00e69301ec78667fa8758))

- Mikro_server
  ([`17a9818`](https://github.com/arkitektio/mikro-server-next/commit/17a9818995d3ac324a82c8aaec5142d541bb9ccc))

- Mor eshit
  ([`600e2af`](https://github.com/arkitektio/mikro-server-next/commit/600e2aff56f3d0b31c72dc4f98749247a5b56f48))

- More dataset features
  ([`645a1cd`](https://github.com/arkitektio/mikro-server-next/commit/645a1cde789dc2144dd3ce4e6feb9edcd66589ba))

- No nore stupid examples
  ([`dfa81b1`](https://github.com/arkitektio/mikro-server-next/commit/dfa81b14fc1d882640bebfa2f9b392c985ce5407))

- Region in parquest
  ([`0a17a47`](https://github.com/arkitektio/mikro-server-next/commit/0a17a477d49eee02b97addf6bbc29779bbd2ccfe))

- Rekuest
  ([`7232c4f`](https://github.com/arkitektio/mikro-server-next/commit/7232c4f97862819c671c1caeffda2d6042fa9cc2))

- Smaller refactor bug fixes
  ([`7966d7e`](https://github.com/arkitektio/mikro-server-next/commit/7966d7ef10b7d7ffdcb5255959bd472cdf3b2e3d))

- Stuff
  ([`ff4a302`](https://github.com/arkitektio/mikro-server-next/commit/ff4a302465f3966a12b62e5b770755409b22fe00))

- Stuff
  ([`1570a25`](https://github.com/arkitektio/mikro-server-next/commit/1570a258ed2ffda9956853e65d89669a0b86997a))

- Test
  ([`614f87e`](https://github.com/arkitektio/mikro-server-next/commit/614f87e60ac5611331ad5b67fe8aed37c9a4f801))

- Test parquet upload
  ([`591f407`](https://github.com/arkitektio/mikro-server-next/commit/591f4076b7a7118c6df12e42c53a717584af259c))

- Test_print_schema
  ([`1b5d483`](https://github.com/arkitektio/mikro-server-next/commit/1b5d483ef13c3efeda59580760acc0731e478981))

- The core refactor of filters
  ([`292bd8d`](https://github.com/arkitektio/mikro-server-next/commit/292bd8d01d99bf277d31fd8ac476443ae0a7c129))

- Trigger bulid and add secret key
  ([`8a9b634`](https://github.com/arkitektio/mikro-server-next/commit/8a9b634bda79cd370197e634f0f3c725dd2c84d4))

- Type names?
  ([`ca0bd83`](https://github.com/arkitektio/mikro-server-next/commit/ca0bd838b7c2a0a64cb800975381d8ad077c78ab))

- Update authentikate dependency to version 0.15
  ([`0676426`](https://github.com/arkitektio/mikro-server-next/commit/06764269d940eb4f194be8800c9afda3ea43cd58))

- Update context reference in file and roi subscription listeners
  ([`591d002`](https://github.com/arkitektio/mikro-server-next/commit/591d00259430e5314949d10dbfc758138abcde65))

- Update datalayer
  ([`f31563b`](https://github.com/arkitektio/mikro-server-next/commit/f31563b94fa7bc6f0034a9e82662871fa19583ca))

- Update Dockerfile to install poetry without root
  ([`78fb33e`](https://github.com/arkitektio/mikro-server-next/commit/78fb33ed1bf7a4c43182fe61ae2e7734cdfccf00))

- Update roi
  ([`0575a34`](https://github.com/arkitektio/mikro-server-next/commit/0575a3401f958a700ce0a5b84297e2a5145d74e2))

- Update to authentikate > 2
  ([`7de94a3`](https://github.com/arkitektio/mikro-server-next/commit/7de94a3e346b5302fed247916c22a9d822ea99e2))

- Update to new authentikate
  ([`ca740b5`](https://github.com/arkitektio/mikro-server-next/commit/ca740b5bcddcb59e622ef5416853428964683fd9))

- Uv.lock
  ([`bb43071`](https://github.com/arkitektio/mikro-server-next/commit/bb430712002da091cffd81a745d37a26c476cf22))

### Features

- Add custom field function with authentication permissions for GraphQL queries
  ([`2469f20`](https://github.com/arkitektio/mikro-server-next/commit/2469f209ae0f9d35289afb8946b3baca51d21cf4))

- Add date-range filters
  ([`eb9933d`](https://github.com/arkitektio/mikro-server-next/commit/eb9933dafba738013ce0e8175bb4d3a469e67809))

- Add delete mutation for histogram views and update related enums and types
  ([`18fff59`](https://github.com/arkitektio/mikro-server-next/commit/18fff59be3e34c68c042a6424528a8742df7e06c))

- Add docker dev workflow
  ([`9491aef`](https://github.com/arkitektio/mikro-server-next/commit/9491aef1f57695632cf7ba0226f25ca89406781b))

- Add HistogramView model and mutation for creating histogram views
  ([`deffbfd`](https://github.com/arkitektio/mikro-server-next/commit/deffbfd7cc11dbc434c8bedd3a3ac6532b9dda17))

- Add instrument organiztation
  ([`f196fc6`](https://github.com/arkitektio/mikro-server-next/commit/f196fc6c7bc78aaca12ec14722e3ba27ecea65af))

- Add more types
  ([`90ae179`](https://github.com/arkitektio/mikro-server-next/commit/90ae17948f77b1016baca7cd8f2addfe52c6406a))

- Add rekuest-compliant describe field
  ([`e2f50a9`](https://github.com/arkitektio/mikro-server-next/commit/e2f50a9147bddd1ab6bceafb5189a4ab085f1ca5))

- Add ROI ordering and history tracking to File model
  ([`42250bc`](https://github.com/arkitektio/mikro-server-next/commit/42250bceb59a20019cf40a4da344c7c102678534))

- Add second description field to Dataset model and update migrations
  ([`ea8077f`](https://github.com/arkitektio/mikro-server-next/commit/ea8077f031314887cf2266031f2d6027505b70c8))

- Add some table updates
  ([`23224a8`](https://github.com/arkitektio/mikro-server-next/commit/23224a8a966102317accfbe05a05a246e73c9a34))

- Add subscription for affine transformation view events
  ([`da6d6de`](https://github.com/arkitektio/mikro-server-next/commit/da6d6de287a91b615ef3ba55d23195d6dcd5bb80))

- Implemented the `affine_transformation_views` async generator function to handle subscriptions for
  affine transformation view events. - Created the `AffineTransformationViewEvent` type to represent
  create, delete, and update events. - Subscribed to the relevant channels and processed incoming
  messages to yield appropriate events based on the message content.

- Add tags field to Dataset model for enhanced tagging capabilities
  ([`079a7c1`](https://github.com/arkitektio/mikro-server-next/commit/079a7c11da610442a53175630b89dd9350a8a125))

- Add the organization features
  ([`fc92ea1`](https://github.com/arkitektio/mikro-server-next/commit/fc92ea1759b59f309cf9727a942457f193febb27))

- Add user authentication check in create_dataset function and include static tokens in AUTHENTIKATE
  settings
  ([`bd0acac`](https://github.com/arkitektio/mikro-server-next/commit/bd0acacba3e8d787f551627d7304533c13e4a125))

- Breaking config
  ([`b478a19`](https://github.com/arkitektio/mikro-server-next/commit/b478a19a81b4e1e15d84c69d5179200c81b812e5))

- Deleted som uneccsary models, remannts of the past
  ([`cd19716`](https://github.com/arkitektio/mikro-server-next/commit/cd1971682a6f96135f955cd2e81c5e468a67b5b0))

- Updated descriptions for scalar types in `core/scalars.py` to remove unnecessary line breaks and
  typos. - Consolidated `@strawberry.type` decorators in `core/types.py` for better readability. -
  Removed redundant line breaks and improved formatting in various sections of `core/types.py`. -
  Streamlined field definitions in `mikro_server/schema.py` for consistency and clarity. - Updated
  dependency version for `authentikate` in `pyproject.toml` and `uv.lock`. - Added new migration to
  `core/migrations` for `mediastore` model to include `file_name` and `mime_type` fields.

- Denormalize the creating task's assigner onto created objects
  ([`30b4017`](https://github.com/arkitektio/mikro-server-next/commit/30b40174799d2470b5177bda4ddc799eca1c022d))

The koherent Task table grows with every assignation, so filtering "objects assigned by user Y"
  through created_through scales with the user's task count. created_through_by (FK -> User,
  SET_NULL) stamps the assigner directly on the object at creation - a write-once fact, so the usual
  denormalization drift risk does not apply.

- created_through_by on all created_through-bearing models (and their historical shadows), stamped
  from the already-fetched task at every create site (task.assigner_id, no extra queries). -
  assignedBy filter now hits the denormalized indexed column instead of joining through the task
  table. - createdThroughBy exposed on the stamped GraphQL types. - Migration 0008 (verified forward
  and backward).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Enhance BigFileStore with filename and mime_type fields, add LocalHash model, and implement
  ensure_dataset mutation
  ([`a59f71d`](https://github.com/arkitektio/mikro-server-next/commit/a59f71d2da08ea5672ea33c7dfcde8a515b92681))

- Extend DatasetFilter with IDFilterMixin and SearchFilterMixin for enhanced filtering capabilities
  ([`dd56150`](https://github.com/arkitektio/mikro-server-next/commit/dd56150e883f2a3391831d40f46df40be48f2a65))

- First attempt at a stats field
  ([`1996d4b`](https://github.com/arkitektio/mikro-server-next/commit/1996d4b393df2f2c649a9a631437d9a13ed1546d))

- Major new things
  ([`bc84093`](https://github.com/arkitektio/mikro-server-next/commit/bc840932fcba5545f299ea7d331ee463b6a1d928))

- Major refactor
  ([`9bf0d74`](https://github.com/arkitektio/mikro-server-next/commit/9bf0d747e94cebe7a29a407ec2e51772039966fe))

- Oh more
  ([`1be9ff8`](https://github.com/arkitektio/mikro-server-next/commit/1be9ff8868f7726c055d82a0d1beb3a97410943f))

- Organization FK on every remaining model — full tenancy coverage
  ([`6a6d5e7`](https://github.com/arkitektio/mikro-server-next/commit/6a6d5e7c82202be711141c9460b71ecddf757622))

- Era, Experiment, MultiWellPlate, RenderTree, ROIGroup, Scene and ViewCollection get a required
  organization FK; core.scoping's UNSCOPED_MODELS escape hatch is now empty - DatalayerStore (and
  thus all polymorphic stores) gets a required organization FK; upload grant generators and finish_*
  take the organization id and stamp/scope the store, and request_*_access store lookups are
  org-filtered — closes the cross-org store claiming hole - creates stamp organization (era,
  multiwellplate, viewcollection, render_tree, scene, plus the 'Unknown' Era/Stage fallbacks); the
  RGBRenderContext fallback also gained its missing required image - hand-written migrations (core
  0006, datalayer 0002) add nullable, backfill (Era via its instrument, else first organization),
  then make non-null; exercised forward, backward and forward-with-legacy-rows on a scratch postgres
  — backfill verified - datalayer mutations: replaced getattr(input, "to_pydantic")() with the
  explicit call and removed stale 'del info' statements - SDL change: MultiWellPlate now exposes
  organization (same shape as Camera/Objective/Instrument already did); everything else unchanged

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Record the rekuest task objects were created through
  ([`4032467`](https://github.com/arkitektio/mikro-server-next/commit/4032467c84a8f540108ae7a4b6acb9e8cb1d6281))

With authentikate 2 every request can carry a validated Rekuest-Task header (task id, assigner, app,
  action, args). koherent 1 persists it as a Task row and links every history entry to it
  automatically; this adds the queryable, denormalized side in mikro:

- created_through FK (-> koherent.Task, SET_NULL) on all creator-bearing models: Image, Render
  (Blurhash/Video/Snapshot), Dataset, File, Table, Stage, Era, ROI, ADataset. - Every create
  mutation stamps created_through=get_or_create_task() explicitly; ensure_dataset passes it via
  defaults so tasks never fork a duplicate dataset; the default dataset helper forwards it. -
  GraphQL: Task type (with TaskFilter/TaskOrder, org-scoped tasks/task queries), createdThrough on
  stamped types, createdThroughTask and assignedBy filters via CreatedThroughFilterMixin, and
  ProvenanceEntry.during is replaced by ProvenanceEntry.task. - Migration 0007: swaps assignation_id
  for the task FK on historical tables and adds created_through (verified forward and backward).

Requires authentikate>=2.0.1 and koherent>=1.0.0 (local packages; release before deploying).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Recovery?
  ([`702de68`](https://github.com/arkitektio/mikro-server-next/commit/702de687d99c3a27223ae7e18ea98c835f633a03))

- Remove bot from anything
  ([`355d828`](https://github.com/arkitektio/mikro-server-next/commit/355d82879800d85a9bb9c07237b9d298f848bc63))

- Up up
  ([`70d2c55`](https://github.com/arkitektio/mikro-server-next/commit/70d2c55d7d44c29c3e4e4439d586d2827495cef6))

- Update authentikate
  ([`8e87a65`](https://github.com/arkitektio/mikro-server-next/commit/8e87a658d291bcf784f0c5a1a732c934753da483))

- Update authentikate
  ([`c85d20c`](https://github.com/arkitektio/mikro-server-next/commit/c85d20c702746b238454be1e12f6459a65e6ecba))

- Update default value for MY_SCRIPT_NAME in settings
  ([`7235e9a`](https://github.com/arkitektio/mikro-server-next/commit/7235e9a81f9a69f1a279e025d9771ac60a7ca253))

- Update request_file_upload and request_file_upload_presigned to handle default mime_type
  ([`b7eba0d`](https://github.com/arkitektio/mikro-server-next/commit/b7eba0d2fd401fc110c5ff3965146da3ea504fce))

- Update to latest kante
  ([`cc12121`](https://github.com/arkitektio/mikro-server-next/commit/cc12121d27e02c60d00227b577a9327bc3f05eda))

- Update to public key
  ([`67d1833`](https://github.com/arkitektio/mikro-server-next/commit/67d1833476afc444ea8399a3698220e9e32f7109))

- With provenance
  ([`4cdea47`](https://github.com/arkitektio/mikro-server-next/commit/4cdea476b0a7810fcd59fe7008e6e85f744a8274))

- With release workflow
  ([`9899ada`](https://github.com/arkitektio/mikro-server-next/commit/9899adace350d145e4bc3ecc620907195f9d19d5))

### Refactoring

- Clean up formatting and remove commented database configuration in settings
  ([`aba1980`](https://github.com/arkitektio/mikro-server-next/commit/aba198025e85dc6db6ae7c793d8c8096c06cb1c9))

- Factor repeated delete/pin mutations into _generic factories
  ([`2a3b8ad`](https://github.com/arkitektio/mikro-server-next/commit/2a3b8ad767433c41686606c59f125e61c831e49d))

- core/mutations/_generic.py: make_delete / make_pin build the resolver bodies as closures; every
  entity keeps its own GraphQL input type and its hand-written create/ensure mutations (explicit
  field mapping, no dynamic marshalling), so the SDL is byte-identical - pin_* is now implemented
  (pinned_by M2M toggle) for dataset, image, roi, era, stage, multi-well plate and mesh instead of
  raising NotImplementedError; entities without a pinned_by field keep their stubs - pinMesh fixed:
  previously took DeleteMeshInput and returned Snapshot (only intentional schema change) - ~40
  duplicate resolver bodies removed across 16 mutation modules

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Split core/models.py monolith into a domain-organized package
  ([`de9e880`](https://github.com/arkitektio/mikro-server-next/commit/de9e8806a28b5c3b7efce7ffa0a1b1fe76ed3a0d))

- core/models/ now has dataset, instrumentation, image, meta, stage, roi, view and adataset modules;
  __init__.py explicitly re-exports every name (including the datalayer stores the old monolith
  leaked) so 'from core import models' is unchanged everywhere - pure move: fields, Meta, helpers
  and class order preserved; verified by empty SDL diff, 'makemigrations --check' clean and 64
  passing tests - adds migration 0005 (related_name-only AlterFields) for pre-existing drift between
  models and migration 0004 that the --check gate exposed; no SQL impact

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

- Split core/types.py monolith into a domain-organized package
  ([`923f5cb`](https://github.com/arkitektio/mikro-server-next/commit/923f5cb0b0019fbf7168aac99dd449273ded79da))

- core/types/ now has auth, credentials, metadata, renders, mesh, instrumentation, acquisition,
  adataset and image modules; __init__.py explicitly re-exports all 93 public names so 'from core
  import types' is unchanged everywhere - the bidirectionally-referencing
  Image/View/ROI/Table/File/Dataset cluster stays together in image.py; seven cross-module
  back-references use strawberry.lazy('core.types.image') - pure move: SDL byte-identical, 64 tests
  pass, lint clean

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>

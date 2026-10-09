# mikro

Notes for whoever changes this service, person or agent.

## Lenses

A lens is a selection over a container that lives in a coordinate system, one row per
(container, normalized selection), and every container has its whole lens from creation
(`core.logic.coordinate_system.whole_lens`). `ArrayLens` selects by slices and, when sliced,
owns the crop's space and the edge back into the grid; `TableLens`, `SparseLens`, `MeshLens`,
`NetworkLens` and `AnnotationLens` select by value windows in the container's own space and
own nothing. **Every layer and chart layer draws through a lens**, whatever its kind; a layer
mutation takes the lens or the container (its whole lens). A lens is reached across a
polymorphic foreign key: prefetch it (`Lens.objects.with_sources()`), never `select_related` it,
or you get the base row. Every kind is hosted *and announced* (`mikro_server/contract.py`,
`mikro_server/service.py`), described in its container's vocabulary by `core/descriptors.py`;
`@mikro/is_whole` tells a crop or a window from the whole lens a container is born with.

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
  annotation collection) its whole lens and folds duplicate array lenses onto one row; the
  database refuses duplicates since the lens tables were split, so it is a guard for rows
  written by hand. It changes nothing the second time and reports what it leaves in place.
- Other jobs: `purge_orphaned_stores`, `respec_datasets`, `backfill_default_scenes`, `backfill_parquet_schemas`. The two backfills are
  safe beside a serving release and report what is still owed; neither is in the setup, so
  an operator runs them.

`tests/test_prepared.py` holds the contract to this: migrations committed, every job a command
of this service, the setup run twice. In `deployments/next` the service runs
`arkitekt-service standalone --debug`, which migrates and then serves: restart its container
to apply a new migration.

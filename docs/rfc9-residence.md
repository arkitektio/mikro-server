# RFC-9: Data lives in a space; a space belongs to nobody

**Status:** Implemented (July 2026).
**Supersedes:** RFC-6's "one truth per space" — both the collision guard and the
one-registration-per-placement rule — the PHYSICAL kind, and the dataset-owned calibration.
**Breaking:** throughout, deliberately.

## The model

Three concepts, and nothing else:

- a **space** is a node (`CoordinateSystem`);
- a **map** between two spaces is an edge (`Transformation`);
- **data lives in exactly one space**, and says so with a foreign key of its own.

`CoordinateSystem` keeps `name`, `epoch`, `organization`, `creator`, `provenance`. It has no
foreign key to anything: it does not know what lives in it, owns nothing, and carries no
classification. Ask it for `residents` and it answers by looking at who points at it.

## What ownership was doing, and why none of it survived

Seven nullable owner FKs used to point back at the containers. They were carrying three jobs:

**Telling a fact from a claim.** `is_registration_target` was "no owner FK is set", and the
walk kept only edges whose output was not one. But that was a second, lossy encoding of what
`Transformation.validity` already states — a pyramid edge is `VALIDATED` because the server
derived it, an authored alignment is `MANUAL`. To know how far to trust an edge, read the
edge. Checked by inspection before committing to this: **every** consumer of
`is_registration_target`, `fact_edges` and `claim_root` died with them.

**Lifecycle.** A cascade was how "this space is no longer used" got said. Residence says it
directly: a space nothing lives in and no scene composes over is garbage, and the orphan
sweep collects it together with its edges.

**`kind`.** It only ever labelled which container pointed back. The honest question about a
space is what data lives in it, and that is a list, not an enum — so `kind`,
`CoordinateSystemKind` and `isAdoptableWorld` are gone and `residents` replaces them. A space
with no residents is a pure reference frame: a world, an atlas. That is the only distinction
the four-value label was really carrying, and `_UNINHABITED` in `core/logic/graph.py` is the
one place that still needs it.

## What the shape buys

**Two special cases evaporate rather than being ported.** A level-0 `DataArray` and an
unsliced `Lens` used to own *no* system, with a null standing in for "the dataset's own grid"
and a convention explaining it. They now simply live in that grid, pointing at the same node
the dataset does. And a calibration stops being a kind of thing: it is a space with an edge
into it, which is all it ever was.

**Spaces become shareable.** The residence key is a plain `ForeignKey`, not a one-to-one, so a
hundred tiles genuinely acquired in one stage frame can say so — while two unrelated
acquisitions still get their own, because the writer creates one each. Sharing is expressible
without being automatic.

**Creation runs one write each, in dependency order.** Space, then the data that lives in it,
then the edges. The old docstring claimed a container-side key would be a cycle; it never
was — every creation path already ran container → system → edge with the edge written last.

## Performance: invert the question, do not prefetch it

The obvious worry is that every ownership read becomes a reverse lookup. It does not, because
the question changes direction.

Ownership asked *space → data*, which was a local column and becomes a reverse lookup the
moment the key moves. **Residence asks *data → space*, and that is `coordinate_system_id` — a
local column on a row the graph modules already fetch.** A dataset's spaces are the distinct
`coordinate_system_id`s of its dataset row, its `DataArray` rows and its `Lens` rows.

Where a batch genuinely needs space → data, `graph.residence_map(system_ids)` answers for a
whole set in three `IN` queries, flat in the spaces *and* in the residents.
`space_graph` goes further and fetches the **residents** of the candidate spaces directly,
which retired a seven-way `select_related`.

Four per-row reads slipped through the first pass, in `placeable_system_ids_in`,
`_fetch_collection_edges` and `scenes_by_sole_dataset` (twice). Each was a free column read
under ownership and a query under residence. **The query-count tests were the only thing that
caught them** — which is exactly what they exist for, and why they must stay.

## What was given up

Placement no longer has exactly one answer. RFC-6's argument was that *"letting the rival row
in would put the choice back into the walk, which is exactly where it must never live"* — and
that is precisely what this reverses. Claim chains are legal, rival claims are allowed, and a
BFS can reach a space by more than one route.

The replacement is an explicit tie-break rather than a guarantee from the data: fewest hops,
then the strongest weakest-validity, then the lexicographically smallest edge-id sequence.
`pathToWorld` returns the winner and `alternativePaths` exposes the losers, so a rival
registration is visible rather than silently discarded.

**This part is designed but not yet built.** See below.

## Consequences the test suite pinned

Rewriting the suite surfaced four behaviour changes worth stating, because each is a
guarantee RFC-9 trades away rather than an accident:

- **A fusion places through *either* parent.** `fact_edges` kept one parent edge per system,
  so registering only the secondary parent placed nothing. Every derivation edge is walkable
  now (`test_a_fusion_places_through_either_parent`).
- **Rivals are written, not refused**, and every scene over one space still composes the
  *same* route -- the choice is a function of the edges, not of the scene
  (`test_a_rival_registration_into_one_shared_world_is_accepted`).
- **Deleting data no longer cascades into its space.** `Scene.world` is still RESTRICT and
  `ADataset.coordinate_system` is PROTECT, so a space is pinned from both sides -- but the
  transitivity is gone, and a scene rooted in a space survives that space being emptied
  (`test_the_container_is_undeletable_while_a_scene_is_rooted_in_its_space`).
- **Every space is adoptable.** The ARRAY refusal had nothing left to stand on.

And two traps that only residence creates, both caught by tests rather than by review:

- **The naive pyramid filter over-matches.** "Edges out of a space one of this dataset's
  arrays lives in" now also catches the calibration edge, because level 0 shares the
  dataset's grid. The pyramid is the edges landing *in* that grid.
- **Collections must be tested before `dataset_behind`.** That helper deliberately follows an
  edge back, and for a collection's space the edge leads to the image the meshes were
  extracted from -- answering with it draws the image straight past `includeMeshes`.

## Not done

- `all_paths` / `best_path` and `Layer.alternativePaths`: until they exist, the walk returns
  whichever path the BFS finds first. It is stable within a process and the suite pins that
  every scene over one space agrees, but the rule is not yet the stated tie-break.
- The orphan sweep still refuses a space any edge touches, so deleting a container can strand
  a space plus its edges. The policy change to "nothing lives here and no scene roots here,
  and its edges go with it" is not written.

## Done since

- The vocabulary now follows the model (July 2026): the shared axis input is
  `PhysicalAxisInput` (written by `create_physical_axes`), the dataset filter is
  `hasPhysicalSpace`, and `calibrated_neighbours` is `physical_neighbours`. The dead
  `CoordinateSystemKind` enums are deleted. "Calibration" in the spatial sense no longer
  names anything in the schema; the word survives only for the unrelated phasor
  instrument-response correction (`PhasorCalibration`).
- The ingest sugar is gone with it (July 2026): `CreateADatasetInput.calibration` was
  briefly renamed `physicalSpace` and then removed outright, together with its input type
  and `create_physical_space`. Physical units enter the model exactly one way -- a
  `createCoordinateSystem` call whose `registrations` entry names the dataset (or a
  separate `createTransformation`) -- and `kind` decides which parameter is read, so a
  pixel size plus a stage offset is one AFFINE matrix, not a SEQUENCE sugar. One
  consequence accepted with it: the per-position axis count/type check died with the sugar
  -- an edge answers only to `assert_edge_rank`, because a physical space is not special.
  (A second consequence, about the in-call `bootstrapScene` always assuming default units,
  expired when that field and the whole dataset bootstrap were deleted -- see below.)
- **The scene/space split is now in the code, not just in the prose (August 2026).** RFC-6
  said "a scene is its world plus its layers"; the model obeyed, but the logic and the API
  did not. The leak had one signature — *a function takes a `Scene` and reads nothing off it
  but `scene.world`* — and it was everywhere:
  - Seven functions in `core/logic/graph.py` took a scene. They now take a `CoordinateSystem`
    and dropped `_in_scene` from their names (`is_placeable_in`, `assert_placeable_in`,
    `placeable_lens_dataset_ids`, …). `placeable_system_ids(scene)` is deleted outright — it
    was a one-line delegation to `placeable_system_ids_in(space)`, which was always the body.
  - The three `placeableIn` filters fetched a whole `Scene` row to reach `.world`. They take
    a coordinate system id now (breaking; pass `scene.worldCoordinateSystem.id`), and are
    organization-scoped, which they were not.
  - `Scene.annotations` and `Scene.coordinateSystems` moved to `CoordinateSystem` as
    `annotations` and `placedSystems`, following `registrations`. Both answered from
    `SceneGraph.reachable_system_ids`, which closed over the world's edges alone and so both
    **under-reported** (a placed dataset's physical space, pyramid grids and derived children
    were reachable and never listed) and **over-reported** (an unregistered layer's own
    systems were seeded and never removed). That closure is deleted; both fields answer from
    `placeable_system_ids_in`, the one implementation the `placeableIn` filters already used.
    The returned sets widen, and nothing pinned the old ones. Both memoize that walk per
    space per request: on `Scene` they shared the scene graph and cost nothing together, and
    without a memo asking a *list* of spaces for both is 2N walks — pinned by
    `test_a_spaces_placeable_set_is_walked_once_per_request`.
  - `SceneGraph` and `SpaceGraph` carried character-identical copies of the lineage walk, the
    adjacency assembly and the collection-edge handling. That half belongs to the space, and
    is now `core/logic/edge_universe.EdgeUniverse`, which both compose. Two asymmetries are
    kept deliberately and commented as such: the **seed set** stays a parameter (a scene
    seeds from two layers, a space from every resident of every placeable space), and
    **organization scoping** stays on `SpaceGraph` only, because it alone hands back whole
    containers. The world's root edges are memoized per request under a key carrying the
    scoping, so two scenes over one world share the fetch and an org-scoped graph cannot
    silently reuse an unscoped one.
  - `create_lens`, `world_axes_for`, the world-axis constants and the world minting inside
    `create_scene` moved to `core/logic/coordinate_system.py`. Nothing that makes a space
    lives in the scene module any more.

  Deleted on the way: five uncalled scene-shaped shims in `graph.py` (`path_in_scene`,
  `placement_path`, `level_placements`, `scene_coordinate_systems`,
  `reachable_coordinate_systems`) — the only `SceneGraph` constructions outside `for_request`,
  so each bypassed the per-request memo, and `path_in_scene` could disagree with the
  placeability predicate it was supposed to mirror; and `TransformationFilter.scene`, which
  walked the membership M2M this RFC's predecessor deleted and had been raising `FieldError`
  ever since, unnoticed because nothing selected it.

  **Still not separated:** `graph._placement_universe` is a third copy of the edge-universe
  concept, kept deliberately flat (one `Q`-union, no buckets) so layer creation stays flat in
  scene size; and `_mint_scene_collection` still takes a scene id and writes a space plus a
  registration, which is declared sugar with an explicit unsugared path.
- **Mirror worlds are deleted, and coordinate systems are the way in (August 2026).** The
  scene bootstrap minted a `CoordinateSystem` whose axes *copied* the dataset's physical
  space (or its pixel axes under default units), then authored an identity registration into
  it named `(mirror)` or `(assumed)`. For a calibrated dataset that was a third space,
  axis-for-axis identical to the second, reached by an edge whose only job was to justify it:

  ```
  dataset.intrinsic ──SCALE──> physical (µm, y, x)      ← already a perfectly good world
                                    │
                                    └──IDENTITY "(mirror)"──> NEW world (µm, y, x)
  ```

  A dataset already has coordinate systems — its pixel grid, and any physical space it is
  registered into. Staging it is `createSceneFromCoordinateSystem` over one of those, which
  authors nothing and was already the tested path
  (`test_a_scene_over_a_calibration_renders_at_physical_scale`, which the suite had been
  calling *"the no-mirror scene the design discussion asked for"* since RFC-6).

  Deleted: `bootstrap_scene`, `world_axes_for`, `_DEFAULT_UNIT_BY_TYPE`, the
  `createSceneFromDataset` mutation, and `createADataset(bootstrapScene:)` — a strict subset
  of that mutation that no test ever passed. No replacement mutation: there is one bootstrap
  path now, and its argument is a space. **Bare `createScene` still mints a world when given
  no `coordinateSystem`, and that is deliberate** — an empty space with default axes is a
  blank canvas, not a copy of anything, and it reads no dataset and authors no edge.

  **Two semantic inversions**, both now pinned by tests rather than left to be discovered:
  - Data staged over its own space has an **empty path** and therefore reads `VALIDATED`,
    where it used to read `UNKNOWN` through the assumed edge. Nothing is assumed about data
    being where it lives (`test_data_in_its_own_space_is_placed_exactly`).
  - `PlacementValidity.UNKNOWN` has no server-side writer left. It exists for a client that
    has a placement and knows it is a guess, and arrives through `createTransformation`.

  **`ScenePolicyInput` gains `kind`.** Deleting the mutation removed the only place a client
  could name a render recipe, and `LABEL` is never inferred from structure — only from a
  derivation declared `CATEGORIZED`. An imported mask would have lost its one-call path. The
  parameter already existed on `_bootstrap_image_layer` and was being dropped on the floor in
  `_materialize_layer`; the policy now carries it. It scopes to image layers only — mesh,
  point, track and annotation layers have no recipe to choose.

  Two tests lost their teeth in the move and were repaired rather than deleted:
  `test_the_weakest_edge_on_the_path_wins` and `test_the_weakest_edge_on_the_path_decides`
  both asserted on a *minimum* over the bootstrap's two-hop path. Staging over the physical
  space collapses that to one hop, where a minimum asserts nothing. Both now use a **sliced
  lens** to restore a genuine second hop, and both assert the hop count explicitly so the
  next collapse fails loudly.

  Guarded by `test_nothing_mints_a_world_for_a_dataset`, which checks the four deleted symbols
  and — the wider net — that no SDL description says one space mirrors another.

  **Not fixed, and worth deciding separately:** `createAnnotation(scene:)` mints a coordinate
  system *and* a registration edge into `scene.world`, which may be an adopted shared atlas.
  Drawing one shape then puts a new space and edge inside a space other scenes compose over,
  where the atlas owner's `clearCoordinateSystem` would delete it — it is guarded by the
  *system's* creator, not the edge's author. The behaviour is defensible (a collection is a
  genuinely new space with no prior home, so stating where it sits is a creation fact) but
  the shared-world case is not. Relatedly, `createMeshCollection` and
  `createAnnotationCollection` default to an **IDENTITY** edge when `derivedFrom` is omitted,
  while `createTableDataset` defaults to UNMAPPABLE with a comment stating the principle the
  other two break: *"naming a source is not the same as claiming a map."*

# RFC-6: One truth per space

**Status:** Draft, implemented on `more-omengfness`.
**Supersedes:** the scene membership set (`Scene.coordinateTransformations`), the
membership-gated placement walk, the `addRegistrationToScene` /
`removeRegistrationFromScene` mutations, and multi-parent placement fallback.
**Unchanged:** the edge table itself, UNMAPPABLE, calibrations, the intrinsic-space
walks, and the GraphQL placement surface (`pathToWorld`, `placement`,
`placementValidity`, `levelPaths`).

> **Amendment (2026-07-24, the migration that introduced it — minted worlds removed).** This RFC
> predates the removal of scene-owned worlds; its body is kept as the historical
> record and three of its claims are now stale. (1) `CoordinateSystem.scene` is
> deleted: a scene *never* owns its world, so the "minted world" of
> `bootstrap_scene` / bare `createScene` is an ordinary ownerless SHARED system.
> (2) Deleting a scene therefore deletes **no** space — the "another scene's
> minted world cascades with that scene" refusal (see "adopting an owned system")
> has nothing left to refuse; two scenes over one space is the ordinary case, and
> a space is removed only through the explicit `deleteCoordinateSystem` (refused
> while any scene roots in it or any edge touches it). (3) The word **hub** is
> retired: with ownership gone, "hub" and "a scene's world" name the same thing —
> an ownerless **shared space**, `kind: SHARED`, `is_hub` deleted from model,
> schema and filters. Everything else — Rule 1–3, the collision guard, the
> fact-tree walk — stands unchanged.

> **Amendment (2026-08-01 — the dataset bootstrap is deleted).** The 0038 amendment
> above narrowed "minted world" to "an ordinary ownerless SHARED system"; this one
> removes the dataset half of it outright. `bootstrap_scene` and its two entry
> points (`createSceneFromDataset`, `createADataset(bootstrapScene:)`) are gone.
> They minted a coordinate system whose axes *copied* the dataset's physical space
> and then authored an identity edge into it — for a calibrated dataset, a third
> space that was an axis-for-axis copy of the second, and an edge whose only job
> was to justify the copy. A dataset already has coordinate systems: its pixel grid
> and any physical space it is registered into. Staging it is
> `createSceneFromCoordinateSystem` over one of those, which authors nothing.
>
> The row below reading *"`bootstrap_scene` | Authors the mirror edge; … the minted
> world survives — it is the composition frame whose seed placement stays a
> revisable, validity-carrying edge (VALIDATED from a calibration, UNKNOWN from
> assumed pixels)"* is exactly what this reverses: there is no seed placement,
> because there is nothing to seed. Bare `createScene` still mints a world when
> given no `coordinateSystem`, and that is untouched — it is an *empty* space with
> default axes, a blank canvas rather than a copy of anything.
>
> Two consequences: data staged in its own space now reads `VALIDATED` with an
> empty path where it used to read `UNKNOWN` through the assumed edge (nothing is
> assumed about data being where it lives), and the server writes `UNKNOWN`
> nowhere — only a client does, by saying so on `createTransformation`.

## The problem: two truth-regimes and a walk that chooses

RFC-5 made every spatial fact an edge between two coordinate systems, and that was
right. But it then had to answer: what happens when two edges *disagree*? A crop
offset cannot disagree with itself — it was measured, it has one value. An
alignment of two independently acquired datasets can: run the rigid registration
and the landmark affine over the same pair and they differ by microns. The old
design answered with a caste system. Edges into owned systems were **facts**,
walkable by everyone; edges into SHARED systems were **claims**, walkable only when
a scene held them in its membership M2M — so rival claims could coexist, quarantined,
and each scene picked its own at walk time via BFS over its blessed subset.

That works, but it puts two parallel truth-regimes in one table, hides the choice
inside a graph search, and makes the common case (one claim, one scene) carry
machinery whose purpose is invisible until the second rival appears. Three
objections, each fatal on its own terms:

1. **Coexisting rival rows are not two truths — they are a fork nobody made.**
   Deliberately revising an alignment is an ordinary, audited write; that is what
   the provenance system is for.
2. **A scene should be a view, not a gatekeeper**: "root the composition at a
   space, walk what's there" — membership re-derives, per scene, a fact that
   should live in the graph.
3. **A walk that *chooses* is a walk that can choose wrong** — silently, at read
   time, differently before and after an unrelated edge lands.

## The model: three rules

**Rule 1 — one current truth per edge.** `updateTransformation` refines an edge in
place; koherent provenance holds the history; the version bump tells dependents the
chain moved. There is no guard against amending a shared claim, on purpose: every
scene over a space moves together because the space moved — that is what "one truth"
means.

**Rule 2 — one claim per (data-tree, space).** An edge into a shared space (a hub,
a world) is a *registration*: the single current answer to "where does this data
sit in this space". Authoring a second registration of the same fact-tree into the
same space is **refused** (`_assert_one_claim_per_space`,
`core/logic/graph.py`): refine the existing one, or register into a **fork of the
space**. `registration1` and `registration2` are claims into *different* worlds —
two spaces, two truths, compared by opening two scenes — never rival rows in one.
The unit of uniqueness is `claim_root`: the primary lineage root for anything
dataset-owned (a derived dataset and its parent are one tree — the derivation
already places the child), the collection for an unmappably-derived feature table,
the system itself for a hub.

**Rule 3 — facts walk a tree.** Every system has at most one walkable edge *out of
its container*: its level edge, its lens edge, its collection derivation — and for
a derived dataset's intrinsic, its **primary** derivation edge only (the first by
the creator's declared order, exactly `primary_derivation_edge`'s rule). Secondary
parents of a fusion stay recorded — `derivedFrom` reports them, provenance keeps
them — but no placement walk crosses them (`fact_edges`,
`core/logic/graph.py`). Within a container everything stays: levels, lenses and
calibrations are children, and a tree has as many children as it likes.

**The corollary that pays for everything: placement is unique by construction.**
Facts give a source one chain; Rule 2 gives that chain one crossing into the
scene's world. There is no chooser anywhere — no membership set, no per-layer
registration reference, no shortest-path tiebreak that matters. A scene is its
world plus its layers; the BFS in the code no longer *searches*, it assembles the
only path there is.

## The running example

Round 1: dataset `A`, a confocal stack, 0.1 µm/px calibration. A segmentation
produces `C`, a crop of `A` — a fact edge, one value, forever. Round 2: the slide
is re-stained and remounted — dataset `B`, which nothing ever measured against `A`.

You compute a rigid alignment and author it: `B → W`. That edge is now *the* truth
of B-in-W: every scene over `W` shows B there, and a layer over `C` places through
`C → A → …` plus `A`'s own claim with no extra authoring. The alignment is a bit
off, so you refine it — `updateTransformation`, in place, audited, every scene over
`W` moves at once, which is correct because they were all showing the same claim.

A colleague computes a landmark affine and tries to author `B → W` again: refused —
one truth per space. They fork: `createCoordinateSystem` mints hub `W2` (or a new
scene mints its own world) and their claim lands there. Comparing the two
alignments is opening the two scenes. Under the old model those two claims sat as
rival rows in one table, and composing `reg_v1` forward with `reg_v2` backward
manufactured a 3 µm map from `A` to itself — the membership gate existed to stop
exactly that walk. Under this model the walk cannot exist: the second row cannot.

## What each existing mechanism became

| Was | Is |
| --- | --- |
| `Scene.coordinate_transformations` M2M | **Deleted** (the migration that introduced it, schema-only — everything is breaking, nothing migrates). |
| `Scene.registrations` (GraphQL) | **Moved to `CoordinateSystem.registrations`.** Derived: the top-level edges into a space — a property of the space, identical for every scene over it, so the scene had no business answering for it. Read as `scene { worldCoordinateSystem { registrations } }`. |
| `is_registration_target` | Kept: the *definition* of a claim (ownership-derived), no longer a gate. |
| `_walkable_in_scene` + membership gate | **Deleted.** `fact_edges` (the tree: claims out, one cross-container primary per system) + the world's own claims. |
| `assert_placeable_in_scene` | An existence check — placed or not, UNREGISTERED vs UNMAPPABLE. **Since renamed `assert_placeable_in` and re-signed to take a `CoordinateSystem`**: it read nothing off a scene but its world, plus the name for the error text (now an optional `destination`). Eleven call sites: the four layer-mutation modules and the bootstrap materializer. |
| `createTransformation(scene:)`, `addRegistrationToScene`, `removeRegistrationFromScene` | **Deleted.** Authoring the edge *is* the placement, everywhere at once. |
| *(new)* | `_assert_one_claim_per_space` + `claim_root`: the collision guard on every claim writer, including the bootstrap mirror. |
| `updateTransformation` | Unchanged, and now the *only* way two states of one placement exist — sequentially, in the audit trail. |
| `lineage_ancestors` | Primary chain, not breadth-first fan: a fusion sits where its primary parent sits. |
| `_derivation_descendants` | Dual of the above: a child descends from its primary parent only. |
| `placeable_system_ids` / `placeableIn` filter | Seeds = the world's registrations; identical answer for every scene over one world. |
| `SceneGraph` | Fetches the world's claims instead of a membership set; buckets hold the fact tree; adjacency is a path-assembler. |
| `bootstrap_scene` | Authors the mirror edge; the membership add is gone. The minted world survives — it is the composition frame whose seed placement stays a revisable, validity-carrying edge (VALIDATED from a calibration, UNKNOWN from assumed pixels), and, per Rule 2, the world's one truth for that dataset's tree. |
| `bootstrap_scene_from_system` | Iterates the hub's registrations; the add/remove dance is gone. |
| `LineageLink` | **Deleted** — a pre-graph relic fully subsumed by derivation edges (typed, primary-ordered, walkable, UNMAPPABLE for history-only). Its `action` belongs to task provenance; its one honest residue became `value_relation` (below). |

## Value relations

A derivation makes two orthogonal statements, and the spatial kind deliberately
carries only one of them: *where the target's pixels sit* says nothing about
*what happened to the numbers*. A threshold is spatially IDENTITY with a wholly
new value domain; a crop is value-identical; an UNMAPPABLE feature table has no
geometry while its values are the point. So the derivation edge carries a second
small field, `value_relation` — **IDENTICAL** (a crop: statistics transfer),
**TRANSFORMED** (a deconvolution: same quantity, new numbers), **CATEGORIZED**
(a threshold or segmentation: values became labels) — on the *same row*, because
it is a fact about the same event; a parallel lineage table for it existed once
(`LineageLink`) and is exactly the two-homes mistake this RFC removes. It is
refused on a registration: a claim relates spaces, and values do not cross it.
The algorithm and its parameters stay with task provenance. First consumer: a
CATEGORIZED primary derivation makes the scene bootstrap infer a LABEL render
graph — closing the documented "nothing structural distinguishes a label map
from an image" gap without an explicit override.

## What this deliberately cannot model

Stated so the door is documented, not just closed:

1. **Redundant registration constraints.** Tile stitching and atlas building are
   over-determined graphs — pairwise overlaps whose redundancy (loop-closure error)
   is the signal an optimizer consumes. A tree cannot hold them *as walkable
   edges*: mikro stores registration **solutions**, not registration **problems**.
   Raw pairwise constraints belong to the optimizer (or to provenance), the solved
   placement to the graph — the position OME-NGFF and neuroglancer take.
2. **Two placements of one dataset in one scene.** The blink-comparator QC view
   (B-under-v1 over B-under-v2) needs one dataset instanced twice in one world;
   uniqueness forbids it. The answer is two scenes over the two spaces, composed
   client-side.
3. **Claim chains.** Tile → section-space → atlas composition would cross two
   claims; a placement crosses exactly one. Uniqueness actually makes chains
   deterministic per hop, but a direct claim beside a via-claim forms a diamond —
   walk-time choice again — so v1 requires a direct claim per space you compose
   in. Revisit if hierarchical spaces become real.
4. **Fusion fallback.** A fusion whose primary parent is unregistered no longer
   places through its secondary. Re-anchor explicitly: reorder `derivedFrom` at
   ingest, or register the fusion's own system.

Also gone, intentionally: the per-channel-lens drift workaround (several claims
from one dataset's tree into one world). The honest fix was always the one the
`Lens` docstring promises — a dataset-owned aligned system with one channel-wise
edge from intrinsic, which is a *fact* and collides with nothing.

## Current gaps

- ~~**ROI staleness is half a system.**~~ **Closed by RFC-8** (July 2026), which also
  corrects the model name: `DataRoi` was replaced by `Annotation` in the migration that introduced it and
  the gap survived the rename, as `Annotation.created_with_transforms`. The stored number
  recorded the chain version a shape was drawn against and `updateTransformation` bumped
  edge versions, but the *current* chain version was exposed nowhere, so nothing could
  compare — the field implied a comparison the API could not perform.
  `CoordinateSystem.transformVersion` is now the read half. The field stays strictly
  provenance-only: the promised bulk bounding-box recompute after a refinement does not
  exist and is not planned, because a refinement does not move a stored vector.

## Open questions

- **Space forking ergonomics.** Forking a hub with N registered datasets to retry
  one alignment means re-registering N-1 of them. A `forkCoordinateSystem` that
  copies a hub's claims (each copy independently amendable) would make Rule 2
  cheap to live with.
- **World-to-world edges** for composing two scenes' truths in one view (the
  comparison story), which is claim-chain territory.

## Resolved since the draft: scene roots on owned systems

With walk-time choice gone, adopting an owned system as a world became
correctness-safe, and it is now allowed (`CoordinateSystem.is_adoptable_world`):
a scene can compose directly over a dataset's INTRINSIC pixel grid, a PHYSICAL
calibration, or a collection's space -- no mirror world, no edge. Exactly two
refusals remain: an **ARRAY** system (a pyramid level, a lens crop) is a slice
of its container's grid, not a space to compose in; and another scene's
**minted world** cascades with that scene and would be deleted out from under
the adopter. The consequences are the model's own rules doing the work:

- The container's data is placed *by construction* -- its fact tree reaches its
  own space with no registration -- so `createSceneFromCoordinateSystem` over an
  owned root materializes the container's layer and authors nothing.
- Only that container's tree can ever compose there: registrations land
  exclusively on SHARED spaces, so foreign data in an owned space is not a
  gap, it is a category error -- wanting it means a hub.
- Lifecycle stays RESTRICT: a dataset whose space roots a scene is undeletable
  until the scene goes, exactly as a hub is.

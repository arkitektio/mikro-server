# RFC-10: Reachability is not placement

**Status:** Implemented (August 2026). Implementation: `is_condensable` and the
`require_affine` thread through `adjacency_of` / `_fact_reachable` / `is_placeable_in` /
`assert_placeable_in` in `core/logic/graph.py`; the affine-first walk in
`SceneGraph.placement_path` / `representative_path` / `level_placements`
(`core/logic/scene_graph.py`); `ScenePolicyInput.skipUnplaceable` (`core/inputs/coords.py`,
`core/logic/scene.py`). Tests: `tests/test_affine_placement_gate.py`, plus the parametrized
refusal in `tests/test_layer_kinds.py`.
**Builds on:** RFC-8, which writes down the invariance lattice this reads its predicate off.
**Unchanged:** every model. No column, no migration — the rule is about edges, and RFC-8's
rule that a spatial fact is a node or an edge and never a column on a view still holds.

## The rule

> **A layer's placement must condense. A route that reaches the world without composing into
> one affine map does not place data a renderer can draw, and creating a layer over it is
> refused.**

## What was wrong

Every layer mutation gated on `assert_placeable_in`, and that gate asked
`is_traversable` — which refuses exactly one kind, UNMAPPABLE. A `FIELD` is traversable and
has no closed form: its map is the values of an array, which is why a segmentation is
expressible at all. So a layer over data registered only through a warp field or a label
mask was accepted, reported a `pathToWorld`, and then made `asAffine` **raise**.

That is not a bug in `asAffine` — `core/types/array_dataset.py` documents the raise, and
raising is right: a path that exists and does not condense is not a null, and the honest
answer is which edge stopped it. The bug was upstream, in a gate that promised something it
had not checked. Reachability answers *is this data related to the world*. Placement asks
*can it be drawn there*, and those came apart at exactly one kind.

Two smaller holes had the same shape. `coords.step_forms` also raises for a rank-changing
per-axis edge — `assert_edge_rank` checks a SCALE or TRANSLATION against the *input* rank
only, so such an edge is writable and surfaces nowhere else. And `placeable_system_ids_in`,
the batched twin backing the `placeableIn` filters and `placedSystems`, shared the loose
predicate, so a picker offered candidates the mutation would refuse.

## The predicate

`is_condensable(edge)` is read off the invariance rather than written a third time.
`step_forms` has exactly two kinds with no closed form — FIELD and UNMAPPABLE — and in
RFC-8's lattice those are exactly `DIFFEOMORPHIC` and `NONE`. So **AFFINE-and-stronger is the
condensable set**, and `invariance_of` already recurses into a composite's children, reads a
childless composite's params as its children, and falls back to NONE for a kind it does not
know. A flat `kind != FIELD` test would have been wrong for a SEQUENCE carrying a field.

The rank clause is the one thing invariance cannot see, because rank is not a property of the
map's class. It is spelled against `_SQUARE_FORM_KINDS` and mirrors `step_forms`' own message.

Nothing here reads a number. `is_invertible` and `invariance_of` keep that discipline for the
same reason and this joins them: numerics do not belong in a metadata answer, and a singular
affine is still caught one altitude down, at write time.

**Backwards needs no twin.** `is_reverse_traversable` already demands an invertible kind and
equal rank on the two sides, and both exclude everything this would.

## The walk must agree, or the guarantee is nominal

`_bfs_tree` maximises bottleneck validity and then minimises hops. Invariance is deliberately
not a key — it is a reported property. So a one-hop VALIDATED warp field beat a two-hop
affine chain, and a layer the gate had just accepted *on the strength of that affine chain*
reported the field, and `asAffine` raised anyway.

`placement_path` therefore searches the affine-only adjacency first and the full universe only
if that finds nothing. **Two passes, not a third cost key**: folding condensability into the
comparator would make a VALIDATED field route lose to an UNKNOWN affine one by a rule buried
in a heap, and each pass now ranks by validity exactly as before.

**A preference, not a filter.** A placement whose only route is a field still reports that
route, still reads `PLACED`, still reads `DIFFEOMORPHIC`, and `asAffine` still errors naming
the edge. That is what keeps the answer intact for rows written before this gate existed and
for the ones written straight through the ORM. Narrowing the universe instead would have
turned all three into nulls and called registered data unregistered.

## What was deliberately not done

- **No fourth `PlacementState`.** Such a layer *is* placed. `placementInvariance` reads
  DIFFEOMORPHIC and `asAffine` errors with the edge id; a third field saying the same thing
  would be a second answer to a question two fields already answer.
- **No backfill.** The guarantee is about creation, not about the database. Nothing here
  justifies deleting someone's layer.
- **`SpaceGraph` is untouched.** It answers a space's extents, has no single destination, and
  already reports `ExtentState.INVERTED` / `NON_AFFINE` rather than guessing.

## Bootstrap keeps one rule, with an opt-out

`createSceneFromCoordinateSystem` holds the same line `createLayer` does: one source the
world does not place affinely refuses the whole build, naming the source. A scene silently
missing a layer is harder to notice than a refusal, and a builder with its own rule would be
two answers to one question.

`ScenePolicyInput.skipUnplaceable` is the opt-out, and it skips exactly as an array too small
to render or a table with too few coordinate columns already does — including that a skipped
source does not count against `nchildren`, which caps the sources materialized rather than the
sources looked at.

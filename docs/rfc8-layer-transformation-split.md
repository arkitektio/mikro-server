# RFC-8: The layer / transformation split, and what a map preserves

**Status:** Implemented (July 2026). Implementation: `TransformInvariance` in
`core/enums.py`, `invariance_of` / `weakest_invariance` in `core/logic/graph.py`,
`placement_invariance` in `core/logic/scene_graph.py`, `Transformation.invariance` and
`CoordinateSystem.transformVersion` in `core/types/coords.py`,
`Layer.placementInvariance` in `core/types/adataset.py`. Tests:
`tests/test_transform_invariance.py`, plus one in `tests/test_annotations.py`.
**Supersedes:** nothing. It writes down a rule that already governs the code and closes the
one gap the audit against that rule found.
**Unchanged:** every model. This RFC adds **no column and no migration** — every field it
introduces is derived on read.

## Why write this down

The rule that governs the coordinate model has never been stated in one place:

> **A spatial fact is a node or an edge, never a column on a view. A layer's spatial
> questions are answered by deriving over its path, and stored nowhere.**

It lived in scattered docstrings and one migration message. That is how `Layer.affine_matrix`
and `Layer.validity` were written in the first place: nothing said, in a form a reviewer
could check a proposed field against, that a per-view copy of a per-edge fact is the bug.
the migration that introduced it removed them and gave the reason —

> a per-layer copy of a per-edge fact — two layers over one dataset carried two copies of
> how-known one registration is, free to disagree — and nothing ever wrote it.

— but as a note on one migration, about one field.

## The audit: the split holds

Checked against the rule, the separation is clean:

- **`Layer` carries no spatial column** (`core/models/adataset.py:633`). `affine_matrix`,
  `validity`, `status` and `x_dim` are all gone. What remains is compositing (`blending`,
  `opacity`, `visible`, `order`), source foreign keys, and render settings.
- **Coordinates are never per-layer.** A point or track layer does not store which columns
  hold the coordinates; the `TableDataset` declares them by role, and a second per-layer copy
  could disagree with the dataset's own schema. Only display picks (`size_column`,
  `color_column`) are per-layer, and those are honestly view state.
- **Every path-shaped answer is derived** through the request-scoped `SceneGraph`:
  `pathToWorld`, `placement`, `placementValidity`, and now `placementInvariance`. Fixing one
  edge fixes every layer that looks through it, because there is only one copy of the fact.

Two boundaries are easy to mistake for violations. Both are decided, and stay decided:

- **A render graph's projection node collapses z.** Spatially that is a rank-reducing map,
  but it says what is *drawn*, not where the data *is* — the geometry is untouched and no
  placement changes. The same line `PreferredView` draws: the render graph says what the
  pixels are, never where the eye goes.
- **`placementValidity` returns `UNKNOWN` for two different situations** — no path at all,
  and a path resting on an assumed edge. Deliberate: `placement` is the field that separates
  UNREGISTERED from UNMAPPABLE, and duplicating that distinction into a second field would be
  two answers to one question. `placementInvariance` inherits the same conflation and the
  same answer.

**Accepted, not fixed: `PlacementValidity` collapses two orthogonal axes.** `MANUAL` and
`INFERRED` say where a number came from; `VALIDATED` says it was checked. An
authored-then-checked registration reads `VALIDATED` and loses the fact that a human authored
it. Splitting it into a source plus a checked flag would be honest and is not worth a
migration: clients want one badge, and the authoring act is already in the provenance record,
which is where the placement's earlier states live anyway.

## The one leak: scalar lengths in scene units

`Layer.pointSize` and `Layer.lineWidth`, `Annotation.strokeWidth`, and the camera's
`crossSectionScale` / `projectionScale` are **scalar lengths in a space whose axes need not
share a scale**.

The bare-number convention is not the problem and is not reopened — it is argued in
`core/render/camera/__init__.py`: these coordinates are read against the world system, whose
axes carry the units, and quantity-typing them would put a second copy of a unit next to the
axis that already owns it, free to disagree. What was missing is the **denominator**:

- An **unregistered** layer has no world, so "3 scene units" denominates in nothing.
- Under an **anisotropic** world there is no single scene unit at all. A scalar length is
  well-defined only when the map to world is a *similarity*.

So a scene unit is hereby **the world's spatial-axis unit, meaningful for a given layer
exactly when its path to world is `SIMILARITY` or better** — and that condition is now
machine-readable rather than folklore, which is what the rest of this RFC is for.

## Invariance: what a map preserves

Each transformation kind preserves a different amount, and the classes nest:

**ISOMETRY ⊂ SIMILARITY ⊂ AFFINE ⊂ DIFFEOMORPHIC ⊂ NONE**

| Class | Survives | Kinds |
|---|---|---|
| `ISOMETRY` | distances, angles, areas | IDENTITY, TRANSLATION, ROTATION, MAP_AXIS |
| `SIMILARITY` | angles, length *ratios*; lengths scale by one factor | SCALE with equal entries |
| `AFFINE` | parallelism, area *ratios* | SCALE with unequal entries, AFFINE |
| `DIFFEOMORPHIC` | topology at best, locally | FIELD |
| `NONE` | nothing | UNMAPPABLE |

A `SEQUENCE` or `BY_DIMENSION` is **the weakest of its children**. (This line said `BIJECTION`
too; that kind was deleted on 2026-08-21 -- zero rows, no writer, and it composed to a silent
identity. See item 15 / D2 of the proposals doc named in `core/logic/coords.py`'s module
docstring.) A *childless*
one — the ordinary shape of a registration crossing a rank boundary — reads the map it
carries in its own parameters, taking the weakest of them.

A **path** is the **minimum** over its edges. That is a minimum for a stronger reason than
caution: the classes are nested groups, so a composition belongs to the weakest group any of
its factors belongs to. Every class is closed under inversion — the inverse of an isometry is
an isometry — which is why the placement walk's `inverted` steps need no special handling.

### Why this is derived and never stored

The same reason `CoordinateSystem.kind` reads its owner foreign keys: a stored class could
contradict the parameters, and the parameters would be right. There is no
`TransformInvarianceChoices`, because a Django choices twin exists only for a column.

### The one number it reads, and the one it refuses

`SCALE` is classified by asking whether its entries are equal — a comparison, not numerics,
and the whole difference between a shape at another size and a shape sheared.

`AFFINE` reads `AFFINE` even when its matrix happens to be a rotation. Proving it rigid needs
an SVD, which is linear algebra inside a metadata answer, and `is_invertible` already stops at
exactly that line by offering a singular affine for inversion when only a determinant would
catch it. Both err toward claiming less than is true, which is the only safe direction: every
caller is asking what it may trust.

### What a client does with it

- **Report a measurement.** An area converts across an affine by |det|; a *circularity* does
  not survive an anisotropic scale at all, because it is an angle-and-aspect quantity; across
  a `FIELD` warp neither survives, since the Jacobian varies with position. Before this, all
  three came back looking alike — a path, and a plausible number.
- **Draw a scale bar.** Well-defined from `SIMILARITY` up; under a general affine the
  micrometres per screen pixel depend on which direction you measure.
- **Light a mesh.** Normals carry straight through an isometry and need the inverse-transpose
  under a general affine.

## Closing the ROI-staleness gap

RFC-6 listed this as a gap against `DataRoi`, which the migration that introduced it replaced — and the gap
survived the rename. `Annotation.createdWithTransforms` records the chain version a shape was
drawn against, and `updateTransformation` bumps edge versions, but nothing exposed what the
chain version *is now*. The stored number had nothing to be compared with, so it implied a
comparison the API could not perform.

`CoordinateSystem.transformVersion` is the missing read half: the summed version of the chain
from a system down to its dataset's intrinsic pixel space, as it stands. Equal to a shape's
`createdWithTransforms` means the geometry was authored against the chain still in force;
different means a registration or calibration on the path has been refined since.

Derived on demand rather than denormalized: a refinement anywhere on a chain would otherwise
have to fan out and rewrite every system below it, and one of those writes would eventually be
missed. Both halves stay strictly provenance — neither takes part in resolving a coordinate,
and a refinement never moves a stored vector.

## Extents, closed: `CoordinateSystem.inView`

The "chart extents" gap this RFC opened is now closed by `inView(region:)`, a field on the
coordinate system — the system being the frame the region is written in. It returns each
registered source with its extent here, the path that places it, its invariance and
validity, and the `CoordinateAnchor`s in view.

Three things it establishes, all of which follow from the rules above:

**The server composes to cull, and still never composes to answer.** A composed matrix is a
predicate here, never a returned value — the same standing that `Annotation.bbox_cube` has
against `intrinsic_bbox`. `path` still comes back as edges for the client to compose. The
old justification for the blanket rule ("the same dataset can sit in two scenes under two
registrations") was already stale under RFC-6: one truth per space means a fact tree has at
most one path into a given space.

**An extent is partial, and says which axes it constrains.** `to_matrix` has no
`BY_DIMENSION` branch, and `BY_DIMENSION` is what every ordinary registration is written as
— so composing a placement path at one fixed rank fails in the common case, not the edge
case. Composition is instead by *substitution*, one affine functional per destination axis
(`compose_forms` in `core/logic/coords.py`), and an axis the path stops constraining is
simply absent. A `(c,y,x)` dataset registered onto the `(y,x)` of a `(z,y,x)` world is a
slab: writing a `0` for z would cull it out of every view it is really in.

**Refusing to bound something is not knowing it is out of view.** A mesh collection's
vertices are Parquet the server never opens; a `FIELD` on the path has no closed form; a
backwards edge would need an inverse this server does not have. All three come back with an
`ExtentState` and a full path rather than being culled. Only `UNMAPPABLE` — a declared
non-correspondence — is genuinely excluded.

It also closes a hole it had to: nothing named the frame an annotation collection's stored
boxes are in, though `nearestAnnotations` and `AnnotationFilter.intersects` both assume one.
`AnnotationCollection.bbox_system` names it, with **null meaning the collection's own
system** — the `Transformation.field` convention, adopted for the same reason, since a real
self-FK under `PROTECT` beats the collection's own `CASCADE` and makes it undeletable.

## What is still open

- **A quantitative uncertainty.** Invariance is qualitative: it says whether a length
  transfers, never how well a registration is known in nanometres. A residual (FRE/TRE) on the
  edge, composed in quadrature along a path, is the quantitative refinement of
  `PlacementValidity` — needed before the API can honestly back an "these are 40 nm apart"
  claim.
- **Authored bounds for mesh and table collections**, so they can be culled rather than
  always returned. The box would be a fact about geometry the server cannot read, supplied by
  the writer that produced it — not a cache of a registration, which is what makes it
  different from a stored extent.
- **Shape-level culling**, which needs the region pulled back into a collection's frame, and
  so a matrix inverse (there is none, and no numpy).
- **A named-axis region input.** `region` names a leading *prefix* of the system's axes,
  which is the wrong end for the natural viewport case — a `(t,z,y,x)` system probed with a
  2-D `(y,x)` box.
- **Time-varying transforms.** Stage drift over a long timelapse is six numbers per frame; it
  can only be expressed today as one affine that is wrong for every frame but one, or as a
  full displacement `FIELD` array, which is far heavier than the fact deserves.

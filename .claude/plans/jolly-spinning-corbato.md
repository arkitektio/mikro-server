# Persist `ADataset.spec` as a materialized column, written at creation

## Context

`ADataset.spec` (a `List[ADatasetSpec]` — one spatial member SCALAR/PROFILE/IMAGE/VOLUME/HYPERVOLUME
plus a modifier per acquisition axis: TIMESERIES/MULTICHANNEL/SPECTRAL/FLIM) is today **derived on
every read** from the axes of the dataset's intrinsic coordinate system, and its SQL twin — the
`spec` filter — is a set of distinct `Count` annotations over the axis relation, wrapped in a long
comment (filters.py:502-547) explaining the alias-collision hazards that machinery creates.

The current design deliberately does **not** store it. The stated reason (enums.py:405-411,
adataset.py:19-38, types/adataset.py:116-117, filters.py:502-547) is that a stored copy "could
disagree with the axes it is derived from." **That reason no longer applies: axes are immutable
after creation** (adataset.py:27-32 — only `name`/`description` are ever editable; an axis edit is a
new dataset, not a correction). A value computed from immutable inputs at write time cannot disagree
with its source — exactly the precedent `DataArray` already sets by storing absolute pyramid scale
"at write time" on the edge (adataset.py:131-135).

So this is **not a reversal** of the codebase's principle but an application of its real rule: *never
store a value that can disagree with its source.* We materialize `spec` into a column at creation
(the only write moment), keep `core.logic.coords.specs_for_axes()` as the single source of truth
(the column is materialized **from** it, never re-derived), and collapse the `spec` filter to a JSONB
containment query. Payoff: reads and filters stop joining the axis relation, and a class of
Count-annotation machinery plus four now-orphaned helpers are deleted.

Scope: `TableDataset` has **no** `spec` concept (only `axis_names`), so this is ADataset-only.

## Settled design decisions

- **Storage form: `models.JSONField(default=list)`** — list of raw enum-value strings. The repo uses
  `JSONField` everywhere; no `ArrayField` exists. Verified: a JSONB `__contains` (`@>`) query gives
  the exact "satisfies every one of these specs" semantics — and because a stored list holds exactly
  one spatial member plus modifiers, `spec__contains=['IMAGE','VOLUME']` matches nothing
  automatically (no special-casing), `['VOLUME','TIMESERIES']` matches 3D timelapses, and
  HYPERVOLUME is stored literally so its old `>= N space axes` range test moves to write time and
  `__contains=['HYPERVOLUME']` matches directly. A headless dataset stores `[]` and is excluded by
  any non-empty request with **no isnull guard needed**.
- **Column named `stored_spec`; the `spec` property reads and coerces it** to `List[ADatasetSpec]`
  members (`[enums.ADatasetSpec(v) for v in self.stored_spec]`). A distinct column name avoids
  shadowing the `spec` property / kante resolver; enum coercion is **mandatory** (the GraphQL field
  type is `List[ADatasetSpec]`), not cosmetic. The resolver at types/adataset.py:121 (`return
  self.spec`) stays byte-identical.
- **Empty list is the "no intrinsic system yet" state**, preserving today's behavior. A genuine
  no-SPACE-axis dataset stores `['SCALAR', ...]`, so SCALAR and "headless" stay distinguishable.
- **GIN index: yes** (chosen). `GinIndex(fields=["stored_spec"], name="adataset_spec_gin")`, mirroring
  `anchor_coords_gin`, to speed the `@>` containment filter. ADataset has no `Meta` today, so this adds
  one, and the migration includes an `AddIndex`.

## Write site — Option A, decided: materialize inside the shared axis-writer

The test seed `_seed_adataset_sync` (tests/seed.py:106-142) **duplicates** the creation logic instead
of calling `create_adataset`, and every spec test routes through it — so writing the spec only in the
mutation would leave every seed-created dataset (and the tests) with an empty column. Both paths call
`graph_logic.create_pixel_axes(system, axes)`, which is therefore the single shared write site.

Materialize there (graph.py:27-56), guarded on `system.intrinsic_of` — the nullable OneToOne
(coords.py:80-87) that is the dataset **only** for the intrinsic system (null for pyramid/array
systems, so they no-op). The function already builds the `AxisSpec` list inline at line 40 for the
time-axis assert; **hoist it to a local `axis_specs`** and reuse it for both the assert and the
materialization:

```python
axis_specs = [coords_logic.AxisSpec(name=a.name, type=a.type.value if hasattr(a.type, "value") else a.type) for a in axes]
coords_logic.assert_at_most_one_time_axis(axis_specs)
rows = models.Axis.objects.bulk_create(rows)   # existing bulk_create, now assigned
dataset = system.intrinsic_of                  # None for array/pyramid systems
if dataset is not None:
    dataset.stored_spec = [s.value for s in coords_logic.specs_for_axes(axis_specs)]
    dataset.save(update_fields=["stored_spec"])
return rows
```

Every current and future path that writes intrinsic axes materializes spec automatically — drift-proof.

## Implementation

1. **Model** (`core/models/adataset.py`): add `stored_spec = models.JSONField(default=list, help_text=...)`
   near line 62 and a `Meta` with `GinIndex(fields=["stored_spec"], name="adataset_spec_gin")`; rewrite
   the `spec` property (92-102) to `return [enums.ADatasetSpec(v) for v in self.stored_spec]`; update
   the class docstring (24-32) to name `spec` as the materialized-at-creation exception, safe because
   axes are immutable (DataArray precedent at 131-135).
2. **Write site** (`core/logic/graph.py` `create_pixel_axes`): as above.
3. **Filter** (`core/filters.py:522-547`): replace the body with an empty-guard + one line —
   `Q(**{f"{prefix}stored_spec__contains": [s.value for s in value]})`; delete the 502-547 comment
   block and the isnull guard. `_annotate_axis_type_count` stays (used by `has_axis_types` at 554) but
   its now-dead `count_axes=True` branch is dropped.
4. **Dead code** (`core/logic/coords.py`): delete the four helpers used only by the old filter —
   `is_spatial_spec`, `spatial_count_for_spec`, `hypervolume_min_spatial_count`, `axis_type_for_spec`
   (219-236). Keep `specs_for_axes`, `spatial_axes`, and both dicts. Fix two stale comments: the
   `specs_for_axes` docstring (208-210, "the filter also expresses in SQL / the two must agree") and
   the `_SPATIAL_SPEC_BY_COUNT` comment (181-182, "the filter reads this as a `>=`") — the `>=` now
   happens here at write time.
5. **GraphQL type** (`core/types/adataset.py:116-117`): reword the description ("materialized from the
   axes at creation", drop "derived…never stored"). Resolver body unchanged.
6. **Enum docstring** (`core/enums.py:403-411`): reverse "Never a DB column…would disagree" to the new
   rationale; keep the true part (still a strawberry-only enum, no TextChoices twin).
7. **Migration `0034_spec_is_stored.py`**: `AddField` on **both** `adataset` and `historicaladataset`
   (simple-history via `ProvenanceField`; mirror `0024`), then `RunPython` backfilling live rows only
   (pattern: `0022`/`0019`) — compute `specs_for_axes` from each dataset's intrinsic axes via
   `apps.get_model` + the pure `AxisSpec`/`specs_for_axes` logic (documented frozen-in-time tradeoff,
   accepted to keep one source of truth). `AddIndex` for the `adataset_spec_gin` GIN index. **Backfill
   is NOT exercised by the suite** — `settings_test` disables migrations — so verify it manually on a
   real DB.

## Verification

- `tests/test_filters_newmodels.py:107-175` already covers the field and filter exhaustively (spatial
  partitioning, stacked modifiers, `[IMAGE,VOLUME]`→∅, `[SCALAR]`→∅, `[HYPERVOLUME]`→∅, `[]`→all,
  headless→`[]`). All route through the seed, so all depend on the seed write. They must pass
  **unchanged** — the strongest proof the JSONB rewrite is behavior-preserving.
- Add: a real **SCALAR** dataset to `_seed_spec_datasets` (a no-SPACE-axis dataset) so `[SCALAR]` has a
  positive match distinct from headless `[]`; a **materialization assertion** (`dataset.stored_spec ==
  ["VOLUME","TIMESERIES","MULTICHANNEL"]` raw and `dataset.spec == [ADatasetSpec.VOLUME, ...]` coerced);
  and a **mutation-path** test asserting `createADataset` populates the column (separate edit site from
  the seed under Option B). Touch up the two test docstrings that describe the old outer-join/annotation
  mechanism (161-166, 180-204).
- Run the full suite; then **manually apply the migration on a real Postgres copy** and spot-check
  `stored_spec` against the `spec` property for a sample of datasets (the suite cannot).

## Critical files
`core/models/adataset.py`, `core/filters.py`, `core/mutations/adataset.py`, `tests/seed.py`,
`core/logic/coords.py`, `core/logic/graph.py` (Option A), `core/enums.py`, `core/types/adataset.py`,
`core/migrations/0034_spec_is_stored.py` (new), `tests/test_filters_newmodels.py`.

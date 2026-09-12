# Quantity storage design

How physical quantities (durations, voltages, lengths, temperature, …) are
represented on the wire, in memory, and at rest — and why.

## TL;DR

| Layer | Representation | Defined in |
|---|---|---|
| GraphQL wire | explicit Pint string (`"100 µs"`) | `kanne/scalars.py` |
| In memory (pydantic) | plain canonical **int** (e.g. picoseconds) | — |
| Typed DB columns | canonical **int** (`BigIntegerField`) | `kanne/fields.py` |
| JSON blobs (`json_model`) | **dual struct** `{canonical, given, unit}` | `kanne/quantities.py` |

A quantity is parsed once at the GraphQL boundary into an integer count of a
per-dimension **canonical sub-unit** (picoseconds, femtovolts, femtosiemens,
nanokelvin, picometers — chosen fine enough for electrophysiology and lossless as
a Python `int`/BigInteger). That int is the in-memory and column form. When a
config is persisted into a JSON blob it is expanded to a self-describing struct.

```jsonc
// NeuronModel.json_model leaf
"tau1": { "canonical": 1000000000, "given": "1 ms", "unit": "picosecond" }
```

- `canonical` — exact integer in the canonical sub-unit. Numerically **searchable**
  (Postgres JSONB), sortable, and dedup-stable.
- `given` — compact human string. Readable and **self-describing** (carries its unit).
- `unit` — the canonical sub-unit, so `canonical` is decodable with **zero external
  docs** — it survives even if Pint can no longer parse `given`.

## Why this shape

The decision was driven by three facts about how this data is used:

1. **External / third parties consume these values.** The unit must therefore live
   *in the data*, not in a naming convention or separate documentation — otherwise a
   consumer sending `100` (ns? µs?) is a silent, dangerous units-confusion bug.
2. **Stored quantities should be searchable** (numeric range queries) while staying
   human-readable.
3. **Precision needs are unspecified.** We must not silently discard precision.

### Options considered

```jsonc
// A — canonical int only        exact + searchable, but OPAQUE to outsiders
{ "duration": 100000000 }

// B — Pint string only          readable + self-validating, but NOT numerically
{ "duration": "100 µs" }        //   searchable, and meaning is hostage to Pint

// C — fixed-unit plain number   simplest, but unit lives in a convention →
{ "duration_us": 100.0 }        //   third-party unit-confusion + float drift

// D — dual struct  (CHOSEN)     A's exact/searchable int + B's readable/unit string
{ "duration": { "canonical": 100000000, "given": "100 µs", "unit": "picosecond" } }
```

- **A** is exact and searchable but `100000000` is undecodable to an outsider
  without our internal scale doc, and it throws away the user's units.
- **B** is readable and self-validating but strings sort lexically (not searchable)
  and the blob's meaning depends on Pint parsing `"µs"`/`"degC"` identically forever.
- **C** is simplest and is how NEURON thinks internally, but a bare number puts the
  unit in a convention — the exact third-party failure mode — and invites float drift.
- **D** keeps A's exact, searchable, dedup-stable int *and* B's readable, unit-bearing
  string, and is the **most durable**: `canonical` (+ `unit`) is parser-independent.
  "Precision unsure" is mooted — nothing is discarded and the canonical sub-unit has
  generous headroom.

## Implementation

Scoped deliberately to the **JSON path only**. The typed `QuantityField` columns are
already numeric/searchable, so they stay plain ints; the GraphQL wire is already
explicit strings. Concretely:

- `kanne/scalars.py` — the wire scalars. `parse_value` (string → canonical int) and
  `serialize` (int → compact string) are **unchanged in contract**. Two helpers were
  factored out for reuse: `format_quantity` (int → compact string) and
  `canonical_base_unit` (→ e.g. `"picosecond"`).
- `kanne/quantities.py` — per-dimension pydantic storage types, each an
  `Annotated[int, BeforeValidator, PlainSerializer]`:
  - **in memory the value stays a plain `int`** — so defaults, hashing, the strawberry
    bridge, and column writes all keep working with no special handling;
  - `model_dump(mode="json")` expands it to `{canonical, given, unit}`;
  - the validator is **tolerant inbound** — accepts the canonical int (GraphQL-input
    path), the `{canonical, …}` dict (JSON read-back), a Pint string, or a bare legacy
    int.
- `core/base_models/{input,type}/model.py` and `.../topology.py` — the `ModelConfig`
  family fields changed from `int` → the `kanne.quantities` types. The strawberry
  layer (`.../graphql/*.py`) is untouched: fields stay `kanne.scalars.*`, so the wire
  contract and the GraphQL SDL are unchanged.

### Consequences that "just work"
- **Dedup** (`core/mutations/neuron_model.py::get_model_hash`) hashes the strawberry
  config, whose values are canonical ints — so `"1 ms"` and `"1000 µs"` still hash
  equal. No change needed.
- **Backwards compatibility** — old `json_model` rows hold bare ints; the validator
  accepts them, and they upgrade to the struct on the next write. **No data migration
  is required.**
- **Search** — query `json_model -> 'duration' ->> 'canonical'`. For top-level fields,
  add a JSONB expression index when a real query workload appears. Quantities nested
  inside arrays (`cells[].topology.sections[].diam`) are queryable but not cheaply
  indexable.

## Declined: verbatim `given`

`given` is **derived** at serialize time (the compact form, e.g. `"0.1 ms"` →
`"100 µs"`), not the user's exact spelling. Echoing the verbatim string would require
`parse_value` to stop coercing to an int and instead carry the original string through
**every** quantity field and model in the codebase (config *and* column inputs), a
large, invasive change for marginal benefit. The wire already re-serializes to the
compact form today, so no fidelity is lost relative to current behaviour.

## Tests

`kanne/tests/test_quantities.py` — in-memory int, JSON struct expansion, string parsing,
struct round-trip, legacy bare-int reads, unit-independent value equality (dedup), and
dimensional-mismatch rejection. `kanne/tests/test_scalars.py` — the wire scalars.
The `createNeuronModel` end-to-end and dedup paths are covered by
`tests/neuron_model/` once the integration backend is running.

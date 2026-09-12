# The sparse store format

The format is **specified in the `sporadik` package**, not here. Its `README.md` is the normative
document: the prefix layout, the block, the encoding-by-rank rules, the ravel definition, the
chunking policy and the invariants a conforming reader checks.

This file exists to say where the specification lives and why this server does not import it.

## Why this server reimplements it

`datalayer/` parses a sporadik store with `json` and nothing else — no `sporadik`, no `zarr`. That
is the same relationship it has with fabriks (`datalayer/fabriks.py`), and for the same three
reasons:

- **A server that imports a writer inherits its dependencies.** Reading one small JSON object needs
  none of them.
- **A version skew becomes an outage.** If registration went through the writer's parser, a release
  that changed its dataclasses could stop a running server reading stores it had already accepted.
  Parsing the wire format directly makes the *format* the contract.
- **The reader is not the writer's mirror.** A writer's types exist to be round-tripped; what this
  needs is narrower and stricter — *did this thing declare a format I can read, and does what it
  declared hang together.*

Which is also why there is no normative content in this file. Two documents saying the same thing
drift; the one that drifts silently is the one nobody runs.

## What lives here instead

| where | what |
|---|---|
| `sporadik/README.md` | the format, normatively |
| `sporadik/sporadik/spec.py` | the constants and rules, as code |
| `datalayer/models.py` | this server's independent copy of them |
| `datalayer/datalayer.py::get_sparse_metadata` | the reader, run at `finishSparseUpload` |
| `tests/test_sparse_metadata.py` | this reader's conformance, against fabricated trees |

`tests/test_sparse_metadata.py` asserts this server's constants against its own copy rather than
against sporadik's, deliberately: importing the package to check agreement would be the dependency
the whole arrangement exists to avoid. What keeps the two honest is that both are checked against
the same written specification.

## The core of it, for orientation only

```text
<prefix>/
  zarr.json                     attributes: {"sporadik": {...}}   <- written LAST
  layouts/
    axis0/                      a sparse group, anndata-spelled
      data/ indices/ indptr/
    axis1/
```

A layout is **one axis made contiguous**; `len(indptr) == shape[indexed_axis] + 1` at every rank.
Everything else — including why the block is last, which is the part that matters most — is in the
specification.

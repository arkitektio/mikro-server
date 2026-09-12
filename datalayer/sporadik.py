"""Reading a sporadik store's own metadata without depending on `sporadik`.

`sporadik` is the library that *writes* this format, and its ``README.md`` is the specification.
This module reads what a store says about itself, and it does so with `json` and nothing else --
the same relationship this datalayer has with `fabriks` (:mod:`datalayer.fabriks`) and with zarr,
where ``get_zarr_metadata`` parses ``zarr.json`` by hand and no zarr package is installed. The
reasons are the same three:

**A server that imports a writer inherits its dependencies.** `sporadik` needs numpy and zarr to do
its job. None of that is required to read a handful of small JSON objects, and all of it would have
to be installed, pinned and upgraded in a service whose entire interest in a store is "is this
registerable, and what does it declare".

**A version skew becomes an outage.** If registration went through the writer's parser, a `sporadik`
release that changed its dataclasses could stop a running server reading stores it had already
accepted. Parsing the wire format directly means the *format* is the contract, and the writer and
the server are two independent implementations of it -- which is also the only arrangement in which
"the format is specified" is a testable claim rather than a shared object file.

**The reader is not the writer's mirror.** `sporadik`'s ``Layout`` exists to be *round-tripped*: it
carries the arrays, the constructors and the vocabulary a writer needs to make choices. What a
server needs is narrower and stricter -- did this thing declare a format I can read, and does what it
declared hang together.

**What this module deliberately does not do:** open a chunk. Everything here comes from the
``zarr.json`` objects, which are small and few. Whether the *values* are there is a question this
cannot answer and does not try to -- which is exactly why the format has a completion marker, and
why its absence is the one refusal below that matters most.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The spec versions this server can read. A version selects how every byte in the prefix is
#: interpreted, so an unknown one is refused rather than accepted and read as if it were familiar.
#:
#: Kept in step with ``sporadik.SPEC_VERSION`` by the contract, not by an import.
SUPPORTED_VERSIONS = frozenset({"1"})

#: Where the block lives, in the root group's own attributes. Namespaced, and named for the format
#: rather than for this project: a format that names one of its consumers in its own bytes is one a
#: second consumer cannot honestly use.
BLOCK_KEY = "sporadik"

#: The group the layouts hang under, one level below the root.
LAYOUTS_GROUP = "layouts"

#: The lowest rank the format describes: a compressed axis needs at least one other axis to hold the
#: positions. **There is no highest** -- a layout is one axis made contiguous, so an array of rank
#: *n* has up to *n* of them, and rank two is where that coincides with CSR and CSC.
MIN_RANK = 2

#: anndata's two rank-two spellings, and which axis each one's ``indptr`` walks. A dict rather than
#: two constants because it is also the validation: an ``encoding-type`` outside it is a group this
#: cannot honestly claim to understand.
INDEXED_AXIS: dict[str, int] = {"csr_matrix": 0, "csc_matrix": 1}

#: The three arrays every layout holds, in the order a reader needs them.
ARRAYS = ("data", "indices", "indptr")


class StoreError(ValueError):
    """A sporadik store this server will not register, with the reason a writer can act on."""


def layout_path(indexed_axis: int) -> str:
    """Where the layout compressing ``indexed_axis`` lives inside the prefix.

    Recomputed and compared rather than trusted, because a layout read from the wrong path is
    indexed along the wrong axis and every lookup then returns a real, wrong slice.
    """
    return f"{LAYOUTS_GROUP}/axis{int(indexed_axis)}"


def anndata_encoding(rank: int, indexed_axis: int) -> str:
    """The ``encoding-type`` a layout's own group declares, at a given rank.

    At rank two it is anndata's exactly. Above rank two the child holds the array raveled to two
    axes, which genuinely is a ``csr_matrix``, so that is what it says -- and the block carries the
    real shape and the ravel order.
    """
    return ("csr_matrix" if indexed_axis == 0 else "csc_matrix") if rank == MIN_RANK else "csr_matrix"


def raveled_shape(shape: list[int], indexed_axis: int, index_order: list[int]) -> list[int]:
    """The shape a layout's own group declares, which above rank two is not the array's."""
    if len(shape) == MIN_RANK:
        return list(shape)
    remainder = 1
    for axis in index_order:
        remainder *= int(shape[axis])
    return [int(shape[indexed_axis]), remainder]


@dataclass(frozen=True)
class LayoutReading:
    """What one layout's ``zarr.json`` objects establish about it."""

    path: str
    encoding: str
    encoding_version: str | None
    indexed_axis: int
    index_order: list[int]
    nnz: int
    dtype: str
    chunks: dict[str, int | None]
    range_readable: bool


@dataclass(frozen=True)
class LayoutEntry:
    """One layout as the *block* names it, before the prefix has been looked at.

    Everything here is checkable against the shape alone, which is why it is checked before a single
    object is fetched: an entry naming an axis the array does not have is wrong whatever is behind
    it, and spending four GETs to find that out would be spending them to learn nothing.
    """

    path: str
    indexed_axis: int
    index_order: list[int]


@dataclass(frozen=True)
class StoreReading:
    """What one store's block establishes, before any layout has been looked at.

    Frozen and flat on purpose: this is a *reading*, not a builder. Nothing here has a default,
    because everything here was stated by the writer or the store was refused.
    """

    spec: str
    shape: list[int]
    entries: list[LayoutEntry]


def _loads(body: bytes, *, where: str) -> dict[str, Any]:
    """One ``zarr.json`` body as an object, or the reason it is not one."""
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StoreError(
            f"{where} is not valid JSON ({error}). It is {len(body)} bytes and starts {body[:80]!r} -- a truncated "
            "body is the other shape an interrupted write leaves behind."
        ) from error
    if not isinstance(raw, dict):
        raise StoreError(f"{where} is a JSON object, got {type(raw).__name__}.")
    return raw


def _assert_node(raw: dict[str, Any], kind: str, *, where: str) -> dict[str, Any]:
    """A zarr v3 node of the kind expected, or the reason it is not."""
    if raw.get("zarr_format") == 2:
        raise StoreError(f"{where} is Zarr v2. Only Zarr v3 stores are supported.")
    if raw.get("node_type") != kind:
        raise StoreError(f"{where} declares node_type {raw.get('node_type')!r}; a sporadik store needs a {kind} here.")
    return raw


def parse_root(body: bytes, *, where: str = "the store") -> StoreReading:
    """Read the root ``zarr.json`` into the block a registration acts on.

    Split from the fetch so the rules are testable without a bucket behind them -- the rules are the
    interesting half, and the GET is not.

    **The block is checked before anything else in the prefix is looked at**, and that ordering is
    the point of the format. Everything else in a store declares something *before* it is true: zarr
    writes an array's ``zarr.json`` ahead of its chunks and substitutes the fill value for a chunk it
    cannot fetch. The block is the only statement made *after* the thing it describes exists, so its
    absence is the only reliable evidence that an upload did not finish. Measured, before the format
    had one: deleting every chunk of ``data`` left a store that passed every other check here,
    recorded the right ``nnz``, and returned the right *number* of values for a slice -- all of them
    zero, with nothing raised anywhere.
    """
    raw = _assert_node(_loads(body, where=where), "group", where=where)
    block = (raw.get("attributes") or {}).get(BLOCK_KEY)
    if not isinstance(block, dict):
        raise StoreError(
            f"{where} carries no `{BLOCK_KEY}` block, so it is not a sporadik store -- or it is an upload that did not "
            "finish. The block is written last, after every chunk, which is the only point at which what it describes "
            "is actually there."
        )

    spec = str(block.get("spec", "")).strip()
    if spec not in SUPPORTED_VERSIONS:
        raise StoreError(
            f"{where} declares `{BLOCK_KEY}` spec {spec!r}, which this server cannot read. Supported: "
            f"{', '.join(sorted(SUPPORTED_VERSIONS))}. The version selects how every byte in the prefix is read, so an "
            "unknown one is refused rather than read as though it were familiar."
        )
    if not block.get("complete"):
        raise StoreError(
            f"{where} declares `{BLOCK_KEY}` complete={block.get('complete')!r}. Only a finished store is "
            "registerable; nothing is known about what a half-written one holds."
        )

    shape = block.get("shape")
    if not isinstance(shape, list) or len(shape) < MIN_RANK:
        raise StoreError(
            f"{where} declares shape {shape!r}; a sparse array has at least {MIN_RANK} axes -- a compressed axis needs "
            "at least one other to hold the positions."
        )
    shape = [int(size) for size in shape]

    entries = block.get("layouts")
    if not isinstance(entries, list) or not entries:
        raise StoreError(f"{where} names no layouts, so it holds no array. A store is its layouts -- one per axis a reader might select along.")
    if len(entries) > len(shape):
        raise StoreError(
            f"{where} names {len(entries)} layouts over a rank-{len(shape)} array, but there is one axis to compress "
            "per axis it has. Any more would be a copy of one of the others."
        )
    readings: list[LayoutEntry] = []
    seen: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise StoreError(f"{where} names layout {entry!r}, which is not a layout entry -- each names its `path`, its `indexed_axis` and its `index_order`.")
        reading = _entry(entry, shape, where=where)
        if reading.indexed_axis in seen:
            raise StoreError(
                f"{where} names two layouts compressing axis {reading.indexed_axis} ('{seen[reading.indexed_axis]}' and "
                f"'{reading.path}'). That is one capability twice, and nothing could say which a reader should use."
            )
        seen[reading.indexed_axis] = reading.path
        readings.append(reading)

    return StoreReading(spec=spec, shape=shape, entries=readings)


def _entry(entry: dict[str, Any], shape: list[int], *, where: str) -> LayoutEntry:
    """One block entry, checked against the shape alone -- no objects fetched."""
    path = str(entry.get("path") or "").strip("/")
    indexed_axis = entry.get("indexed_axis")
    if not isinstance(indexed_axis, int) or not 0 <= indexed_axis < len(shape):
        raise StoreError(f"Layout '{path}' declares indexed_axis {indexed_axis!r}, which is not an axis of {shape}.")
    if path != layout_path(indexed_axis):
        raise StoreError(
            f"Layout '{path}' compresses axis {indexed_axis}, so it is filed under the wrong name -- it belongs at "
            f"'{layout_path(indexed_axis)}'. Read from the wrong path it would be indexed along the wrong axis, and "
            "every lookup would return a real, wrong slice."
        )

    others = [axis for axis in range(len(shape)) if axis != indexed_axis]
    index_order = entry.get("index_order")
    if not isinstance(index_order, list) or sorted(index_order) != others:
        raise StoreError(
            f"Layout '{path}' declares index_order {index_order!r}, which is not a permutation of the axes it did not "
            f"compress {others}. That order is how `indices` was raveled and cannot be recovered from the bytes, so a "
            "wrong one does not fail -- it puts every value in a different cell."
        )
    return LayoutEntry(path=path, indexed_axis=indexed_axis, index_order=[int(axis) for axis in index_order])


def parse_layout(entry: LayoutEntry, bodies: dict[str, bytes], shape: list[int], *, where: str = "the store") -> LayoutReading:
    """Check one layout's own objects against the entry the block made for it.

    ``bodies`` carries the layout's four ``zarr.json`` objects: ``""`` for the group itself, and one
    per array in :data:`ARRAYS`. The entry has already been checked against the shape by
    :func:`parse_root`, so what is left here is the half that needs the artifact.

    Every refusal here is silent if skipped. That is the whole reason each exists -- a sparse store
    is a pile of integers, and reading it along the wrong axis or unravelling it in the wrong order
    does not crash, it returns real numbers from the wrong cells.
    """
    path, indexed_axis, index_order = entry.path, entry.indexed_axis, entry.index_order

    group = _assert_node(_loads(bodies[""], where=f"{where}/{path}"), "group", where=f"{where}/{path}")
    attributes = group.get("attributes") or {}

    encoding = attributes.get("encoding-type")
    expected_encoding = anndata_encoding(len(shape), indexed_axis)
    if encoding != expected_encoding:
        raise StoreError(
            f"Layout '{path}' declares encoding-type {encoding!r}, but a layout compressing axis {indexed_axis} of a "
            f"rank-{len(shape)} array is {expected_encoding!r}. The group's own attributes and the block disagree "
            "about what this is."
        )

    declared = [int(size) for size in (attributes.get("shape") or [])]
    expected_shape = raveled_shape(shape, indexed_axis, index_order)
    if declared != expected_shape:
        raise StoreError(
            f"Layout '{path}' declares shape {declared}, but compressing axis {indexed_axis} of {shape} gives "
            f"{expected_shape}. A store is one array in up to one layout per axis, so every layout has to be that array."
        )

    arrays = {
        name: _assert_node(_loads(bodies[name], where=f"{where}/{path}/{name}"), "array", where=f"{where}/{path}/{name}")
        for name in ARRAYS
    }

    nnz = int(arrays["data"]["shape"][0])
    if int(arrays["indices"]["shape"][0]) != nnz:
        raise StoreError(
            f"Layout '{path}' has {nnz} values and {arrays['indices']['shape'][0]} indices. They are parallel arrays, "
            "so an upload that wrote one and not the other stopped partway."
        )
    expected = shape[indexed_axis] + 1
    if int(arrays["indptr"]["shape"][0]) != expected:
        raise StoreError(
            f"Layout '{path}' compresses axis {indexed_axis} of {shape}, so `indptr` holds {expected} entries -- one "
            f"per slice, plus the end -- but it holds {arrays['indptr']['shape'][0]}. The declaration and the arrays "
            "disagree about what this array is."
        )

    return LayoutReading(
        path=path,
        encoding=str(encoding),
        encoding_version=attributes.get("encoding-version"),
        indexed_axis=indexed_axis,
        index_order=index_order,
        nnz=nnz,
        dtype=str(arrays["data"].get("data_type")),
        chunks={name: _chunk_of(meta) for name, meta in arrays.items()},
        range_readable=all(_byte_addressable(meta) for meta in arrays.values()),
    )


def _chunk_of(meta: dict[str, Any]) -> int | None:
    """The chunk length of a 1-D array, or ``None`` if it declares none."""
    configuration = (meta.get("chunk_grid") or {}).get("configuration") or {}
    shape = configuration.get("chunk_shape")
    return int(shape[0]) if shape else None


def _byte_addressable(meta: dict[str, Any]) -> bool:
    """Whether this array's stored object is the raw buffer, so a byte range reads elements.

    One chunk and no compressor, read off the codecs rather than taken from anyone's word. Both
    halves matter: a compressor makes an offset meaningless, and more than one chunk means the
    offset is into a chunk rather than into the array.
    """
    codecs = [codec.get("name") for codec in (meta.get("codecs") or [])]
    return codecs == ["bytes"] and _chunk_of(meta) == int((meta.get("shape") or [0])[0])

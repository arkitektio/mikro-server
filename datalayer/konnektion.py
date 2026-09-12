"""Reading a konnektion collection's ``konnektion.json`` without depending on konnektion.

`konnektion` is the library that *writes* this format. This module reads the one file that says
what was written, and it does so with `json` and nothing else -- the same relationship this
datalayer has with zarr and with fabriks. The reasons are the same three:

**A server that imports a writer inherits its dependencies.** konnektion needs pyarrow and numpy
to do its job. None of that is required to read one small JSON object, and all of it would have
to be installed, pinned and upgraded in a service whose entire interest in a collection is "is
this registerable, and what does it declare".

**A version skew becomes an outage.** If registration went through the writer's parser, a
konnektion release that changed its dataclasses could stop a running server reading collections
it had already accepted. Parsing the wire format directly means the *format* is the contract,
and the writer and the server are two independent implementations of it -- which is also the
only arrangement in which "the format is specified" is a testable claim rather than a shared
object file.

**The reader is not the writer's mirror.** konnektion's ``Manifest`` exists to be
*round-tripped*: it carries defaults, constructors and the vocabulary a writer needs to make
choices. What a server needs is narrower and stricter -- did this thing declare a format I can
read, and does what it declared hang together.

**What this module deliberately does not do:** open a Parquet file. Everything here comes from
one GET of one small object.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

#: The format versions this server can read. A version selects how every byte in the prefix is
#: interpreted, so an unknown one is refused rather than accepted and read as if it were
#: familiar.
#:
#: Kept in step with ``konnektion.manifest.SPEC_VERSION`` by the contract, not by an import. A
#: bump on either side is made on both, in the same change, or the store becomes unreadable.
SUPPORTED_VERSIONS = frozenset({"1"})

#: The manifest's name, at the root of the collection's prefix.
MANIFEST_NAME = "konnektion.json"

#: Where the format puts the two catalogs when a manifest does not say otherwise.
CELL_CATALOG_PATH = "catalog/cells.parquet"
OBJECT_CATALOG_PATH = "catalog/objects.parquet"

#: The vocabulary, mirroring the format's. A value outside it is refused: every one of these is
#: a claim a decoder acts on, and there is no safe way to read a word nobody defined.
#:
#: ``edges`` is the one whose absence would be least survivable, and the reason this server
#: refuses a manifest that omits it rather than defaulting it. Arity is the only thing
#: separating this blob from a mesh's index buffer, and reading a segment list at arity three
#: raises nothing anywhere: the length divides whenever the edge count is a multiple of three,
#: every index is in range, and the picture is a plausible, wrong graph.
#:
#: ``pruning`` and ``simplification`` name what a writer actually did rather than offering one
#: name that is sometimes a lie -- a collection that coarsened nothing declares ``NONE`` for
#: both, which is the common case for traced data and is checkable precisely because it is
#: stated.
ENCODING_VOCABULARY: dict[str, frozenset[str]] = {
    "positions": frozenset({"UINT16_QUANTIZED_PER_CELL"}),
    "edges": frozenset({"UINT32_PAIRS"}),
    "nodeIds": frozenset({"UINT32", "UINT64"}),
    "radii": frozenset({"NONE", "FLOAT32", "UINT16_QUANTIZED_PER_CELL"}),
    "ghosts": frozenset({"TRAILING_PER_OWNER_CELL"}),
    "codec": frozenset({"NONE"}),
    "compression": frozenset({"NONE", "ZSTD"}),
    "pruning": frozenset({"NONE", "STRAHLER", "CUSTOM"}),
    "simplification": frozenset({"NONE", "DOUGLAS_PEUCKER", "CUSTOM"}),
}

#: The keys a decoder cannot work without, so none of them is ever defaulted on a writer's
#: behalf. ``codec`` and ``compression`` are the load-bearing pair a mesh store also has: a
#: wrong guess is not an error anywhere, it is geometry that decodes to garbage. ``edges`` joins
#: them here for the reason given above.
#:
#: There is deliberately **no** ``boundary`` key, and its absence is the format's decision rather
#: than an omission this server should paper over. fabriks declares ``boundary: LOCKED`` -- a
#: vertex on a cell face plane did not move -- which is what lets a fine cell be drawn beside a
#: coarse one without a crack. A format that prunes whole branches cannot make that promise: a
#: branch present at level 0 may be absent at level 1 entirely. So konnektion does not claim it.
REQUIRED_ENCODING_KEYS = (
    "positions",
    "edges",
    "nodeIds",
    "radii",
    "ghosts",
    "codec",
    "compression",
    "pruning",
    "simplification",
)

#: The vocabulary of the top-level ``attributes`` key: per-node value columns riding beside the
#: geometry, declared so this server can publish the names a picker may colour by without ever
#: opening a Parquet file. The key sits *beside* ``encoding`` in the format, deliberately -- the
#: nine encoding keys are required-never-defaulted, so a tenth would have made every existing
#: collection unreadable for a backwards-compatible addition. Absent means none, which is the
#: normal state of a manifest written before attributes existed.
ATTRIBUTE_ENCODINGS = frozenset({"FLOAT32"})

#: What a computed column means. ``null`` is a writer-supplied column the format makes no claim
#: about; the named ones are the metrics konnektion computes itself, once, on the full level-0
#: graph -- which is what makes them safe to colour by at any level.
ATTRIBUTE_SEMANTICS = frozenset({"STRAHLER", "DEGREE", "DEPTH", "COMPONENT"})

#: Mirrors the format's name rule: the name becomes a pair of Parquet columns and a picker
#: value, both places where case and punctuation are trouble nobody needs.
ATTRIBUTE_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,63}\Z")

_SORT_KEYS = frozenset({"MORTON"})
_CELL_SIZE_RANK = 3
_AXIS_RANK = 3


class ManifestError(ValueError):
    """A manifest this server will not register, with the reason a writer can act on."""


@dataclass(frozen=True)
class Manifest:
    """What one GET of ``konnektion.json`` establishes about a collection.

    Frozen and flat on purpose: this is a *reading*, not a builder. Nothing here has a default,
    because everything here was either stated by the writer or is absent, and those are
    different facts.
    """

    spec_version: str
    grid: dict[str, Any]
    encoding: dict[str, Any]
    axes: list[str] | None
    #: The per-node value columns the geometry carries, each ``{name, encoding, semantics}``.
    #: Empty for a manifest that declares none or predates the key -- the same fact either way:
    #: there is nothing to colour by.
    attributes: list[dict[str, Any]]
    counts: dict[str, Any]
    files: dict[str, Any]

    @property
    def levels(self) -> int:
        """How many octree levels the collection declares.

        **One is normal here, not degenerate.** A traced arbor of a few thousand nodes has
        nothing to gain from a ladder, so konnektion chooses depth from the data and a
        single-level collection is the common case. A server that treated ``levels == 1`` as
        suspicious would reject most of what it is given.
        """
        return int(self.grid["levels"])

    @property
    def cell_size(self) -> list[int]:
        """The level-0 cell, one size per component, in the node position component order."""
        return [int(component) for component in self.grid["cellSize"]]

    @property
    def coarsens(self) -> bool:
        """Whether this collection claims any level is a reduction of another."""
        return self.encoding.get("pruning") != "NONE" or self.encoding.get("simplification") != "NONE"

    def level_paths(self, level: int) -> list[str] | None:
        """The parts the manifest names for one level, or ``None`` if it names none.

        ``None`` rather than an empty list, because "this manifest does not say" and "this level
        has no parts" send a caller to different places -- the first to the store's listing, the
        second nowhere. A manifest's file list is a **claim**: it is a convenience for a reader
        that would otherwise list the prefix, never authority over what is actually there.
        """
        declared = self.files.get("levels")
        if not isinstance(declared, dict):
            return None
        entries = declared.get(str(int(level)))
        if not isinstance(entries, list) or not entries:
            return None
        return [_entry_path(entry) for entry in entries]

    def catalog_path(self, role: str, fallback: str) -> str:
        """Where the manifest says a catalog is, or where the format puts it."""
        declared = self.files.get(role)
        return fallback if declared is None else _entry_path(declared)

    @property
    def cells_path(self) -> str:
        """The cell catalog: the spatial index a frustum query reads."""
        return self.catalog_path("cells", CELL_CATALOG_PATH)

    @property
    def objects_path(self) -> str:
        """The object catalog: the inverted index from an object to its cells."""
        return self.catalog_path("objects", OBJECT_CATALOG_PATH)


def _entry_path(raw: Any) -> str:
    """One file entry's path, accepting the bare string a hand-written manifest may use.

    A konnektion-written entry is an object carrying ``bytes`` and ``rowGroups`` alongside the
    path, so a reader can range-read a part without being able to stat it. The server needs only
    the path -- it addresses files, it does not seek inside them -- but it must not choke on the
    richer form.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("path"), str):
        return str(raw["path"])
    raise ManifestError(f"A file entry in a manifest is a path, or an object carrying one, got {raw!r}.")


def _assert_grid(grid: Any) -> dict[str, Any]:
    """Refuse a grid a reader could not address cells in."""
    if not isinstance(grid, dict):
        raise ManifestError(f"A manifest's `grid` is an object, got {type(grid).__name__}.")

    cell_size = grid.get("cellSize")
    if not isinstance(cell_size, list) or len(cell_size) != _CELL_SIZE_RANK:
        raise ManifestError(
            f"`grid.cellSize` is the level-0 cell in voxels, one size per component, so it takes exactly {_CELL_SIZE_RANK} values, got {cell_size!r}."
        )
    if any(not isinstance(component, int) or isinstance(component, bool) or component < 1 for component in cell_size):
        raise ManifestError(f"`grid.cellSize` counts voxels, so every component is a whole number of at least 1, got {cell_size!r}.")

    levels = grid.get("levels")
    if not isinstance(levels, int) or isinstance(levels, bool) or levels < 1:
        raise ManifestError(
            f"`grid.levels` is how many octree levels this collection stores, so it is a whole number of at least 1, got {levels!r}."
        )

    sort_key = grid.get("sortKey", "MORTON")
    if sort_key not in _SORT_KEYS:
        raise ManifestError(f"`grid.sortKey` is {sort_key!r}, which the format does not define. It is one of: {', '.join(sorted(_SORT_KEYS))}.")

    return {"cellSize": [int(component) for component in cell_size], "levels": int(levels), "sortKey": str(sort_key)}


def _assert_encoding(encoding: Any) -> dict[str, Any]:
    """Refuse an encoding that leaves a decoder guessing, or names a word nobody defined."""
    if not isinstance(encoding, dict):
        raise ManifestError(f"A manifest's `encoding` is an object, got {type(encoding).__name__}.")

    missing = [key for key in REQUIRED_ENCODING_KEYS if key not in encoding]
    if missing:
        raise ManifestError(
            f"This manifest's `encoding` omits {', '.join(missing)}. A decoder cannot infer them -- a wrong guess is not an error, it is geometry that decodes to garbage -- so the collection is refused."
        )

    for key in REQUIRED_ENCODING_KEYS:
        allowed = ENCODING_VOCABULARY[key]
        if encoding[key] not in allowed:
            raise ManifestError(f"`encoding.{key}` is {encoding[key]!r}, which the format does not define. It is one of: {', '.join(sorted(allowed))}.")

    return {key: str(encoding[key]) for key in REQUIRED_ENCODING_KEYS}


def _assert_attributes(attributes: Any) -> list[dict[str, Any]]:
    """Check the declared per-node attributes, and accept their absence.

    **Absent is legitimate** -- it is every manifest written before the key existed, and a
    collection that simply carries no attributes. Present but malformed is refused: these names
    are what a layer's picker will be validated against, so a declaration nobody could act on is
    worse than none.
    """
    if attributes is None:
        return []
    if not isinstance(attributes, list):
        raise ManifestError(f"A manifest's `attributes` is a list of declarations, got {type(attributes).__name__}.")

    parsed: list[dict[str, Any]] = []
    for entry in attributes:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ManifestError(f"An entry in `attributes` is an object naming an attribute, got {entry!r}.")
        name = entry["name"]
        if not ATTRIBUTE_NAME_PATTERN.match(name):
            raise ManifestError(
                f"Attribute {name!r}: a name is lowercase letters, digits and underscores, starting with a letter or underscore, at most 64 characters."
            )
        if name == "radius":
            raise ManifestError(
                "A manifest may not declare an attribute named 'radius': a per-node radius travels in `encoding.radii`, and an attribute of that name would shadow it."
            )
        encoding = entry.get("encoding", "FLOAT32")
        if encoding not in ATTRIBUTE_ENCODINGS:
            raise ManifestError(
                f"Attribute {name!r} declares encoding {encoding!r}, which the format does not define. It is one of: {', '.join(sorted(ATTRIBUTE_ENCODINGS))}."
            )
        semantics = entry.get("semantics")
        if semantics is not None and semantics not in ATTRIBUTE_SEMANTICS:
            raise ManifestError(
                f"Attribute {name!r} declares semantics {semantics!r}, which the format does not define. It is one of: {', '.join(sorted(ATTRIBUTE_SEMANTICS))}, or null for a writer-supplied column."
            )
        parsed.append({"name": name, "encoding": str(encoding), "semantics": None if semantics is None else str(semantics)})

    names = [entry["name"] for entry in parsed]
    if len(set(names)) != len(names):
        repeated = sorted({name for name in names if names.count(name) > 1})
        raise ManifestError(f"A manifest declares each attribute once; {', '.join(repeated)} appear(s) twice.")
    return parsed


def _assert_axes(axes: Any) -> list[str] | None:
    """Check a declared axis order, and accept its absence.

    **Absent is legitimate.** Nothing in the format is decoded through `axes`: node position
    components, `cellSize`, the bbox columns and the Morton interleave are all positional and
    self-consistent without any names. Naming those axes is a question about the collection's
    relationship to *something else* -- the image it was traced out of, the coordinate graph it
    is placed in -- which is a layer above the bytes.

    So a writer that knows the answer states it and the server can check the two agree; one that
    does not, omits it, and omitting is better than a plausible guess. Present but malformed is
    refused, because that is a layer above stating something it got wrong.
    """
    if axes is None:
        return None
    if isinstance(axes, str) or not isinstance(axes, list) or len(axes) != _AXIS_RANK:
        raise ManifestError(f"A network collection is three-dimensional, so `axes` names {_AXIS_RANK} axes, got {axes!r}.")
    names = [str(axis) for axis in axes]
    if len(set(names)) != _AXIS_RANK:
        raise ManifestError(f"`axes` names each axis once, got {names!r}.")
    return names


def parse_manifest(body: bytes, *, where: str = "the manifest") -> Manifest:
    """Read ``konnektion.json``'s bytes into the facts a registration acts on.

    Split from the fetch so the rules are testable without a bucket behind them -- the rules are
    the interesting half, and the GET is not.
    """
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(
            f"{where} is not valid JSON ({error}). It is {len(body)} bytes and starts {body[:80]!r} -- a truncated body is the other shape an interrupted write leaves behind."
        ) from error

    if not isinstance(raw, dict):
        raise ManifestError(f"{where} is a JSON object, got {type(raw).__name__}.")

    version = str(raw.get("specVersion", "")).strip()
    if version not in SUPPORTED_VERSIONS:
        raise ManifestError(
            f"{where} declares specVersion {version!r}, which this server cannot read. Supported: {', '.join(sorted(SUPPORTED_VERSIONS))}. The version selects how every byte in the prefix is read, so an unknown one is refused rather than read as though it were familiar."
        )

    if "grid" not in raw or "encoding" not in raw:
        raise ManifestError(
            f"{where} must carry a `grid` and an `encoding` object: they are how a reader turns a Morton code into a box and the blobs into nodes and edges, and nothing else in the store states them."
        )

    counts = raw.get("counts") or {}
    files = raw.get("files") or {}
    return Manifest(
        spec_version=version,
        grid=_assert_grid(raw["grid"]),
        encoding=_assert_encoding(raw["encoding"]),
        axes=_assert_axes(raw.get("axes")),
        attributes=_assert_attributes(raw.get("attributes")),
        counts=dict(counts) if isinstance(counts, dict) else {},
        files=dict(files) if isinstance(files, dict) else {},
    )

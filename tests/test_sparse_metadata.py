"""What a sparse store states about itself, read back off the artifact.

`createSparseDataset` declares no spec, no shape, no encoding and no chunking -- all of them are
read here, when the upload is finished, because a fact derived from the artifact cannot be
declared wrong. Which makes this function the one place those facts can be got wrong, and every
branch in it a refusal that is otherwise silent much later, in a reader.

`tests/test_sparse_datasets.py` patches `fill_info` away -- correctly, since a prefix-backed store
does real S3 work where `ParquetStore.fill_info` does none -- so nothing there reaches this code
at all. That gap is not hypothetical: `get_sparse_metadata` referred to `models` without the
deferred import its neighbours all have, and the first bytes ever to reach it raised `NameError`
against a live server. The stub below is only `get_object`; everything else is the real parse.

Two cases carry most of the weight.

:func:`test_a_store_with_no_block_is_refused` is the one the format exists for. zarr writes an
array's `zarr.json` **before** its chunks and substitutes the fill value for a chunk it cannot
fetch, so a torn upload leaves a tree whose every declaration is intact and whose values are
silently zero. The root block is written last, in one object, after every chunk; its absence is
the only reliable evidence that an upload did not finish.

:func:`test_a_layout_filed_under_the_wrong_name_is_refused` is the other. A layout read from the
wrong path is indexed along the wrong axis, and every lookup then returns a real, wrong slice.

**Two axes is one case, not the definition** -- the rank-three cases below are not exotica, they
are the same code path with more axes, and they are here because rank two is the one rank where
the format's own generalisations happen to be invisible.
"""

import io
import json
import pathlib

import pytest

from datalayer.datalayer import Datalayer
from datalayer import models
from datalayer.models import sparse_layout_path

CHUNK = 32_768


def _array(nelements: int, dtype: str = "float32", chunk: int = CHUNK, compressed: bool = True) -> dict:
    """A zarr v3 array's `zarr.json`, cut down to the keys this reads."""
    codecs = [{"name": "bytes"}] + ([{"name": "zstd"}] if compressed else [])
    return {
        "zarr_format": 3,
        "node_type": "array",
        "shape": [nelements],
        "data_type": dtype,
        "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [chunk]}},
        "codecs": codecs,
    }


def _layout_group(encoding: str, shape: tuple[int, ...]) -> dict:
    """One layout's own `zarr.json`, in the spelling anndata writes."""
    return {
        "zarr_format": 3,
        "node_type": "group",
        "attributes": {"encoding-type": encoding, "encoding-version": "0.1.0", "shape": list(shape)},
    }


def _root(block: dict | None) -> dict:
    """The store root's `zarr.json`, carrying the block the writer lands last."""
    return {
        "zarr_format": 3,
        "node_type": "group",
        "attributes": ({"sporadik": block} if block is not None else {}),
    }


class _Store:
    """Enough of a `SparseStore` for the read: it is addressed by its path."""

    path = "s3://zarr/some-prefix"
    key = "some-prefix"


class _S3:
    """Serves the fabricated `zarr.json` bodies, and 404s anything not written."""

    def __init__(self, tree: dict[str, dict]) -> None:
        self.tree = tree

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 -- boto3's spelling
        assert Bucket == "zarr"
        suffix = Key[len("some-prefix/") :]
        if suffix not in self.tree:
            raise FileNotFoundError(Key)
        return {"Body": io.BytesIO(json.dumps(self.tree[suffix]).encode())}


def _layer(tree: dict[str, dict]) -> Datalayer:
    layer = Datalayer()
    layer._s3 = _S3(tree)
    return layer


def _encoding_for(shape: tuple[int, ...], axis: int) -> str:
    """anndata's name at rank two; the raveled csr above it."""
    if len(shape) == 2:
        return "csr_matrix" if axis == 0 else "csc_matrix"
    return "csr_matrix"


def _declared_shape(shape: tuple[int, ...], axis: int, order: list[int]) -> list[int]:
    """What a layout's own group declares: the array's shape, or its raveled pair."""
    if len(shape) == 2:
        return list(shape)
    remainder = 1
    for other in order:
        remainder *= shape[other]
    return [shape[axis], remainder]


def _tree(
    shape: tuple[int, ...] = (4, 3),
    axes: tuple[int, ...] = (0,),
    nnz: int = 5,
    *,
    spec: str = "1",
    complete: bool = True,
    block: dict | None = None,
    compressed: bool = True,
) -> dict:
    """A complete, consistent sparse store -- the baseline each refusal below breaks one way."""
    entries = []
    tree: dict[str, dict] = {}
    for axis in axes:
        order = [other for other in range(len(shape)) if other != axis]
        path = sparse_layout_path(axis)
        entries.append({"path": path, "indexed_axis": axis, "index_order": order})
        tree[f"{path}/zarr.json"] = _layout_group(_encoding_for(shape, axis), tuple(_declared_shape(shape, axis, order)))
        tree[f"{path}/data/zarr.json"] = _array(nnz, compressed=compressed)
        tree[f"{path}/indices/zarr.json"] = _array(nnz, dtype="int32", compressed=compressed)
        tree[f"{path}/indptr/zarr.json"] = _array(shape[axis] + 1, dtype="int32", compressed=compressed)

    tree["zarr.json"] = _root(
        block if block is not None else {"spec": spec, "complete": complete, "shape": list(shape), "layouts": entries}
    )
    return tree


# --------------------------------------------------------------------------- #
# The happy paths
# --------------------------------------------------------------------------- #
def test_a_complete_store_reads_back_what_it_states() -> None:
    """The happy path, and the one that would have caught the missing import."""
    metadata = _layer(_tree()).get_sparse_metadata(_Store())

    assert metadata.spec == "1"
    assert metadata.shape == [4, 3]
    assert len(metadata.layouts) == 1

    layout = metadata.layouts[0]
    assert layout.path == "layouts/axis0"
    assert layout.encoding == "csr_matrix"
    assert layout.encoding_version == "0.1.0"
    assert layout.indexed_axis == 0
    assert layout.index_order == [1]
    assert layout.nnz == 5
    assert layout.dtype == "float32"
    assert layout.chunks == {"data": CHUNK, "indices": CHUNK, "indptr": CHUNK}
    assert layout.range_readable is False


def test_both_layouts_of_one_matrix_are_one_store() -> None:
    """One matrix is one upload, so the two capabilities arrive together or not at all."""
    metadata = _layer(_tree(axes=(0, 1))).get_sparse_metadata(_Store())

    assert [layout.indexed_axis for layout in metadata.layouts] == [0, 1]
    assert [layout.encoding for layout in metadata.layouts] == ["csr_matrix", "csc_matrix"]
    # `indptr` walks the compressed axis, so its length is what tells the two apart.
    assert [layout.chunks["indptr"] for layout in metadata.layouts] == [CHUNK, CHUNK]


def test_an_uncompressed_single_chunk_store_is_range_readable() -> None:
    """Derived off the codecs, never declared: one chunk and no compressor, or it is not."""
    tree = _tree(nnz=CHUNK, compressed=False)
    for name in ("data", "indices"):
        tree[f"layouts/axis0/{name}/zarr.json"]["chunk_grid"]["configuration"]["chunk_shape"] = [CHUNK]
    tree["layouts/axis0/indptr/zarr.json"]["chunk_grid"]["configuration"]["chunk_shape"] = [5]

    assert _layer(tree).get_sparse_metadata(_Store()).layouts[0].range_readable is True


def test_a_compressed_single_chunk_store_is_not_range_readable() -> None:
    """Both halves matter: a compressor makes a byte offset meaningless on its own."""
    tree = _tree(nnz=CHUNK, compressed=True)
    tree["layouts/axis0/indptr/zarr.json"]["chunk_grid"]["configuration"]["chunk_shape"] = [5]

    assert _layer(tree).get_sparse_metadata(_Store()).layouts[0].range_readable is False


# --------------------------------------------------------------------------- #
# The block -- what makes an interrupted upload detectable at all
# --------------------------------------------------------------------------- #
def test_a_store_with_no_block_is_refused() -> None:
    """The case the format exists for.

    Without the block this store passes every other check here: the encodings are declared, the
    shapes agree, `indptr` is the right length. It would register, report the right `nnz`, and
    hand a reader back the right *number* of values for a slice, every one of them zero -- because
    zarr writes an array's metadata ahead of its chunks and fills a missing chunk rather than
    failing. Nothing else in this file can tell that store from a good one.
    """
    tree = _tree()
    tree["zarr.json"] = _root(None)

    with pytest.raises(ValueError, match="did not finish"):
        _layer(tree).get_sparse_metadata(_Store())


def test_an_unknown_spec_is_refused_rather_than_guessed_at() -> None:
    """A spec selects how every byte in the prefix is read, so reading one blind is not cautious."""
    with pytest.raises(ValueError, match="spec '2'"):
        _layer(_tree(spec="2")).get_sparse_metadata(_Store())


def test_an_incomplete_block_is_refused() -> None:
    """A writer that got as far as saying so, and no further."""
    with pytest.raises(ValueError, match="complete=False"):
        _layer(_tree(complete=False)).get_sparse_metadata(_Store())


def test_a_block_naming_no_layouts_is_refused() -> None:
    """A store is its layouts."""
    tree = _tree()
    tree["zarr.json"] = _root({"spec": "1", "complete": True, "shape": [4, 3], "layouts": []})

    with pytest.raises(ValueError, match="names no layouts"):
        _layer(tree).get_sparse_metadata(_Store())


def test_a_layout_named_but_absent_is_refused() -> None:
    """The block lists what the writer finished, so a name with nothing behind it is a torn upload."""
    tree = _tree()
    del tree["layouts/axis0/zarr.json"]

    with pytest.raises(FileNotFoundError, match="not a readable sporadik store"):
        _layer(tree).get_sparse_metadata(_Store())


def test_more_layouts_than_axes_is_refused() -> None:
    """One axis to compress per axis the array has; any more would be a copy of another."""
    tree = _tree(axes=(0, 1))
    block = json.loads(json.dumps(tree["zarr.json"]["attributes"]["sporadik"]))
    block["layouts"].append(dict(block["layouts"][0]))
    tree["zarr.json"] = _root(block)

    with pytest.raises(ValueError, match="rank-2 array"):
        _layer(tree).get_sparse_metadata(_Store())


def test_two_layouts_compressing_one_axis_are_refused() -> None:
    """One capability twice, with nothing to say which a reader should use."""
    tree = _tree(shape=(4, 3, 2), axes=(0, 1, 2))
    block = json.loads(json.dumps(tree["zarr.json"]["attributes"]["sporadik"]))
    block["layouts"][2] = dict(block["layouts"][0])
    tree["zarr.json"] = _root(block)

    with pytest.raises(ValueError, match="two layouts compressing axis 0"):
        _layer(tree).get_sparse_metadata(_Store())


# --------------------------------------------------------------------------- #
# Each layout against the store around it
# --------------------------------------------------------------------------- #
def test_a_layout_filed_under_the_wrong_name_is_refused() -> None:
    """Read from the wrong path it is indexed along the wrong axis -- a real, wrong slice."""
    tree = _tree(axes=(1,))
    for suffix in ("zarr.json", "data/zarr.json", "indices/zarr.json", "indptr/zarr.json"):
        tree[f"layouts/axis0/{suffix}"] = tree.pop(f"layouts/axis1/{suffix}")
    block = {"spec": "1", "complete": True, "shape": [4, 3], "layouts": [{"path": "layouts/axis0", "indexed_axis": 1, "index_order": [0]}]}
    tree["zarr.json"] = _root(block)

    with pytest.raises(ValueError, match="filed under the wrong name"):
        _layer(tree).get_sparse_metadata(_Store())


def test_a_layout_whose_encoding_contradicts_its_axis_is_refused() -> None:
    """The group's own attributes and the block are two statements of one fact."""
    tree = _tree(axes=(0,))
    tree["layouts/axis0/zarr.json"]["attributes"]["encoding-type"] = "csc_matrix"

    with pytest.raises(ValueError, match="is 'csr_matrix'"):
        _layer(tree).get_sparse_metadata(_Store())


def test_an_unknown_encoding_is_refused() -> None:
    """A word nobody defined is not something to read past."""
    tree = _tree()
    tree["layouts/axis0/zarr.json"]["attributes"]["encoding-type"] = "coo_matrix"

    with pytest.raises(ValueError, match="encoding-type 'coo_matrix'"):
        _layer(tree).get_sparse_metadata(_Store())


def test_a_layout_shape_that_disagrees_with_the_store_is_refused() -> None:
    """The check two separately-registered stores used to need, made once against the bytes."""
    tree = _tree()
    tree["layouts/axis0/zarr.json"]["attributes"]["shape"] = [5, 3]

    with pytest.raises(ValueError, match="declares shape"):
        _layer(tree).get_sparse_metadata(_Store())


def test_a_shape_of_one_axis_is_refused() -> None:
    """A compressed axis needs at least one other to hold the positions."""
    tree = _tree()
    tree["zarr.json"] = _root({"spec": "1", "complete": True, "shape": [4], "layouts": [{"path": "layouts/axis0", "indexed_axis": 0, "index_order": []}]})

    with pytest.raises(ValueError, match="at least 2 axes"):
        _layer(tree).get_sparse_metadata(_Store())


def test_a_missing_array_is_refused() -> None:
    """A layout holds all three; one of them absent is an upload that stopped partway."""
    tree = _tree()
    del tree["layouts/axis0/indices/zarr.json"]

    with pytest.raises(FileNotFoundError, match="not a readable sporadik store"):
        _layer(tree).get_sparse_metadata(_Store())


def test_values_and_indices_of_different_lengths_are_refused() -> None:
    """They are parallel arrays, so an upload that wrote one and not the other stopped partway."""
    tree = _tree()
    tree["layouts/axis0/indices/zarr.json"]["shape"] = [4]

    with pytest.raises(ValueError, match="parallel arrays"):
        _layer(tree).get_sparse_metadata(_Store())


def test_an_indptr_that_disagrees_with_the_shape_is_refused() -> None:
    """`len(indptr) == shape[indexed_axis] + 1` is the spine of the format at every rank."""
    tree = _tree()
    tree["layouts/axis0/indptr/zarr.json"]["shape"] = [9]

    with pytest.raises(ValueError, match="holds 5 entries"):
        _layer(tree).get_sparse_metadata(_Store())


def test_the_indptr_check_follows_the_compressed_axis_rather_than_the_first_one() -> None:
    """The check that would pass by accident on a square matrix, so it is asserted on 4x3."""
    tree = _tree(axes=(1,))
    tree["layouts/axis1/indptr/zarr.json"]["shape"] = [5]  # 4 + 1: right for axis 0, wrong for axis 1

    with pytest.raises(ValueError, match="holds 4 entries"):
        _layer(tree).get_sparse_metadata(_Store())


# --------------------------------------------------------------------------- #
# Rank three -- the same code path, with the generalisations visible
# --------------------------------------------------------------------------- #
def test_a_rank_three_store_reads_back_every_layout() -> None:
    """A layout is one axis made contiguous, so an array of rank n has up to n of them."""
    metadata = _layer(_tree(shape=(4, 3, 2), axes=(0, 1, 2))).get_sparse_metadata(_Store())

    assert metadata.shape == [4, 3, 2]
    assert [layout.indexed_axis for layout in metadata.layouts] == [0, 1, 2]
    assert [layout.index_order for layout in metadata.layouts] == [[1, 2], [0, 2], [0, 1]]
    # Above rank two the child holds the raveled two-axis view, which really is a csr_matrix.
    assert {layout.encoding for layout in metadata.layouts} == {"csr_matrix"}


def test_a_rank_three_layout_declares_its_raveled_shape() -> None:
    """Not a lie about the data: what the group holds is that two-axis matrix."""
    tree = _tree(shape=(4, 3, 2), axes=(1,))
    assert tree["layouts/axis1/zarr.json"]["attributes"]["shape"] == [3, 8]

    tree["layouts/axis1/zarr.json"]["attributes"]["shape"] = [3, 4]
    with pytest.raises(ValueError, match=r"gives \[3, 8\]"):
        _layer(tree).get_sparse_metadata(_Store())


def test_an_index_order_that_is_not_a_permutation_is_refused() -> None:
    """The one fact in the format that cannot be recovered from the bytes.

    A wrong order does not fail anywhere -- it reads a different cell -- which is exactly why the
    writer states it and this refuses one that cannot be right.
    """
    tree = _tree(shape=(4, 3, 2), axes=(0,))
    block = {"spec": "1", "complete": True, "shape": [4, 3, 2], "layouts": [{"path": "layouts/axis0", "indexed_axis": 0, "index_order": [1, 1]}]}
    tree["zarr.json"] = _root(block)

    with pytest.raises(ValueError, match="not a permutation"):
        _layer(tree).get_sparse_metadata(_Store())


def test_an_indexed_axis_outside_the_shape_is_refused() -> None:
    """Naming an axis the array does not have."""
    tree = _tree()
    tree["zarr.json"] = _root({"spec": "1", "complete": True, "shape": [4, 3], "layouts": [{"path": "layouts/axis7", "indexed_axis": 7, "index_order": [0]}]})

    with pytest.raises(ValueError, match="not an axis of"):
        _layer(tree).get_sparse_metadata(_Store())


# --------------------------------------------------------------------------- #
# The specification this reader implements, and does not import
# --------------------------------------------------------------------------- #
def test_the_block_key_is_the_formats_own_name() -> None:
    """`sporadik`, not a name borrowed from a consumer.

    It was `mikro-sparse` while the format lived inside this project. A format that names one of
    its consumers in its own bytes is one a second consumer cannot honestly use, and the rename is
    the visible half of moving the spec out to a package of its own.
    """
    assert models.SPARSE_BLOCK_KEY == "sporadik"


def test_this_server_documents_where_the_specification_lives() -> None:
    """The pointer in `docs/` has to keep pointing.

    There is deliberately **no** normative content in this repository: the format is specified in
    the `sporadik` package, and this server reimplements a reader from it rather than importing it
    -- a reader that imports its writer inherits the writer's dependencies and its release cycle.
    Two documents saying the same thing drift, and the one that drifts silently is the one nobody
    runs, so what is checked here is only that the pointer still names the package.
    """
    document = pathlib.Path(__file__).resolve().parents[1] / "docs" / "sparse-store-format.md"
    assert document.exists(), "the pointer to the specification is part of this reader's contract"

    text = document.read_text()
    assert "sporadik" in text, "the document no longer names the package the format is specified in"
    assert "does not import" in text or "reimplements" in text, "it should say why this server has its own copy"


def test_this_readers_constants_are_the_ones_the_specification_states() -> None:
    """The drift guard for an implementation that is *deliberately* not importable from its writer.

    `sporadik` is never imported here -- that independence is the whole arrangement -- which means
    nothing mechanical stops this copy of the format's names from wandering. So the values are
    written out literally, from the specification, and compared. If the format changes, this fails
    and someone reads the spec; if this wanders, it fails and someone reads the spec. Either way the
    document is what settles it.
    """
    from datalayer import sporadik

    assert sporadik.BLOCK_KEY == "sporadik"
    assert sporadik.LAYOUTS_GROUP == "layouts"
    assert sporadik.MIN_RANK == 2
    assert sporadik.SUPPORTED_VERSIONS == frozenset({"1"})
    assert sporadik.INDEXED_AXIS == {"csr_matrix": 0, "csc_matrix": 1}
    assert sporadik.ARRAYS == ("data", "indices", "indptr")

    assert sporadik.layout_path(0) == "layouts/axis0"
    assert sporadik.layout_path(2) == "layouts/axis2"

    # anndata's two spellings at rank two, and the raveled csr above it.
    assert sporadik.anndata_encoding(2, 0) == "csr_matrix"
    assert sporadik.anndata_encoding(2, 1) == "csc_matrix"
    assert sporadik.anndata_encoding(3, 1) == "csr_matrix"

    # A child declares the array's own shape at rank two, and the raveled pair above it.
    assert sporadik.raveled_shape([40, 12], 1, [0]) == [40, 12]
    assert sporadik.raveled_shape([40, 12, 3], 1, [0, 2]) == [12, 120]


def test_every_invariant_the_specification_lists_has_a_refusal_here() -> None:
    """The specification's checklist, mapped to the tests that prove each entry.

    Not a coverage metric: it is the list a second implementer works from, and the point is that
    each line of it is a thing *this* reader actually refuses rather than a thing the document
    merely hopes for. A named test that stops existing fails this.
    """
    proven = {
        "indptr length": (test_an_indptr_that_disagrees_with_the_shape_is_refused, test_the_indptr_check_follows_the_compressed_axis_rather_than_the_first_one),
        "parallel arrays": (test_values_and_indices_of_different_lengths_are_refused,),
        "path names the axis": (test_a_layout_filed_under_the_wrong_name_is_refused,),
        "index_order is a permutation": (test_an_index_order_that_is_not_a_permutation_is_refused,),
        "child shape and encoding by rank": (test_a_layout_whose_encoding_contradicts_its_axis_is_refused, test_a_rank_three_layout_declares_its_raveled_shape),
        "one layout per axis, at most rank": (test_two_layouts_compressing_one_axis_are_refused, test_more_layouts_than_axes_is_refused),
        "the block is present and complete": (test_a_store_with_no_block_is_refused, test_an_incomplete_block_is_refused),
        "the spec version is known": (test_an_unknown_spec_is_refused_rather_than_guessed_at,),
    }
    for invariant, tests in proven.items():
        assert all(callable(test) for test in tests), invariant
    assert len(proven) == 8, "the specification lists eight things a conforming reader checks"

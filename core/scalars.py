"""Custom GraphQL scalars.

Each scalar is a plain ``NewType`` used in annotations across types, inputs
and filters; the matching GraphQL definition lives in :data:`SCALAR_MAP`,
which the schema registers via ``StrawberryConfig(scalar_map=...)`` in
``mikro_server/schema.py`` (the deprecated class-wrapping ``strawberry.scalar``
form is gone).
"""

from typing import NewType

import strawberry
from strawberry.types.scalar import ScalarDefinition

ArrayLike = NewType("ArrayLike", str)
RGBAColor = NewType("RGBAColor", list)
FileLike = NewType("FileLike", str)
ImageFileLike = NewType("ImageFileLike", str)
ParquetLike = NewType("ParquetLike", str)
# A whole mesh collection as one uploaded prefix. Distinct from `ParquetLike` because it names
# a *tree* whose manifest the server reads, not a single object.
FabriksLike = NewType("FabriksLike", str)
# A whole network collection as one uploaded prefix. Distinct from `FabriksLike` for the same
# reason `FabriksLike` is distinct from `ParquetLike`: it names a tree of a *different format*,
# whose manifest declares an edge arity and a coarsening scheme a mesh manifest does not have.
KonnektionLike = NewType("KonnektionLike", str)
# One sparse matrix as one uploaded prefix: a zarr *group* holding `data`, `indices` and
# `indptr`. Distinct from `ArrayLike`, which names a single array -- a group has three shapes,
# three dtypes and three chunkings, and `get_zarr_metadata` refuses anything but an array.
SporadikLike = NewType("SporadikLike", str)
Matrix = NewType("Matrix", object)
FourByFourMatrix = NewType("FourByFourMatrix", object)
FiveDVector = NewType("FiveDVector", list)
FourDVector = NewType("FourDVector", list)
ThreeDVector = NewType("ThreeDVector", list)
TwoDVector = NewType("TwoDVector", list)
Any = NewType("Any", object)


def _identity(v: object) -> object:
    """Pass-through serialization: these scalars carry their JSON value unchanged."""
    return v


def _definition(name: str, description: str) -> ScalarDefinition:
    """A pass-through ScalarDefinition for :data:`SCALAR_MAP`."""
    return strawberry.scalar(name=name, description=description, serialize=_identity, parse_value=_identity)


SCALAR_MAP: dict[object, ScalarDefinition] = {
    ArrayLike: _definition("ArrayLike", "The `ArrayLike` scalar type represents a reference to a store previously created by the user n a datalayer"),
    RGBAColor: _definition("RGBAColor", "The Color scalar type represents a color as a list of 4 values RGBA"),
    FileLike: _definition("FileLike", "The `FileLike` scalar type represents a reference to a big file storage previously created by the user n a datalayer"),
    ImageFileLike: _definition("ImageFileLike", "The `ImageFileLike` scalar type represents a reference to a snapshot image previously created by the user n a datalayer"),
    ParquetLike: _definition("ParquetLike", "The `ParquetLike` scalar type represents a reference to a parquet objected stored previously created by the user on a datalayer"),
    FabriksLike: _definition(
        "FabriksLike",
        "A reference to an uploaded **fabriks store**: one prefix holding `fabriks.json`, both catalogs and every octree level. Request it with `requestFabriksUpload`, write the tree, land the manifest last, then `finishFabriksUpload` -- which reads the manifest and refuses a prefix without one. A collection registered this way declares no grid and no encoding: the server reads them from the artifact, so they cannot be stated wrong",
    ),
    KonnektionLike: _definition(
        "KonnektionLike",
        "A reference to an uploaded **konnektion store**: one prefix holding `konnektion.json`, both catalogs and every octree level of a node/edge network. Named for the wire format the way `FabriksLike` is, and a separate scalar because it is a separate format -- a graph, not a surface. Request it with `requestKonnektionUpload`, write the tree, land the manifest last, then `finishKonnektionUpload` -- which reads the manifest and refuses a prefix without one. A collection registered this way declares no grid and no encoding: the server reads them from the artifact, so they cannot be stated wrong",
    ),
    SporadikLike: _definition(
        "SporadikLike",
        "A reference to an uploaded **sporadik store**: one prefix holding one child per axis made contiguous, under `layouts/axis{k}`, each an anndata-spelled sparse group of `data`, `indices` and `indptr`. Named for the wire format the way `FabriksLike` is. Request it with `requestSparseUpload`, write the layouts, land the `sporadik` block last, then `finishSparseUpload` -- which reads that block and refuses a prefix without one, because zarr fills a missing chunk rather than failing and a torn upload is otherwise indistinguishable from a finished one. A dataset registered this way declares no encoding, no shape and no chunking: the server reads them from the artifact, so they cannot be stated wrong",
    ),
    Matrix: _definition("Matrix", "The `Matrix` scalar type represents a matrix values as specified by"),
    FourByFourMatrix: _definition("FourByFourMatrix", "The `FourByFourMatrix` scalar type represents a matrix values as specified by"),
    FiveDVector: _definition("FiveDVector", "The `Vector` scalar type represents a matrix values as specified by"),
    FourDVector: _definition("FourDVector", "The `Vector` scalar type represents a matrix values as specified by"),
    ThreeDVector: _definition("ThreeDVector", "The `Vector` scalar type represents a matrix values as specified by"),
    TwoDVector: _definition("TwoDVector", "The `Vector` scalar type represents a matrix values as specified by"),
    Any: _definition("Any", "The `Any` scalar any type"),
}

import strawberry
from strawberry.scalars import JSON
from datalayer import models
from kante.types import Info
import kante
from typing import cast
from datalayer import base_models
from datalayer.datalayer import get_current_datalayer


@kante.pydantic_type(base_models.BigFileAccessGrant, description="Temporary S3 credentials for reading a big file.")
class BigFileAccessGrant:
    """Temporary S3 credentials for a big file."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str

    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.MediaAccessGrant, description="Temporary S3 credentials for reading a media object.")
class MediaAccessGrant:
    """Temporary S3 credentials for a media object."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.GeneralMediaAccessGrant, description="Temporary S3 credentials for reading a media object.")
class GeneralMediaAccessGrant:
    """Temporary S3 credentials for a media object."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.ZarrAccessGrant, description="Temporary S3 credentials for reading a Zarr store.")
class ZarrAccessGrant:
    """Temporary S3 credentials for a Zarr store."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str

    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.GeneralZarrAccessGrant, description="Temporary S3 credentials for reading a Zarr store.")
class GeneralZarrAccessGrant:
    """Temporary S3 credentials for a Zarr store."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.GeneralParquetAccessGrant, description="Temporary S3 credentials for reading a parquet object.")
class GeneralParquetAccessGrant:
    """Temporary S3 credentials for a parquet object."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.ParquetAccessGrant, description="Temporary S3 credentials for reading a parquet object.")
class ParquetAccessGrant:
    """Temporary S3 credentials for a parquet object."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.SparseAccessGrant, description="Temporary S3 credentials for reading a sparse store. Covers the whole prefix, because a lookup needs `indptr` before it knows which range of `data` to fetch.")
class SparseAccessGrant:
    """Temporary S3 credentials for reading a sparse store."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


@kante.pydantic_type(base_models.GeneralSparseAccessGrant, description="Temporary S3 credentials for reading the organization's sparse stores.")
class GeneralSparseAccessGrant:
    """Temporary S3 credentials for the organization's sparse stores."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    expires_in: int


@kante.pydantic_type(base_models.SparseUploadGrant, description="Temporary S3 credentials for uploading a sparse store. Scoped to the prefix and permitted to read back and delete inside it, because the three arrays are written incrementally.")
class SparseUploadGrant:
    """Temporary S3 credentials for a sparse upload."""

    region: str
    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.pydantic_type(base_models.FabriksAccessGrant, description="Temporary S3 credentials for reading a fabriks store. Covers the whole prefix, so one grant reads the manifest, both catalogs and every level.")
class FabriksAccessGrant:
    """Temporary S3 credentials for a fabriks store."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


# Note the field list: `status`/`region`/`bucket`/`expires_in` and nothing else, because that
# is what `GeneralAccessGrant` actually carries. The zarr and parquet twins declare `path` and
# `store` here as non-null, neither of which exists on the model, so both resolve to None
# through a non-null field. Not copied.
@kante.pydantic_type(base_models.GeneralFabriksAccessGrant, description="Temporary S3 credentials for reading the organization's fabriks stores.")
class GeneralFabriksAccessGrant:
    """Temporary S3 credentials for the organization's fabriks stores."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    expires_in: int


@kante.pydantic_type(base_models.KonnektionAccessGrant, description="Temporary S3 credentials for reading a konnektion store. Covers the whole prefix, so one grant reads the manifest, both catalogs and every level.")
class KonnektionAccessGrant:
    """Temporary S3 credentials for a konnektion store."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None


# Same field list as its fabriks twin, and for the same reason: `GeneralAccessGrant` carries
# `status`/`region`/`bucket`/`expires_in` and nothing else, so declaring `path` or `store`
# here would resolve None through a non-null field.
@kante.pydantic_type(base_models.GeneralKonnektionAccessGrant, description="Temporary S3 credentials for reading the organization's konnektion stores.")
class GeneralKonnektionAccessGrant:
    """Temporary S3 credentials for the organization's konnektion stores."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    expires_in: int


@kante.pydantic_type(base_models.KonnektionUploadGrant, description="Temporary S3 credentials for uploading a konnektion store. Scoped to the prefix and permitted to read back and delete inside it, because the tree is written incrementally and its manifest lands last.")
class KonnektionUploadGrant:
    """Temporary S3 credentials for a konnektion upload."""

    region: str
    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


# Modelled on `MediaUploadGrant`, deliberately not on `ZarrUploadGrant`: that one declares an
# `action: str` the pydantic model has never had, and omits `region`, which it does have.
@kante.pydantic_type(base_models.FabriksUploadGrant, description="Temporary S3 credentials for uploading a fabriks store. Scoped to the prefix and permitted to read back and delete inside it, because the tree is written incrementally and its manifest lands last.")
class FabriksUploadGrant:
    """Temporary S3 credentials for a fabriks upload."""

    region: str
    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.pydantic_type(base_models.MediaUploadGrant, description="A presigned PUT grant for uploading a media object.")
class MediaUploadGrant:
    """A presigned PUT grant for a media upload."""

    region: str
    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.pydantic_type(base_models.BigFileUploadGrant, description="Temporary S3 credentials for uploading a big file.")
class BigFileUploadGrant:
    """Temporary S3 credentials for a big file upload."""

    region: str
    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.pydantic_type(base_models.ZarrUploadGrant, description="Temporary S3 credentials for uploading a Zarr store.")
class ZarrUploadGrant:
    """Temporary S3 credentials for a Zarr upload."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    action: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.pydantic_type(base_models.ParquetUploadGrant, description="Temporary S3 credentials for uploading a parquet store.")
class ParquetUploadGrant:
    """Temporary S3 credentials for a parquet upload."""

    status: str
    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    key: str
    path: str
    action: str
    expires_in: int
    max_bytes: int
    original_file_name: str | None
    upload_file_name: str
    upload_content_type: str | None
    upload_form_field: str
    store: str


@kante.django_type(
    models.BigFileStore,
    description="A BigFileStore represents a large object stored behind the S3 datalayer.",
)
class BigFileStore:
    """A large object stored behind the S3 datalayer."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    original_file_name: str | None
    content_type: str | None

    @strawberry.field(description="Get temporary S3 read credentials for the object.")
    def access_grant(self, info: Info, host: str | None = None) -> BigFileAccessGrant:
        """Return a signed read grant for the big file."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.BigFileStore, self).get_access_grant(datalayer=datalayer)
        return BigFileAccessGrant.from_pydantic(grant)

    @strawberry.field()
    def presigned_url(self, info: Info) -> str:
        """Compatibility field returning the canonical S3 object path."""
        datalayer = get_current_datalayer()
        return cast(models.BigFileStore, self).get_presigned_url(datalayer=datalayer)


@kante.django_type(models.MediaStore)
class MediaStore:
    """A media object stored behind the S3 datalayer."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    original_file_name: str | None
    content_type: str | None

    @kante.django_field(description="Get temporary S3 read credentials for the media object.")
    def access_grant(self, info: Info, host: str | None = None) -> MediaAccessGrant:
        """Return a signed read grant for the media object."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.MediaStore, self).get_access_grant(datalayer=datalayer)
        return MediaAccessGrant(**grant.model_dump())

    @kante.django_field(description="Compatibility field returning the canonical S3 object path.")
    def presigned_url(self, info: Info, host: str | None = None) -> str:
        """Compatibility field returning the canonical S3 object path."""
        datalayer = get_current_datalayer()
        return cast(models.MediaStore, self).get_presigned_url(datalayer=datalayer, host=host)


@strawberry.type(
    description=(
        "One stored layout of a sparse matrix: an anndata-spelled group under `layouts/<encoding>`, holding `data`, `indices` and `indptr`. Read it with two requests -- "
        "`indptr[i:i+2]` at the position, then the range those two offsets name in `indices` and `data`."
    )
)
class SparseLayout:
    """One stored layout of a sparse matrix, as its own group declares it."""

    path: str = strawberry.field(description="Where this layout sits inside the store's prefix, e.g. `layouts/csr_matrix`. A reader opens the group at this path, not the store root")
    encoding: str = strawberry.field(description="The anndata encoding this layout declares: `csr_matrix` or `csc_matrix`. It names which axis `indptr` indexes, which is the whole of what the two layouts differ in")
    encoding_version: str | None = strawberry.field(description="The version of that encoding, as the layout declares it")
    indexed_axis: int = strawberry.field(description="Which axis of the store's `shape` this layout makes contiguous. Ask along an axis no layout compresses and there is no range to read at all, only a scan of everything")
    index_order: list[int] = strawberry.field(
        description=(
            "The axes this layout did not compress, in the order `indices` was raveled over them. At rank two it has one member and says nothing; above it, unravel a returned "
            "position through this -- it is the one fact in the format that cannot be recovered from the bytes, so a wrong reading does not fail, it reads a different cell"
        )
    )
    nnz: int = strawberry.field(description="How many nonzeros this layout holds. Read from the length of `data`, never declared")
    dtype: str = strawberry.field(description="The dtype of the stored values")
    chunks: JSON | None = strawberry.field(
        description=(
            "The chunk length of each of `data`, `indices` and `indptr`. What decides the read cost: a chunk is the granularity at which bytes can be fetched, so a slice costs "
            "whole chunks -- measured on a 16 um matrix, one slice costs 0.95 ms at 32 768-element chunks and 23.55 ms at 4 Mi ones. Sized for one object-store request, where the "
            "cost is round trips rather than bytes and a chunk is also the unit the next lookup along an adjacent slice reuses"
        )
    )
    range_readable: bool = strawberry.field(
        description=(
            "Whether a slice can be fetched as an exact byte range instead of as whole chunks -- true when every array is one uncompressed chunk, so `indptr` names byte offsets "
            "into the raw buffer. False is the ordinary case and not a defect: the default trades bytes for cache reuse, which is the better trade when the cost is requests"
        )
    )

    @classmethod
    def of(cls, layout: dict) -> "SparseLayout":
        """Rebuild one layout from the JSON the store recorded at registration."""
        encoding = str(layout.get("encoding"))
        return cls(
            path=str(layout.get("path")),
            encoding=encoding,
            encoding_version=layout.get("encoding_version"),
            indexed_axis=int(layout.get("indexed_axis") or 0),
            index_order=[int(axis) for axis in (layout.get("index_order") or [])],
            nnz=int(layout.get("nnz") or 0),
            dtype=str(layout.get("dtype")),
            chunks=layout.get("chunks"),
            range_readable=bool(layout.get("range_readable")),
        )


@kante.django_type(
    models.SparseStore,
    description=(
        "A sparse matrix stored as an anndata-spelled zarr group behind the S3 datalayer: `data`, `indices` and `indptr`, with the encoding, shape and chunking read from the group "
        "itself rather than declared. Its `encoding` says which axis `indptr` indexes, and so which question it answers in one contiguous read -- ask the other and there is no range "
        "to read at all."
    ),
)
class SparseStore:
    """A sparse matrix stored as a zarr group behind the S3 datalayer."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    spec: str | None = strawberry.field(description="The version of the `sporadik` block this store was accepted under. A spec selects how every byte in the prefix is read, so an unknown one is refused rather than guessed at")
    shape: list[int] | None = strawberry.field(description="The shape of the matrix, as the root block declares it and every layout agrees. Two axes")

    @kante.django_field(
        description=(
            "The stored layouts, one per `layouts/<encoding>` child. Which axis a layout's `indptr` indexes decides which question it answers in one contiguous read, so a store "
            "holding one layout offers one capability and a store holding both offers both. Empty while the store is unpopulated, which is the only state in which what it holds is unknown"
        )
    )
    def layouts(self, info: Info) -> list["SparseLayout"]:
        """The layouts this store holds, rebuilt from what was read off the artifact."""
        del info
        return [SparseLayout.of(layout) for layout in (cast(models.SparseStore, self).layouts or [])]

    @kante.django_field(description="Get temporary S3 read credentials for the sparse store.")
    def access_grant(self, info: Info, host: str | None = None) -> SparseAccessGrant:
        """Return a signed read grant for the sparse store."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.SparseStore, self).get_access_grant(datalayer=datalayer)
        return SparseAccessGrant(**grant.model_dump())


@kante.django_type(models.FabriksStore, description="A fabriks collection stored as a prefix of Parquet files behind the S3 datalayer. Its grid and encoding are read from its own manifest, never declared by a caller.")
class FabriksStore:
    """A fabriks store: one prefix holding a manifest, two catalogs and the level partitions."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    spec_version: str | None
    grid: JSON | None
    encoding: JSON | None
    axes: list[str] | None
    counts: JSON | None
    files: JSON | None

    @kante.django_field(description="Get temporary S3 read credentials covering this store's whole prefix -- the manifest, both catalogs and every level, in one grant.")
    def access_grant(self, info: Info, host: str | None = None) -> FabriksAccessGrant:
        """Return a signed read grant for the fabriks prefix."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.FabriksStore, self).get_access_grant(datalayer=datalayer)
        return FabriksAccessGrant(**grant.model_dump())


@kante.django_type(models.KonnektionStore, description="A konnektion collection stored as a prefix of Parquet files behind the S3 datalayer. Its grid and encoding are read from its own manifest, never declared by a caller.")
class KonnektionStore:
    """A konnektion store: one prefix holding a manifest, two catalogs and the level partitions."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    spec_version: str | None
    grid: JSON | None
    encoding: JSON | None
    axes: list[str] | None
    counts: JSON | None
    files: JSON | None
    attributes: JSON | None = strawberry.field(description="The per-node value columns the manifest declares, each {name, encoding, semantics} -- the vocabulary a network layer's GRAPH picker entries are validated against, `radius` joining off `encoding.radii`. Null on a store filled before attributes existed, which reads the same as declaring none")

    @kante.django_field(description="Get temporary S3 read credentials covering this store's whole prefix -- the manifest, both catalogs and every level, in one grant.")
    def access_grant(self, info: Info, host: str | None = None) -> KonnektionAccessGrant:
        """Return a signed read grant for the konnektion prefix."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.KonnektionStore, self).get_access_grant(datalayer=datalayer)
        return KonnektionAccessGrant(**grant.model_dump())


@kante.django_type(models.ZarrStore)
class ZarrStore:
    """A Zarr object stored behind the S3 datalayer."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    shape: list[int]
    chunks: list[int] = strawberry.field(description="Effective inner chunk shape — the brick/residency unit a reader can decode. For sharded arrays this is the sharding codec's inner chunk_shape, not the chunk-grid (shard) shape.")
    shards: list[int] | None = strawberry.field(description="Shard (outer storage object) shape for zarr v3 sharding_indexed arrays; null when unsharded. Shards exist to cut object count — readers should still treat `chunks` as the brick unit.")
    version: str | None
    dtype: str | None
    dimension_names: list[str | None] | None
    fill_value: JSON
    attributes: JSON | None
    storage_transformers: JSON | None
    chunk_key_encoding: JSON | None
    codecs: JSON | None

    @kante.django_field(description="Get temporary S3 read credentials for the Zarr object.")
    def access_grant(self, info: Info, host: str | None = None) -> ZarrAccessGrant:
        """Return a signed read grant for the Zarr store."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.ZarrStore, self).get_access_grant(datalayer=datalayer)
        return ZarrAccessGrant(**grant.model_dump())


@kante.django_type(models.ParquetStore)
class ParquetStore:
    """A parquet object stored behind the S3 datalayer."""

    id: strawberry.auto
    path: str
    bucket: str
    key: str
    max_bytes: int | None = strawberry.field(description="The byte budget the upload grant advertised for this store. Advertised, not enforced: a session policy bounds what a credential may write, never how much, so a store may exceed this")
    size_bytes: int | None = strawberry.field(description="How many bytes this store actually holds, measured when its upload was finished. Null while unfinished, or for stores written before this was recorded")
    original_file_name: str | None
    content_type: str | None

    @kante.django_field(description="Get temporary S3 read credentials for the parquet object.")
    def access_grant(self, info: Info, host: str | None = None) -> ParquetAccessGrant:
        """Return a signed read grant for the Zarr store."""
        del info, host
        datalayer = get_current_datalayer()
        grant = cast(models.ParquetStore, self).get_access_grant(datalayer=datalayer)
        return ParquetAccessGrant(**grant.model_dump())

    @kante.django_field(description="Compatibility field returning the canonical S3 object path.")
    def presigned_url(self, info: Info, host: str | None = None) -> str:
        """Compatibility field returning the canonical S3 object path."""
        datalayer = get_current_datalayer()
        return cast(models.ParquetStore, self).get_presigned_url(datalayer=datalayer, host=host)

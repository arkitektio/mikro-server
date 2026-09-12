from typing import Literal, Optional, cast

from pydantic import BaseModel, JsonValue


class RequestMediaUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a media object."""

    original_file_name: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None


class FinishMediaUploadInput(BaseModel):
    """Mark a MediaStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RequestMediaAccessInput(BaseModel):
    """Request temporary S3 access credentials for a media object."""

    store_id: str


class RequestGeneralMediaAccessInput(BaseModel):
    """Request temporary S3 access credentials for media objects in the organization."""

    expires_in: Optional[int] = None


class RequestGeneralZarrAccessInput(BaseModel):
    """Request temporary S3 access credentials for media objects in the organization."""

    expires_in: Optional[int] = None


class RequestGeneralFabriksAccessInput(BaseModel):
    """Request temporary S3 access credentials for fabriks stores in the organization."""

    expires_in: Optional[int] = None


class RequestGeneralKonnektionAccessInput(BaseModel):
    """Request temporary S3 access credentials for konnektion stores in the organization."""

    expires_in: Optional[int] = None


class RequestGeneralSparseAccessInput(BaseModel):
    """Request temporary S3 access credentials for sparse stores in the organization."""

    expires_in: Optional[int] = None


class RequestGeneralParquetAccessInput(BaseModel):
    """Request temporary S3 access credentials for media objects in the organization."""

    expires_in: Optional[int] = None


class RequestBigFileUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a big file."""

    original_file_name: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class FinishBigFileUploadInput(BaseModel):
    """Mark a BigFileStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RequestBigFileAccessInput(BaseModel):
    """Request temporary S3 access credentials for a media object."""

    store_id: str


class RequestZarrUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a Zarr store."""

    shape: Optional[list[int]] = None
    chunks: Optional[list[int]] = None
    version: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class FinishZarrUploadInput(BaseModel):
    """Mark a ZarrStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RefreshZarrUploadInput(BaseModel):
    """Reissue upload credentials for a Zarr store whose upload is still in flight."""

    store_id: str


class RequestZarrAccessInput(BaseModel):
    """Request temporary S3 access credentials for a media object."""

    store_id: str


class RequestFabriksUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a fabriks store.

    Carries nothing about the meshes. A fabriks store is *self-describing*: the writer states
    its grid and encoding in the manifest it uploads, and the server reads them back when the
    upload is finished. Declaring them here would create a second statement of the same facts,
    free to disagree with the bytes.
    """

    host: Optional[str] = None
    port: Optional[int] = None


class FinishFabriksUploadInput(BaseModel):
    """Mark a FabriksStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RequestFabriksAccessInput(BaseModel):
    """Request temporary S3 access credentials for a fabriks store."""

    store_id: str


class RequestKonnektionUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a konnektion store.

    Carries nothing about the networks, for the reason its fabriks sibling carries nothing about
    the meshes: a konnektion store is *self-describing*. The writer states its grid and encoding
    in the manifest it uploads, and the server reads them back when the upload is finished.
    Declaring them here would create a second statement of the same facts, free to disagree with
    the bytes.
    """

    host: Optional[str] = None
    port: Optional[int] = None


class FinishKonnektionUploadInput(BaseModel):
    """Mark a KonnektionStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RequestKonnektionAccessInput(BaseModel):
    """Request temporary S3 access credentials for a konnektion store."""

    store_id: str


class RequestSparseUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a sparse store.

    Carries nothing about the matrix, for the reason its fabriks sibling carries nothing about
    the meshes: a sparse store is *self-describing*. The writer states the encoding, the shape
    and the chunking in the group it uploads, and the server reads them back when the upload is
    finished. Declaring them here would be a second statement of the same facts, free to
    disagree with the bytes.

    Note this is the one place it differs from `RequestZarrUploadInput`, which does take
    `shape` and `chunks`: that grant describes a single array whose metadata a caller may
    legitimately know in advance, where this one describes a group of three whose relationship
    is the whole content.
    """

    host: Optional[str] = None
    port: Optional[int] = None


class FinishSparseUploadInput(BaseModel):
    """Mark a SparseStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RefreshSparseUploadInput(BaseModel):
    """Reissue upload credentials for a sparse store whose upload is still in flight.

    Present for the same reason `RefreshZarrUploadInput` is: a prefix is written incrementally,
    and a session token can expire in the middle of a matrix that takes minutes to upload.
    """

    store_id: str


class RequestSparseAccessInput(BaseModel):
    """Request temporary S3 access credentials for a sparse store."""

    store_id: str


class SparseLayoutMetadata(BaseModel):
    """One stored layout of a sparse matrix, as its own group declares it.

    ``encoding`` is the whole of what the two layouts differ in -- ``csr_matrix`` means
    ``indptr`` indexes axis 0, ``csc_matrix`` axis 1 -- and therefore which question this layout
    answers in one contiguous read. It is never taken from a caller.
    """

    path: str
    encoding: str
    encoding_version: Optional[str] = None
    indexed_axis: int
    #: The axes this layout did *not* compress, in the order ``indices`` was raveled over them.
    #: At rank two it has one member and says nothing; above it, it is the one fact in the format
    #: that cannot be recovered from the bytes -- a wrong one does not fail, it puts every value
    #: in a different cell -- which is why the writer states it and the reader checks it.
    index_order: list[int]
    nnz: int
    dtype: str
    chunks: JsonValue = None
    #: Whether a slice of this layout can be fetched as an exact byte range rather than as whole
    #: chunks. **Derived, never declared** -- true exactly when each array is one uncompressed
    #: chunk, so the stored object is the raw buffer and `indptr` names byte offsets into it.
    #:
    #: False is the ordinary case and not a defect: the default trades bytes for reuse, because on
    #: an object store the cost is requests, and a chunk is a cache unit that the next lookup along
    #: an adjacent slice hits again.
    range_readable: bool = False


class SparseMetadata(BaseModel):
    """What a sparse store states about itself, as discovered from its own zarr metadata.

    The sparse analogue of :class:`ZarrMetadata` and :class:`FabriksMetadata`, and read for the
    same reason: a fact derived from the artifact cannot be declared wrong.

    Unlike those two it is *nested*, because one matrix is one upload and may hold a layout per
    axis. ``shape`` is the store's, at whatever rank it has, and every layout is checked against
    it; everything that differs between layouts lives in :class:`SparseLayoutMetadata`.

    **Two axes is one case, not the definition.** A layout is one axis made contiguous, so an
    array of rank *n* has up to *n* of them -- a (object, feature, timepoint) matrix can answer
    "this object", "this feature" and "this timepoint" in one contiguous read each.

    ``spec`` comes from the root block, which the writer lands **last**. That ordering is the
    only reason an interrupted upload is detectable at all: zarr writes an array's metadata
    ahead of its chunks and substitutes the fill value for a chunk it cannot fetch, so a torn
    prefix otherwise reads back as the right number of zeros and raises nothing.
    """

    spec: str
    shape: list[int]
    layouts: list[SparseLayoutMetadata]


class FabriksMetadata(BaseModel):
    """The manifest of a fabriks store, as discovered from ``fabriks.json``.

    The fabriks analogue of :class:`ZarrMetadata`, and the reason a fabriks store is one store
    rather than a handful: everything a reader needs to decode the geometry travels with the
    geometry.
    """

    spec_version: str
    grid: JsonValue
    encoding: JsonValue
    axes: Optional[list[str]] = None
    counts: JsonValue = None
    files: JsonValue = None


class KonnektionMetadata(BaseModel):
    """The manifest of a konnektion store, as discovered from ``konnektion.json``.

    The konnektion analogue of :class:`FabriksMetadata`, and read for the same reason:
    everything a reader needs to decode the nodes and edges travels with them.
    """

    spec_version: str
    grid: JsonValue
    encoding: JsonValue
    axes: Optional[list[str]] = None
    #: The per-node value columns the manifest declares, each ``{name, encoding, semantics}``.
    #: Empty for a manifest that declares none or predates the key -- the same fact either way.
    attributes: list[JsonValue] = []
    counts: JsonValue = None
    files: JsonValue = None


class ParquetColumn(BaseModel):
    """One column, as the file itself declares it.

    The three fields are the first three of a DuckDB ``DESCRIBE`` row. ``type`` is therefore a
    DuckDB type name (``BIGINT``, ``DOUBLE``, ``VARCHAR``) -- the same vocabulary a caller used
    to have to guess at, now read rather than declared.
    """

    name: str
    type: str
    nullable: bool


class ZarrMetadata(BaseModel):
    """Structured metadata discovered from a Zarr store."""

    zarr_format: int
    node_type: Literal["array"]
    shape: list[int]
    data_type: JsonValue
    chunk_grid: JsonValue
    chunk_key_encoding: JsonValue
    fill_value: JsonValue
    codecs: list[JsonValue]
    attributes: dict[str, JsonValue] | None = None
    storage_transformers: list[JsonValue] | None = None
    dimension_names: list[str | None] | None = None

    @property
    def version(self) -> str:
        """Return the Zarr format version as a string for legacy callers."""

        return str(self.zarr_format)

    @property
    def dtype(self) -> str | None:
        """Return the data type identifier when it is a plain string."""

        return self.data_type if isinstance(self.data_type, str) else None

    def _chunk_grid_shape(self) -> list[int] | None:
        """Return the chunk-grid shape from ``chunk_grid.configuration.chunk_shape``.

        For a sharded array this is the *shard* shape — zarr v3 puts the outer
        (storage-object) shape in the chunk grid and hides the readable inner
        chunk shape inside the sharding codec's configuration.
        """

        if not isinstance(self.chunk_grid, dict):
            return None

        configuration = self.chunk_grid.get("configuration")
        if not isinstance(configuration, dict):
            return None

        chunk_shape = configuration.get("chunk_shape")
        if not isinstance(chunk_shape, list) or not all(isinstance(item, int) for item in chunk_shape):
            return None

        return cast(list[int], chunk_shape)

    def _sharding_configuration(self) -> dict[str, JsonValue] | None:
        """Return the ``sharding_indexed`` codec configuration, or None when unsharded.

        Only ``codecs[0]`` is considered: readers require the sharding codec to sit
        first (and alone) at the top level, and :meth:`validate_sharding` rejects any
        other placement, so a sharding codec elsewhere must not silently flip the
        chunks/shards split here.
        """

        if not self.codecs:
            return None
        first = self.codecs[0]
        if not isinstance(first, dict) or first.get("name") != "sharding_indexed":
            return None
        configuration = first.get("configuration")
        return configuration if isinstance(configuration, dict) else None

    @property
    def shards(self) -> list[int] | None:
        """Return the shard (outer storage-object) shape, or None for unsharded arrays."""

        if self._sharding_configuration() is None:
            return None
        return self._chunk_grid_shape()

    @property
    def chunks(self) -> list[int] | None:
        """Return the effective inner chunk shape — the unit a reader can decode.

        For a sharded array that is the sharding codec's inner ``chunk_shape``,
        not the chunk grid's (which is the shard shape); unsharded arrays fall
        back to the chunk grid.
        """

        configuration = self._sharding_configuration()
        if configuration is not None:
            inner = configuration.get("chunk_shape")
            if isinstance(inner, list) and all(isinstance(item, int) for item in inner):
                return cast(list[int], inner)
            return None
        return self._chunk_grid_shape()

    def validate_sharding(self) -> None:
        """Reject sharded layouts the platform's readers cannot decode.

        Mirrors the frontend's sharding contract: ``sharding_indexed`` must be the
        sole top-level codec, ranks must match, the shard must be an exact per-axis
        multiple of the inner chunk, sharding must not nest, and the index location
        must be a known value. Unsharded arrays pass untouched.

        Raises:
            ValueError: If a sharding codec is present but the layout is unreadable.
        """

        sharding_positions = [
            i
            for i, codec in enumerate(self.codecs)
            if isinstance(codec, dict) and codec.get("name") == "sharding_indexed"
        ]
        if not sharding_positions:
            return
        if sharding_positions != [0] or len(self.codecs) != 1:
            raise ValueError(f"'sharding_indexed' must be the sole top-level codec (found it at position {sharding_positions[0]} of {len(self.codecs)}); readers reject any other layout.")

        configuration = self._sharding_configuration()
        inner = configuration.get("chunk_shape") if configuration else None
        inner_codecs = configuration.get("codecs") if configuration else None
        if not isinstance(inner, list) or not inner or not all(isinstance(item, int) and item > 0 for item in inner) or not isinstance(inner_codecs, list):
            raise ValueError("Malformed 'sharding_indexed' configuration: missing or invalid chunk_shape or codecs.")

        shard = self._chunk_grid_shape()
        if shard is None:
            raise ValueError("Malformed zarr.json metadata: sharded array without a regular chunk grid shape.")
        if len(inner) != len(shard):
            raise ValueError(f"Sharding inner chunk rank {len(inner)} does not match the shard rank {len(shard)}.")
        for axis, (shard_size, inner_size) in enumerate(zip(shard, inner)):
            if shard_size % inner_size != 0:
                raise ValueError(f"Shard shape {shard} is not an exact multiple of the inner chunk shape {inner} on axis {axis}.")

        for codec in inner_codecs:
            if isinstance(codec, dict) and codec.get("name") == "sharding_indexed":
                raise ValueError("Nested 'sharding_indexed' codecs are not supported by readers.")

        index_location = configuration.get("index_location") if configuration else None
        if index_location is not None and index_location not in ("start", "end"):
            raise ValueError(f"Unknown sharding index_location {index_location!r}; readers only support 'start' and 'end'.")


class RequestParquetUploadInput(BaseModel):
    """Request temporary S3 upload credentials for a Parquet store."""

    content_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class FinishParquetUploadInput(BaseModel):
    """Mark a ParquetStore as populated after a successful upload."""

    store_id: str
    valid: bool = True


class RequestParquetAccessInput(BaseModel):
    """Request temporary S3 access credentials for a media object."""

    store_id: str


class AccessGrant(BaseModel):
    """Temporary S3 credentials scoped to a datalayer action."""

    status: str = "granted"
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    key: str
    path: str
    expires_in: int
    store: str | None = None


class GeneralAccessGrant(BaseModel):
    """Temporary S3 credentials for an existing media object, without a store reference."""

    status: str = "granted"
    access_key: str
    secret_key: str
    session_token: str
    region: str
    bucket: str
    expires_in: int


class GeneralMediaAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for an existing media object, without a store reference."""


class GeneralZarrAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for an existing media object, without a store reference."""


class GeneralParquetAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for an existing media object, without a store reference."""


class GeneralFabriksAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for existing fabriks stores, without a store reference."""


class GeneralKonnektionAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for existing konnektion stores, without a store reference."""


class GeneralSparseAccessGrant(GeneralAccessGrant):
    """Temporary S3 credentials for existing sparse stores, without a store reference."""


class BigFileAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing big file."""


class MediaAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing media object."""


class ZarrAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing Zarr store."""


class ParquetAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing parquet store."""


class FabriksAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing fabriks store.

    Covers the whole prefix, so one grant reads the manifest, both catalogs and every level --
    where the same collection stored as separate objects needed one grant each.
    """


class KonnektionAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing konnektion store.

    Covers the whole prefix, so one grant reads the manifest, both catalogs and every level.
    """


class SparseAccessGrant(AccessGrant):
    """Temporary S3 credentials for an existing sparse store.

    Covers the whole prefix, so one grant reads the group's attributes and all three of its
    arrays -- which is the minimum that answers anything, since a lookup needs `indptr` before
    it knows which range of `data` to fetch.
    """


class BaseUploadGrant(AccessGrant):
    """Temporary S3 credentials for uploads bound to a specific store."""

    region: str
    max_bytes: int
    original_file_name: str | None = None
    upload_file_name: str
    upload_content_type: str | None = None
    upload_form_field: str = "file"


class MediaUploadGrant(BaseUploadGrant):
    """A presigned PUT grant for a media upload."""


class BigFileUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a big file upload."""


class ZarrUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a Zarr upload."""


class ParquetUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a parquet upload."""


class SparseUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a sparse upload.

    Scoped to the prefix and permitted to read back and delete inside it, because the three
    arrays are written incrementally.
    """


class FabriksUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a fabriks upload.

    Scoped to the prefix, and -- unlike an object upload -- permitted to read back and delete
    inside it, because a tree is written incrementally and its manifest lands last.
    """


class KonnektionUploadGrant(BaseUploadGrant):
    """Temporary S3 credentials for a konnektion upload.

    Scoped to the prefix, and -- unlike an object upload -- permitted to read back and delete
    inside it, because a tree is written incrementally and its manifest lands last.
    """

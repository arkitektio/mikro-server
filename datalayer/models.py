import logging
from pathlib import PurePosixPath
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from django.db import models
from polymorphic.models import PolymorphicModel
from datalayer import base_models, sporadik
from datalayer.datalayer import AccessGrant, Datalayer

if TYPE_CHECKING:
    from types_boto3_s3.type_defs import FileobjTypeDef


logger = logging.getLogger(__name__)


def get_default_upload_token() -> str:
    """Return the default opaque token used sfor storage keys."""
    return uuid4().hex


def build_opaque_storage_key(original_file_name: str, generator: Callable[[], str] = get_default_upload_token) -> str:
    """Build a fully opaque storage key without sembsedding filename metadata."""
    del original_file_name
    return generator()


#: Which axis a sparse encoding's ``indptr`` indexes, which is the whole of what the two
#: layouts differ in. A dict rather than a pair of constants because it is also the validation:
#: an ``encoding-type`` outside it is a group nothing here can honestly claim to understand.
#: The sporadik format's own names and rules, re-exported so the models here read as one thing.
#:
#: **Defined in :mod:`datalayer.sporadik`, not here.** That module is this server's independent
#: implementation of a format specified elsewhere -- in the `sporadik` package's ``README.md`` --
#: and keeping the constants beside the parser that uses them is what stops the two drifting into
#: two slightly different readings of the same bytes.
SPARSE_INDEXED_AXIS = sporadik.INDEXED_AXIS
SPARSE_BLOCK_KEY = sporadik.BLOCK_KEY
SPARSE_LAYOUTS_GROUP = sporadik.LAYOUTS_GROUP
SPARSE_MIN_RANK = sporadik.MIN_RANK
SPARSE_SPECS = sporadik.SUPPORTED_VERSIONS
sparse_layout_path = sporadik.layout_path
sparse_anndata_encoding = sporadik.anndata_encoding


class DatalayerStore(PolymorphicModel):
    """An object stored behind the S3-backed datalayer."""

    objects: models.Manager["DatalayerStore"]  # type: ignore[assignment]

    #: The logical datalayer bucket this kind of store lives in -- the key
    #: ``Datalayer.get_bucket_config`` takes, and the value written to the ``bucket`` column.
    #: A ClassVar because it is a property of the *type*, not of a row: the column records what
    #: a store was written with, this records where its kind belongs. Declared so
    #: :func:`Datalayer.prefix_bucket_keys` can derive which buckets hold prefixes from the
    #: classes themselves rather than from a literal that has to be remembered.
    bucket_key: ClassVar[str] = ""

    #: Whether ``key`` names a *prefix* -- a directory of objects -- rather than one object.
    #: A ClassVar, so it is not queryable and does not need to be: ``DatalayerStore.objects``
    #: is polymorphic, so a query returns already-downcast instances and this is read off each.
    #: Read by ``purge_bytes`` to choose object vs prefix deletion, and -- through
    #: ``bucket_key`` above -- by the grant builder to decide whether a grant must cover a whole
    #: tree. **Both halves matter**: a prefix store whose grants are object-scoped cannot list
    #: or write its own children, and one whose deletion is object-scoped leaks every byte it
    #: ever wrote, because ``DeleteObject`` on a prefix succeeds having removed nothing.
    is_prefix: ClassVar[bool] = False

    organization = models.ForeignKey(
        "authentikate.Organization",
        on_delete=models.CASCADE,
        help_text="The organization this store belongs to.",
    )
    path = models.CharField(max_length=1000, null=True, blank=True, help_text="The object-store URI of the file", unique=True)
    key = models.CharField(max_length=1000, help_text="The object key/path within the datalayer bucket.")
    bucket = models.CharField(max_length=1000, help_text="The datalayer bucket/service this store belongs to.")
    original_file_name = models.CharField(max_length=1000, null=True, blank=True, help_text="The original client-provided file name.")
    content_type = models.CharField(max_length=255, null=True, blank=True, help_text="The client-provided content type for the uploaded file.")
    populated = models.BooleanField(default=False, help_text="Whether the store has been populated with a valid path and is ready for use.")
    max_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "The byte budget the upload grant advertised for this store, recorded so the advertised number and the delivered one can be compared. **Advertised, not enforced**: a "
            "session policy scopes what a credential may write, never how much, so S3 accepts an upload that exceeds this and `finish` accepts it as valid"
        ),
    )
    size_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="How many bytes this store actually holds, measured when the upload was finished. Null while an upload is unfinished, or for stores written before this was recorded",
    )
    orphaned_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "When the last data row referencing this store was deleted, or null while it is still in use. A *candidate* for garbage collection, not an authority: "
            "`purge_orphaned_stores` re-checks for referrers before deleting anything, and clears this again if the store was re-attached in the meantime"
        ),
    )

    def build_store_path(self, datalayer: Datalayer | None = None) -> str:
        """Return the canonical object-store URI for this store."""
        layer = datalayer or Datalayer()
        return layer.build_store_path(self.bucket, self.key)

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> AccessGrant:
        """Return temporary credentials for reading this store."""
        del host
        return datalayer.generate_file_read_url(self.bucket, self.key, store_id=str(self.pk))

    def grant_delete_access(self, datalayer: Datalayer) -> AccessGrant:
        """Return temporary credentials for deleting this store."""
        return datalayer.generate_file_delete_url(self.bucket, self.key, store_id=str(self.pk))

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Finalize the store after a successful upload."""
        raise NotImplementedError("Subclasses must implement fill_info()")

    def purge_bytes(self, datalayer: Datalayer | None = None) -> int:
        """Delete this store's objects from S3, and return how many went.

        Prefix-aware: a zarr is a directory of chunks and needs list-then-batch-delete, where
        every other store is a single key. Idempotent, so a retry after a partial failure is
        safe -- deleting an absent key is a no-op.
        """
        layer = datalayer or Datalayer()
        if self.is_prefix:
            return layer.delete_prefix(self.bucket, self.key)
        layer.delete_object(self.bucket, self.key)
        return 1

    def measure_bytes(self, datalayer: Datalayer | None = None) -> int:
        """Return how many bytes this store actually occupies in S3.

        Prefix-aware for the same reason as :meth:`purge_bytes`: a zarr is a directory, so its
        size is the sum over a listing, while every other store is one key and a HEAD answers.

        This is the only thing that can check an upload against the ``max_bytes`` its grant
        advertised, because nothing at write time can. A session policy scopes *what* a
        credential may write, not *how much* -- ``s3:PutObject`` takes no size condition -- so a
        cap is measurable after the fact or not at all.
        """
        layer = datalayer or Datalayer()
        if self.is_prefix:
            return layer.measure_prefix_bytes(self.bucket, self.key)
        return layer.get_object_size(self.bucket, layer.build_object_key(self.bucket, self.key))

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        """Delete the remote objects, then the row.

        **This purges immediately and ignores the grace period.** It is the "I mean it, now"
        path; the ordinary route is to let a data-row deletion flag the store and let
        `purge_orphaned_stores` collect it, which is what every delete mutation does. Nothing
        in `core.mutations` calls this.

        Bytes first, then the row, deliberately: bytes gone with the row still present is
        recoverable by re-running the sweep, while the row gone with bytes left is a leak
        nothing points at any more. A failure therefore propagates rather than being logged
        and swallowed -- the old behaviour dropped the row anyway and left the bytes orphaned
        with no record of them.
        """
        self.purge_bytes()
        return super().delete(*args, **kwargs)

    def get_upload_file_name(self) -> str:
        """Return the client-visible filename to use in multipart uploads."""
        if self.original_file_name:
            return PurePosixPath(self.original_file_name).name

        return self.key.rsplit("/", 1)[-1]


class BigFileStore(DatalayerStore):
    """A large file stored behind the S3-backed datalayer."""

    objects: models.Manager["BigFileStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "bigfile"

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.BigFileAccessGrant:
        """Return temporary credentials for reading this big file."""
        del host
        return datalayer.generate_bigfile_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.BigFileAccessGrant:
        """Return temporary credentials for reading the object."""
        return self.grant_read_access(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Mark the object as populated and normalize its stored URI."""
        self.path = self.build_store_path(datalayer)
        self.populated = True
        self.save(update_fields=["path", "populated"])

    def get_presigned_url(
        self,
        datalayer: Datalayer,
        host: str | None = None,
    ) -> str:
        """Return the canonical S3 path for the object."""
        del host
        return self.build_store_path(datalayer)

    def calculate_size(self, datalayer: Datalayer) -> int:
        """Calculate the size of the big file by querying the datalayer."""
        return datalayer.get_object_size(self.bucket, self.key)


class MediaStore(DatalayerStore):
    """Media objects stored behind the S3-backed datalayer."""

    objects: models.Manager["MediaStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "media"

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.MediaAccessGrant:
        """Return temporary credentials for reading this media object."""
        del host
        return datalayer.generate_media_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.MediaAccessGrant:
        """Return temporary credentials for reading the object."""
        return self.grant_read_access(datalayer)

    def get_presigned_url(self, datalayer: Datalayer, host: str | None = None) -> str:
        """Return the canonical S3 path for the object."""
        del host
        return self.build_store_path(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Mark the object as populated and normalize its stored URI."""
        self.path = self.build_store_path(datalayer)
        self.populated = True
        self.save(update_fields=["path", "populated"])

    def put_file(self, datalayer: Datalayer, file: "FileobjTypeDef") -> None:
        """Upload a file with the service credentials and finalize the store."""
        datalayer.put_file(
            self.bucket,
            self.key,
            file.read(),
            getattr(file, "content_type", "application/octet-stream"),
        )
        self.fill_info(datalayer)


class ZarrStore(DatalayerStore):
    """Zarr objects stored behind the S3-backed datalayer."""

    objects: models.Manager["ZarrStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "zarr"

    # A zarr is a *directory* -- `zarr.json` plus a tree of chunk objects -- so its `key` is a
    # prefix and there is no single object at it. `DeleteObject` against a prefix succeeds with
    # a 204 having deleted nothing, which is why removing one needs list + batched delete.
    is_prefix: ClassVar[bool] = True

    shape = models.JSONField(null=True, blank=True, help_text="The shape of the Zarr array stored at this location.")
    chunks = models.JSONField(null=True, blank=True, help_text="The effective inner chunk shape of the Zarr array — the unit a reader can decode. For sharded arrays this is the sharding codec's inner chunk shape, not the chunk grid's.")
    shards = models.JSONField(null=True, blank=True, help_text="The shard (outer storage object) shape when the array uses zarr v3 sharding_indexed; null for unsharded arrays. When set, `chunks` holds the inner chunk shape.")
    version = models.CharField(max_length=10, null=True, blank=True, help_text="The Zarr format version of the array stored at this location.")
    dtype = models.CharField(max_length=255, null=True, blank=True, help_text="The dtype of the Zarr array stored at this location.")
    dimension_names = models.JSONField(null=True, blank=True, help_text="The dimension names declared by the Zarr array.")
    fill_value = models.JSONField(null=True, blank=True, help_text="The fill value declared by the Zarr array.")
    attributes = models.JSONField(null=True, blank=True, help_text="The user attributes stored in zarr.json.")
    storage_transformers = models.JSONField(null=True, blank=True, help_text="The storage transformers declared by the Zarr array.")
    chunk_key_encoding = models.JSONField(null=True, blank=True, help_text="The chunk key encoding configuration for the Zarr array.")
    codecs = models.JSONField(null=True, blank=True, help_text="The codec pipeline declared for the Zarr array.")

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.ZarrAccessGrant:
        """Return temporary credentials for reading this Zarr prefix."""
        del host
        return datalayer.generate_zarr_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.ZarrAccessGrant:
        """Return temporary credentials for reading the object prefix."""
        return self.grant_read_access(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Populate Zarr metadata and mark the store as ready.

        Raises:
            FileNotFoundError: If the Zarr metadata file cannot be retrieved.
            ValueError: If the Zarr metadata is invalid or unsupported.
        """
        layer = datalayer or Datalayer()
        self.path = self.build_store_path(layer)
        metadata = layer.get_zarr_metadata(self)
        self.shape = metadata.shape
        self.chunks = metadata.chunks
        self.shards = metadata.shards
        self.dtype = metadata.dtype
        self.dimension_names = metadata.dimension_names
        self.fill_value = metadata.fill_value
        self.attributes = metadata.attributes
        self.storage_transformers = metadata.storage_transformers
        self.chunk_key_encoding = metadata.chunk_key_encoding
        self.codecs = metadata.codecs
        self.version = metadata.version
        self.populated = True
        self.save(
            update_fields=[
                "path",
                "shape",
                "chunks",
                "shards",
                "dtype",
                "dimension_names",
                "fill_value",
                "attributes",
                "storage_transformers",
                "chunk_key_encoding",
                "codecs",
                "version",
                "populated",
            ]
        )

    @property
    def c_size(self) -> int:
        """Return the regular chunk shape for callers using the legacy field name."""
        return self.shape[0]

    @property
    def t_size(self) -> int:
        """Return the regular chunk shape for callers using the legacy field name."""
        return self.shape[1]

    @property
    def z_size(self) -> int:
        """Return the regular chunk shape for callers using the legacy field name."""
        return self.shape[2]

    @property
    def y_size(self) -> int:
        """Return the regular chunk shape for callers using the legacy field name."""
        return self.shape[3]

    @property
    def x_size(self) -> int:
        """Return the regular chunk shape for callers using the legacy field name."""
        return self.shape[4]


class ParquetStore(DatalayerStore):
    """Parquet objects stored behind the S3-backed datalayer."""

    objects: models.Manager["ParquetStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "parquet"

    columns = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The file's own schema, as DESCRIBE reports it: one entry per column with `name`, `type` and `nullable`, in file order. "
            "Read off the parquet when the upload was finished, never declared -- a fact derived from the artifact cannot be declared wrong, "
            "which is what a column's dtype used to be. Null only for a store written before this field existed, or one whose bytes could not be described."
        ),
    )

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.ParquetAccessGrant:
        """Return temporary credentials for reading this parquet object."""
        del host
        return datalayer.generate_parquet_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.ParquetAccessGrant:
        """Return temporary credentials for reading the object."""
        return self.grant_read_access(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Read the columns the file declares, and mark the store populated.

        This used to set two fields and learn nothing -- the only data store that came away from
        its own upload no wiser. The cost of that was paid one layer up: a caller had to declare
        every column's name and DuckDB type by hand, and `createTableDataset` never compared the
        types to anything, so the declaration could not be wrong and could not be right either.

        A DESCRIBE reads the footer, not the rows, and it happens here because *here* is the one
        moment the bytes are known to be reachable: the client has just finished writing them.
        That is why the check it replaces was opt-in ("the store may not be reachable at create
        time") and why this one needs no opt-out. If S3 is unreachable at this instant the finish
        raises and the client retries the finish -- the bytes are already up, so nothing is lost.
        The same bargain `ZarrStore` and `SparseStore` have always made.

        Raises:
            Exception: Whatever DuckDB raises when the object cannot be read or is not parquet.
        """
        layer = datalayer or Datalayer()
        self.path = self.build_store_path(layer)
        self.columns = [column.model_dump() for column in layer.get_parquet_schema(self)]
        self.populated = True
        self.save(update_fields=["path", "columns", "populated"])


class FabriksStore(DatalayerStore):
    """A fabriks collection -- one octree of surfaces -- stored as a prefix of Parquet files.

    **One artifact, one store.** The writer names files inside its own tree, so the layout the
    format specifies is the layout on disk::

        <prefix>/fabriks.json
        <prefix>/catalog/cells.parquet
        <prefix>/catalog/objects.parquet
        <prefix>/level0/part-00000.parquet

    One grant covers the whole collection, and a reader can glob a level without being handed a
    list of store ids.

    ``level0`` rather than the Hive-style ``level=0`` this once was. Every name in the tree is
    spelled to be *signable*: a path component ends up inside a signed URL, and SigV4 canonicalises
    a request by percent-encoding the path against RFC 3986's unreserved set before signing. ``=``
    is a sub-delimiter, so one signer sends ``%3D`` and another leaves it bare -- two strings to
    sign for one object, and a ``SignatureDoesNotMatch`` that reads like a credentials problem. The
    server hands out a prefix-wide grant and lets the client sign, so this is the client's failure
    to hit; the layout is written down here because this is where the server states it.

    **It is self-describing, and that is the point.** ``fabriks.json`` states the grid and the
    encoding next to the bytes they describe, and :meth:`fill_info` reads them here rather than
    trusting a caller to retype them -- the same move ``ZarrStore`` makes with ``zarr.json``,
    for the same reason: a fact derived from the artifact cannot be declared wrong.

    The manifest is also the **completion marker**. A prefix has no atomic "upload finished"
    flag, so a half-written tree would otherwise register as a store and fail much later, on a
    reader. Writing the manifest last and refusing a store without one converts that into a
    refusal at registration.
    """

    objects: models.Manager["FabriksStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "fabriks"

    # A fabriks store is a *directory*: a manifest, two catalogs and a tree of level partitions.
    # Same consequence as a zarr -- `DeleteObject` on the prefix would delete nothing and report
    # success, so removal needs list + batched delete.
    is_prefix: ClassVar[bool] = True

    spec_version = models.CharField(max_length=64, null=True, blank=True, help_text="The mesh format version declared by the store's manifest.")
    grid = models.JSONField(null=True, blank=True, help_text="The octree grid declared by the manifest: cellSize (in voxels, ordered x/y/z), levels and sortKey.")
    encoding = models.JSONField(null=True, blank=True, help_text="The geometry encoding declared by the manifest: how positions, normals and indices are packed and compressed.")
    axes = models.JSONField(null=True, blank=True, help_text="The axis order the writer states it wrote, used to refuse a collection whose declared axes disagree with its geometry.")
    counts = models.JSONField(null=True, blank=True, help_text="Object and per-level cell counts declared by the manifest. Convenience for budgeting; never authoritative.")
    files = models.JSONField(null=True, blank=True, help_text="The file layout the manifest claims. A claim, not authority -- the prefix listing is what a check reads.")

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.FabriksAccessGrant:
        """Return temporary credentials for reading this fabriks prefix."""
        del host
        return datalayer.generate_fabriks_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.FabriksAccessGrant:
        """Return temporary credentials for reading the object prefix."""
        return self.grant_read_access(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Read the manifest, learn what it says, and mark the store populated.

        Raises:
            FileNotFoundError: If the manifest is missing -- which is also how an interrupted
                upload presents, and why it is refused rather than tolerated.
            ValueError: If the manifest is malformed or declares an unsupported version.
        """
        layer = datalayer or Datalayer()
        self.path = self.build_store_path(layer)
        metadata = layer.get_fabriks_metadata(self)
        self.spec_version = metadata.spec_version
        self.grid = metadata.grid
        self.encoding = metadata.encoding
        self.axes = metadata.axes
        self.counts = metadata.counts
        self.files = metadata.files
        self.populated = True
        self.save(update_fields=["path", "spec_version", "grid", "encoding", "axes", "counts", "files", "populated"])


class KonnektionStore(DatalayerStore):
    """A konnektion collection -- one octree of node/edge networks -- stored as a prefix.

    The graph sibling of :class:`FabriksStore`, with the same shape and for the same reasons::

        <prefix>/konnektion.json
        <prefix>/catalog/cells.parquet
        <prefix>/catalog/objects.parquet
        <prefix>/level0/part-00000.parquet

    One grant covers the whole collection, and a reader can glob a level without being handed a
    list of store ids. ``level0`` rather than ``level=0`` for the signing reason its fabriks
    twin documents: ``=`` is a sub-delimiter, so one signer percent-encodes it and another does
    not, which is two strings to sign for one object.

    **It is self-describing, and that is the point.** ``konnektion.json`` states the grid and the
    encoding next to the bytes they describe, and :meth:`fill_info` reads them here rather than
    trusting a caller to retype them -- a fact derived from the artifact cannot be declared wrong.

    The manifest is also the **completion marker**: a prefix has no atomic "upload finished"
    flag, so a half-written tree would otherwise register and fail much later, on a reader.
    Writing the manifest last and refusing a store without one converts that into a refusal at
    registration.

    **What its encoding says that a mesh store's does not**, and what a reader must act on:
    ``edges`` gives the index arity, which is the only thing separating this geometry from a
    triangle list and produces no error when read wrongly; and ``pruning`` / ``simplification``
    say whether any level is a reduction of another, which for a traced arbor is usually
    ``NONE`` -- konnektion chooses its depth from the data and a single-level collection is the
    normal case rather than a degenerate one.
    """

    objects: models.Manager["KonnektionStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "konnektion"

    # A konnektion store is a *directory*, like a fabriks store and a zarr: `DeleteObject` on
    # the prefix would delete nothing and report success, so removal needs list + batched delete.
    is_prefix: ClassVar[bool] = True

    spec_version = models.CharField(max_length=64, null=True, blank=True, help_text="The network format version declared by the store's manifest.")
    grid = models.JSONField(null=True, blank=True, help_text="The octree grid declared by the manifest: cellSize (in voxels, ordered x/y/z), levels and sortKey.")
    encoding = models.JSONField(null=True, blank=True, help_text="The geometry encoding declared by the manifest: how positions, edges, node ids, radii and ghosts are packed and compressed, and which coarsening operations each level ran.")
    axes = models.JSONField(null=True, blank=True, help_text="The axis order the writer states it wrote, used to refuse a collection whose declared axes disagree with its geometry.")
    counts = models.JSONField(null=True, blank=True, help_text="Object and per-level cell counts declared by the manifest. Convenience for budgeting; never authoritative.")
    files = models.JSONField(null=True, blank=True, help_text="The file layout the manifest claims. A claim, not authority -- the prefix listing is what a check reads.")
    attributes = models.JSONField(null=True, blank=True, help_text="The per-node value columns the manifest declares, each {name, encoding, semantics}. What a network layer's graph colorBys are validated against; null on a store filled before attributes existed, which reads the same as declaring none.")

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.KonnektionAccessGrant:
        """Return temporary credentials for reading this konnektion prefix."""
        del host
        return datalayer.generate_konnektion_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.KonnektionAccessGrant:
        """Return temporary credentials for reading the object prefix."""
        return self.grant_read_access(datalayer)

    def attribute_vocabulary(self) -> list[str]:
        """The per-node value names a graph colouring may use, `radius` included when carried.

        The single source both the options query and the mutation validate against -- the
        offered set and the accepted set stay one set because they are both this list. `radius`
        is not an attribute (it travels in `encoding.radii`) but it is a per-node value all the
        same, so it joins the vocabulary exactly when the encoding says the collection carries
        one.
        """
        names = [str(entry["name"]) for entry in (self.attributes or [])]
        if (self.encoding or {}).get("radii", "NONE") != "NONE":
            names.append("radius")
        return names

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Read the manifest, learn what it says, and mark the store populated.

        Raises:
            FileNotFoundError: If the manifest is missing -- which is also how an interrupted
                upload presents, and why it is refused rather than tolerated.
            ValueError: If the manifest is malformed or declares an unsupported version.
        """
        layer = datalayer or Datalayer()
        self.path = self.build_store_path(layer)
        metadata = layer.get_konnektion_metadata(self)
        self.spec_version = metadata.spec_version
        self.grid = metadata.grid
        self.encoding = metadata.encoding
        self.axes = metadata.axes
        self.counts = metadata.counts
        self.files = metadata.files
        self.attributes = metadata.attributes
        self.populated = True
        self.save(update_fields=["path", "spec_version", "grid", "encoding", "axes", "counts", "files", "attributes", "populated"])


class SparseStore(DatalayerStore):
    """A sparse matrix stored as a zarr *group* behind the S3-backed datalayer.

    A measurement matrix over two enumerations -- objects on one axis, features on the other --
    which at any real size is mostly zeros: a Visium HD run at 2 um is 0.12 % dense, so the same
    facts are ~1 GB stored sparse against 43.8 GB as a dense table of even its top 2 000
    features. Nothing about the shape is specific to transcriptomics; it is equally metabolites
    x cells, proteins x pixels, peaks x cells, or a connectome.

    **The format is anndata's**, deliberately: the group's attributes carry ``encoding-type``
    (``csr_matrix`` or ``csc_matrix``), ``encoding-version`` and ``shape``, and it holds three
    1-D arrays -- ``data``, ``indices``, ``indptr``. That is what scanpy, spatialdata,
    Seurat-via-h5ad and CELLxGENE already write, so a store round-trips to the rest of the field
    for free and the encoding is a fact read off the artifact rather than one a caller states.

    **Which axis ``indptr`` indexes is the whole content of the encoding**, and it decides which
    question the store answers in one contiguous read: ``csr_matrix`` over (objects, features)
    makes one object contiguous, ``csc_matrix`` makes one feature contiguous. Ask the other one
    and there is no range to read -- the wanted entries are one per slice across the whole
    ``indices`` array. Measured on the 16 um Visium HD matrix: one object is 2.2 ms from the
    object-major store and 1 777 ms from the feature-major one, having scanned 352 MB. A dataset
    that must answer both questions therefore holds two of these.

    **It lives in the zarr bucket**, because it *is* a zarr tree -- ``node_type: group``,
    ``zarr_format: 3``. A bucket of its own would need the mikro config, the MinIO bucket, a
    Caddy route in both site blocks and a `get_buckets()` entry in `arkitekt-server`, for no
    fact the zarr bucket does not already carry.

    Distinct from :class:`ZarrStore` all the same, and not a flag on it: ``get_zarr_metadata``
    refuses anything but ``node_type: "array"`` by name, and ``ZarrMetadata.node_type`` is typed
    ``Literal["array"]``. A group has no single shape, dtype or chunking to record -- it has
    three of each -- so the two describe genuinely different artifacts.
    """

    objects: models.Manager["SparseStore"]  # type: ignore[assignment]

    bucket_key: ClassVar[str] = "zarr"

    # Three arrays plus the group's own metadata: a prefix, exactly as a zarr array is.
    is_prefix: ClassVar[bool] = True

    spec = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        help_text="The version of the `sporadik` block this store was accepted under. A spec selects how every byte in the prefix is read, so an unknown one is refused rather than guessed at",
    )
    shape = models.JSONField(null=True, blank=True, help_text="The shape of the matrix, as the root block declares it and every layout agrees. Two axes")
    layouts = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The stored layouts, one entry per `layouts/<encoding>` child: its path, `encoding`, `encoding-version`, `nnz`, `dtype` and the chunk length of each of `data`, "
            "`indices` and `indptr`. Everything that differs between layouts, and nothing that does not. The chunking is here because it decides the read cost: `indptr` names "
            "exactly which bytes a lookup wants and a chunk is the granularity at which they can be fetched, so the two have to agree -- measured on a 16 um matrix, one slice "
            "costs 0.95 ms at 32 768-element chunks and 23.55 ms at 4 Mi ones, the same bytes a hundred times over-read"
        ),
    )

    def grant_read_access(self, datalayer: Datalayer, host: str | None = None) -> base_models.SparseAccessGrant:
        """Return temporary credentials for reading this sparse prefix."""
        del host
        return datalayer.generate_sparse_access_grant(self)

    def get_access_grant(self, datalayer: Datalayer) -> base_models.SparseAccessGrant:
        """Return temporary credentials for reading the object prefix."""
        return self.grant_read_access(datalayer)

    def fill_info(self, datalayer: Datalayer | None = None) -> None:
        """Read what the group says about itself, and mark the store populated.

        The refusals are the point. A prefix has no atomic "upload finished" flag, so a group
        missing its ``encoding-type``, missing one of its three arrays, or carrying an
        ``indptr`` whose length disagrees with the declared shape is exactly the shape an
        interrupted upload takes. Catching it here turns a much later failure in a reader into
        a refusal at registration -- the same move ``FabriksStore`` makes with its manifest.

        And unlike the earlier version of this store, it is **not** weaker than the fabriks
        check any more. The writer lands the root block last, in one object, after every chunk;
        a prefix without it is an upload that died. That matters more than it sounds: zarr
        writes an array's ``zarr.json`` ahead of its chunks and substitutes the fill value for a
        chunk it cannot fetch, so a torn prefix used to pass every check here, report the right
        ``nnz``, and hand a reader back the right *number* of values, every one of them zero.

        Raises:
            FileNotFoundError: If the group's metadata is missing.
            ValueError: If it is malformed, or the arrays contradict what it declares.
        """
        layer = datalayer or Datalayer()
        self.path = self.build_store_path(layer)
        metadata = layer.get_sparse_metadata(self)
        self.spec = metadata.spec
        self.shape = metadata.shape
        self.layouts = [layout.model_dump() for layout in metadata.layouts]
        self.populated = True
        self.save(update_fields=["path", "spec", "shape", "layouts", "populated"])

    @property
    def encodings(self) -> list[str]:
        """The encodings this store holds, in the order the block named them."""
        return [str(layout.get("encoding")) for layout in (self.layouts or [])]

    def layout_indexing(self, axis: int) -> dict | None:
        """The layout whose one contiguous read selects along ``axis``, if this store has it.

        The question every surface asks before offering itself: a colouring needs the layout
        indexing the *feature* axis, a per-object lookup the one indexing the *object* axis, and
        a store holding only one of them offers only one of those capabilities. Asking for the
        other is not slow, it is a scan of everything.

        The axis is derived from each layout's own encoding and never stored beside it -- two
        statements of one fact are two things to drift.
        """
        for layout in self.layouts or []:
            if SPARSE_INDEXED_AXIS.get(str(layout.get("encoding"))) == axis:
                return layout
        return None

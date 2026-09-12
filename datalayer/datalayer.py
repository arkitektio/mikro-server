import json
import logging
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional, TypeVar, cast

import boto3
from botocore.config import Config
from django.conf import settings
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from datalayer import base_models
from datalayer import fabriks as fabriks_format
from datalayer import konnektion as konnektion_format

if TYPE_CHECKING:
    from datalayer import models

logger = logging.getLogger(__name__)


AccessGrant = base_models.AccessGrant
StoreModel = TypeVar("StoreModel", bound="models.DatalayerStore")

#: STS refuses a shorter session, and a refused `AssumeRole` used to fall through to the
#: service's permanent key -- so a caller asking for five minutes silently got forever.
#: Clamping here keeps `expires_in` a preference rather than a way out.
MIN_SESSION_DURATION_SECONDS = 900

#: The other end of the same clamp. `expires_in` reaches this from a GraphQL input, so a
#: caller must not be able to pick a duration STS will reject -- refusing at both ends is a
#: bound, refusing at one is a way to turn a grant into an error.
MAX_SESSION_DURATION_SECONDS = 43200


# Context variable for the datalayer instance
datalayer: ContextVar["Datalayer"] = ContextVar("datalayer")


class BucketConfig(BaseModel):
    """Resolved bucket configuration for one datalayer store type."""

    bucket: str = Field(..., validation_alias=AliasChoices("PATH", "path"))
    subpath: str | None = Field(None, validation_alias=AliasChoices("SUBPATH", "subpath"))
    default_max_bytes: int = Field(
        100 * 1024 * 1024,
        validation_alias=AliasChoices("DEFAULT_MAX_BYTES", "default_max_bytes"),
    )

    model_config = ConfigDict(populate_by_name=True)


class DatalayerConfig(BaseModel):
    """Runtime configuration loaded from ``settings.DATALAYER``."""

    role_arn: str | None = Field(None, validation_alias=AliasChoices("ROLE_ARN", "role_arn"))
    external_id: str | None = Field(None, validation_alias=AliasChoices("EXTERNAL_ID", "external_id"))
    session_duration_seconds: int = Field(
        3600,
        validation_alias=AliasChoices("SESSION_DURATION_SECONDS", "session_duration_seconds"),
    )
    allow_unscoped_fallback: bool = Field(
        False,
        validation_alias=AliasChoices("ALLOW_UNSCOPED_FALLBACK", "allow_unscoped_fallback"),
        description="Hand out this service's own permanent credentials when no scoped session can be issued. Development only -- it makes every grant unlimited in scope and lifetime.",
    )
    access_key: str | None = Field(
        None,
        validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "aws_access_key_id", "access_key"),
    )
    secret_key: str | None = Field(
        None,
        validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "aws_secret_access_key", "secret_key"),
    )
    session_token: str | None = Field(
        None,
        validation_alias=AliasChoices("AWS_SESSION_TOKEN", "aws_session_token", "session_token"),
    )
    host: str | None = Field(
        None,
        validation_alias=AliasChoices("AWS_S3_ENDPOINT_URL", "aws_s3_endpoint_url", "host"),
    )
    region: str = Field(
        "us-east-1",
        validation_alias=AliasChoices("AWS_S3_REGION_NAME", "aws_s3_region_name", "region"),
    )
    port: int | None = Field(None, validation_alias=AliasChoices("AWS_S3_PORT", "aws_s3_port", "port"))
    protocol: str = Field(
        "https",
        validation_alias=AliasChoices("AWS_S3_URL_PROTOCOL", "aws_s3_url_protocol", "protocol"),
    )

    bigfile: Optional[BucketConfig] = None
    media: Optional[BucketConfig] = None
    zarr: Optional[BucketConfig] = None
    parquet: Optional[BucketConfig] = None
    fabriks: Optional[BucketConfig] = None
    konnektion: Optional[BucketConfig] = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def endpoint_url(self) -> Optional[str]:
        """Construct the full endpoint URL if host and port are provided."""
        if not self.host:
            return None
        if self.port is None:
            return f"{self.protocol}://{self.host}"
        return f"{self.protocol}://{self.host}:{self.port}"


class Datalayer:
    """Generate temporary S3 grants and manage datalayer-backed stores."""

    def __init__(self) -> None:
        """Initialize storage clients.

        The datalayer reads all connection and bucket configuration from
        ``settings.DATALAYER``.
        """
        self.config = DatalayerConfig(**getattr(settings, "DATALAYER", {}))

        client_kwargs = {
            "aws_access_key_id": self.config.access_key,
            "aws_secret_access_key": self.config.secret_key,
            "endpoint_url": self.config.endpoint_url,
            "region_name": self.config.region,
            "config": Config(signature_version="s3v4"),
        }
        if self.config.session_token:
            client_kwargs["aws_session_token"] = self.config.session_token

        self._s3 = boto3.client("s3", **client_kwargs)
        self._sts = boto3.client("sts", **client_kwargs)

    def get_bucket_config(self, bucket_key: str) -> BucketConfig:
        """Return bucket configuration for a known datalayer store.

        Args:
            bucket_key: Logical store type such as ``media`` or ``zarr``.

        Returns:
            The resolved bucket configuration.

        Raises:
            ValueError: If the bucket key is not configured.
        """
        conf = getattr(self.config, bucket_key, None)
        if conf is not None:
            return conf

        else:
            raise ValueError(f"Service/Bucket '{bucket_key}' not configured in datalayer.")

    def build_object_key(self, bucket_key: str, object_path: str) -> str:
        """Build the concrete S3 key for a logical object path.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.

        Returns:
            The S3 object key including any configured bucket subpath.
        """
        conf = self.get_bucket_config(bucket_key)
        if conf.subpath:
            return f"{conf.subpath.rstrip('/')}/{object_path.lstrip('/')}"
        return object_path.lstrip("/")

    def build_store_path(self, bucket_key: str, object_path: str) -> str:
        """Build the canonical S3 URI stored in the database.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.

        Returns:
            A canonical ``s3://`` URI.
        """
        conf = self.get_bucket_config(bucket_key)
        return f"s3://{conf.bucket}/{self.build_object_key(bucket_key, object_path)}"

    def _parse_s3_path(self, path: str) -> tuple[str, str]:
        """Parse a canonical S3 URI into bucket and key parts.

        Args:
            path: Canonical ``s3://`` URI.

        Returns:
            The bucket name and object key prefix.

        Raises:
            ValueError: If the path is not a valid ``s3://`` URI.
        """
        if not path.startswith("s3://"):
            raise ValueError(f"Invalid S3 path: {path}")

        bucket_name, key = path.removeprefix("s3://").split("/", 1)
        return bucket_name, key

    def _new_key(self) -> str:
        """Generate a new opaque storage key.

        Returns:
            A random hex key suitable for store creation.
        """
        return uuid.uuid4().hex

    def _session_duration(self, expires_in: int | None = None) -> int:
        """Resolve a credential lifetime.

        Args:
            expires_in: Optional explicit duration override in seconds.

        Returns:
            The requested duration or the configured default, clamped to what STS accepts.
        """
        requested = expires_in or self.config.session_duration_seconds
        return min(max(requested, MIN_SESSION_DURATION_SECONDS), MAX_SESSION_DURATION_SECONDS)

    def get_fabriks_metadata(self, store: "models.FabriksStore") -> base_models.FabriksMetadata:
        """Read a fabriks store's manifest.

        One GET of one small object, at registration only -- the same shape as
        :meth:`get_zarr_metadata` and for the same reason: the artifact describes itself, so
        the server reads rather than asks.

        The parsing lives in :mod:`datalayer.fabriks`, which reads the wire format with `json`
        and no dependency on the `fabriks` package that writes it. See that module for why the
        server is a second implementation of the format rather than a user of the first.

        Args:
            store: Fabriks store whose prefix should be inspected.

        Returns:
            The parsed manifest.

        Raises:
            FileNotFoundError: If ``fabriks.json`` is missing.
            ValueError: If the manifest is malformed or its version unsupported.
        """
        path = store.path or self.build_store_path("fabriks", store.key)
        bucket_name, prefix = self._parse_s3_path(path)
        manifest_key = prefix.rstrip("/") + "/" + fabriks_format.MANIFEST_NAME
        location = f"s3://{bucket_name}/{manifest_key}"

        logger.debug("Fetching fabriks manifest from bucket '%s' with key '%s'", bucket_name, manifest_key)
        try:
            manifest_file = self._s3.get_object(Bucket=bucket_name, Key=manifest_key)
        except Exception as exc:
            # A missing manifest is the ordinary shape of an interrupted upload, because the
            # writer lands it last. Naming that is more useful than "not found".
            raise FileNotFoundError(
                f"No `{fabriks_format.MANIFEST_NAME}` at {location}, so this prefix is not a readable fabriks store. A writer uploads the manifest last, so an interrupted run leaves exactly this."
            ) from exc

        manifest = fabriks_format.parse_manifest(manifest_file["Body"].read(), where=f"The fabriks manifest at {location}")

        return base_models.FabriksMetadata(
            spec_version=manifest.spec_version,
            grid=manifest.grid,
            encoding=manifest.encoding,
            axes=manifest.axes,
            counts=manifest.counts,
            files=manifest.files,
        )

    def parquet_source(self, store: "models.ParquetStore") -> str:
        """The ``s3://`` URL DuckDB reads a parquet store through."""
        return f"s3://{self.get_bucket_config('parquet').bucket}/{store.key}"

    def get_parquet_schema(self, store: "models.ParquetStore") -> list[base_models.ParquetColumn]:
        """Read the columns a parquet file declares, in file order.

        A ``DESCRIBE`` and nothing else: it reads the footer's schema, never a row. That is the
        same line :meth:`get_zarr_metadata` and :meth:`get_sparse_metadata` hold -- registration
        asks a store what it is, and asks nothing about what it contains. A picker that wanted a
        column's distinct values was refused here for exactly that reason; the client already
        holds an access grant and can scan it locally at its own expense.

        Args:
            store: Parquet store whose file should be described.

        Returns:
            One entry per column, carrying the name and the DuckDB type the file declares.
        """
        # Deferred: duckdb is a heavy import and only the parquet path needs it.
        from datalayer.duck import get_current_duck

        # Bound to a name, not inlined. `sql()` returns a lazy relation; a `get_current_duck()`
        # whose only reference is the temporary in that expression is collected as soon as the
        # relation is built, taking its duckdb connection with it, and `fetchall` then raises
        # "Connection has already been closed". Measured against 24 live stores.
        duck = get_current_duck()
        rows = duck.sql(f"DESCRIBE SELECT * FROM read_parquet('{self.parquet_source(store)}');").fetchall()
        return [base_models.ParquetColumn(name=row[0], type=row[1], nullable=str(row[2]).upper() == "YES") for row in rows]

    def get_sparse_metadata(self, store: "models.SparseStore") -> base_models.SparseMetadata:
        """Read what a sporadik store states about itself: its block, and every layout it names.

        The GET half. Every rule lives in :mod:`datalayer.sporadik`, which parses the wire format
        with `json` and nothing else -- the same split :meth:`get_fabriks_metadata` makes, and for
        the same reason: the rules are the interesting part and are worth testing without a bucket
        behind them, while fetching a handful of small objects is not.

        A handful of small GETs at registration only: the root's ``zarr.json`` for the block, then
        per layout its own plus one per array. Nothing here opens a chunk.

        Args:
            store: Sparse store whose prefix should be inspected.

        Returns:
            The parsed store metadata: the spec, the shape, and one entry per layout.

        Raises:
            FileNotFoundError: If an object the block names is missing.
            ValueError: If the prefix contradicts what the block declares.
        """
        from datalayer import sporadik

        path = store.path or self.build_store_path("zarr", store.key)
        bucket_name, prefix = self._parse_s3_path(path)
        root = prefix.rstrip("/")

        def fetch(suffix: str) -> bytes:
            key = f"{root}/{suffix}zarr.json" if suffix else f"{root}/zarr.json"
            try:
                return self._s3.get_object(Bucket=bucket_name, Key=key)["Body"].read()
            except Exception as exc:
                raise FileNotFoundError(
                    f"No zarr metadata at s3://{bucket_name}/{key}, so this prefix is not a readable sporadik store. "
                    f"A store is a group carrying a `{sporadik.BLOCK_KEY}` block and holding its layouts under "
                    f"`{sporadik.LAYOUTS_GROUP}/`; an interrupted upload leaves exactly this."
                ) from exc

        where = f"s3://{bucket_name}/{root}"
        reading = sporadik.parse_root(fetch(""), where=where)

        # The block is fully checked by `parse_root` before a single layout object is fetched: an
        # entry naming an axis the array does not have is wrong whatever is behind it, and four GETs
        # is a lot to spend learning nothing.
        layouts = [
            base_models.SparseLayoutMetadata(
                **vars(
                    sporadik.parse_layout(
                        entry,
                        {"": fetch(f"{entry.path}/"), **{name: fetch(f"{entry.path}/{name}/") for name in sporadik.ARRAYS}},
                        reading.shape,
                        where=where,
                    )
                )
            )
            for entry in reading.entries
        ]

        return base_models.SparseMetadata(spec=reading.spec, shape=reading.shape, layouts=layouts)

    def get_zarr_metadata(self, store: "models.ZarrStore") -> base_models.ZarrMetadata:
        """Retrieve structured metadata for a Zarr store.

        Args:
            store: Zarr store whose object prefix should be inspected.

        Returns:
            Parsed Zarr metadata for the discovered array.

        Raises:
            FileNotFoundError: If the Zarr v3 metadata file is missing.
            ValueError: If the discovered metadata is malformed.
        """
        path = store.path or self.build_store_path("zarr", store.key)
        bucket_name, prefix = self._parse_s3_path(path)
        metadata_key = prefix.rstrip("/") + "/zarr.json"

        logger.debug("Fetching Zarr metadata from bucket '%s' with key '%s'", bucket_name, metadata_key)
        try:
            zarr_file = self._s3.get_object(Bucket=bucket_name, Key=metadata_key)
        except Exception as exc:
            raise FileNotFoundError(f"Could not find Zarr v3 metadata for store {store.pk or store.key}.") from exc

        body = zarr_file["Body"].read().decode("utf-8")
        try:
            metadata = json.loads(body)
        except json.JSONDecodeError as exc:
            # An empty body is the common shape of this: the object exists (so the get above
            # succeeded) but the writer never finished, or the key is a zero-byte placeholder.
            # A bare decoder error names neither the store nor the object it was reading.
            raise ValueError(f"The Zarr metadata at s3://{bucket_name}/{metadata_key} is not valid JSON ({exc}). It is {len(body)} bytes and starts: {body[:80]!r}") from exc

        if metadata.get("zarr_format") == 2:
            raise ValueError("Zarr v2 is not supported. Only Zarr v3 stores are supported.")
        if metadata.get("node_type") != "array":
            raise ValueError("Only Zarr v3 ARRAY stores are supported. You may be trying to load metadata for a Zarr group or a non-Zarr object.")

        shape = metadata.get("shape")
        chunk_shape = metadata.get("chunk_grid", {}).get("configuration", {}).get("chunk_shape")
        if shape is None or chunk_shape is None:
            raise ValueError("Malformed zarr.json metadata: missing shape or chunk shape.")

        parsed = base_models.ZarrMetadata(
            zarr_format=metadata["zarr_format"],
            node_type=metadata["node_type"],
            shape=shape,
            data_type=metadata.get("data_type"),
            chunk_grid=metadata.get("chunk_grid"),
            chunk_key_encoding=metadata.get("chunk_key_encoding"),
            fill_value=metadata.get("fill_value"),
            codecs=metadata.get("codecs") or [],
            attributes=metadata.get("attributes"),
            storage_transformers=metadata.get("storage_transformers"),
            dimension_names=metadata.get("dimension_names"),
        )

        try:
            parsed.validate_sharding()
        except ValueError as exc:
            raise ValueError(f"The Zarr metadata at s3://{bucket_name}/{metadata_key} declares an unreadable sharding layout: {exc}") from exc

        return parsed

    @staticmethod
    def prefix_bucket_keys() -> frozenset[str]:
        """The logical buckets whose stores are prefixes rather than single objects.

        Derived from the store classes -- every ``DatalayerStore`` subclass declares its
        ``bucket_key`` and its ``is_prefix`` -- rather than from a literal here. Before this,
        the grant builder tested ``bucket_key == "zarr"`` while deletion tested ``is_prefix``,
        so the two halves of "this store is a directory" were stated in different places and
        could disagree. A new prefix store type that set only ``is_prefix`` deleted correctly
        and was granted credentials that could neither list nor write its own children -- and
        nothing raised, because an unscoped grant works anyway wherever no role is assumed.

        Imported inside the function: ``datalayer.models`` imports this module.
        """
        from datalayer import models

        return frozenset(subclass.bucket_key for subclass in models.DatalayerStore.__subclasses__() if subclass.is_prefix and subclass.bucket_key)

    def _object_resources(self, bucket_key: str, object_path: str) -> tuple[str, list[str], bool]:
        """Resolve S3 resources covered by a grant.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.

        Returns:
            A tuple containing the full object key, the covered resource paths,
            and whether bucket listing permission is also required.
        """
        full_key = self.build_object_key(bucket_key, object_path)
        if bucket_key in self.prefix_bucket_keys():
            prefix = full_key.rstrip("/")
            return full_key, [prefix, f"{prefix}/*"], True
        return full_key, [full_key], False

    def _build_policy(self, bucket_name: str, bucket_key: str, object_path: str, action: str) -> dict[str, object]:
        """Build an inline session policy for an assumed role.

        Args:
            bucket_name: Physical S3 bucket name.
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.
            action: Requested action such as ``read`` or ``upload``.

        Returns:
            An IAM policy document scoped to the requested object resources.
        """
        _, resources, allow_list = self._object_resources(bucket_key, object_path)
        s3_resources = [f"arn:aws:s3:::{bucket_name}/{resource}" for resource in resources]
        action_map = {
            "read": ["s3:GetObject"],
            "upload": ["s3:PutObject", "s3:AbortMultipartUpload"],
            "delete": ["s3:DeleteObject"],
        }
        # A prefix writer needs more than PutObject: it reads back and rewrites objects inside
        # its own tree as it goes (a zarr rewrites `zarr.json`; a fabriks store writes its
        # manifest after its parts), and a failed multipart leaves garbage only DeleteObject
        # can clear.
        if bucket_key in self.prefix_bucket_keys() and action == "upload":
            action_map["upload"] = [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:AbortMultipartUpload",
            ]

        statements: list[dict[str, object]] = [
            {
                "Effect": "Allow",
                "Action": action_map[action],
                "Resource": s3_resources,
            }
        ]

        if allow_list:
            full_key, _, _ = self._object_resources(bucket_key, object_path)
            prefix = full_key.rstrip("/")
            statements.append(
                {
                    "Effect": "Allow",
                    # `s3:ListBucket` and nothing else. MinIO validates the inline policy when
                    # the role is assumed and rejects the whole document if a condition key is
                    # not valid for every action it covers -- `s3:GetBucketLocation` with an
                    # `s3:prefix` condition fails with "unsupported condition keys
                    # '[s3:prefix]'". That refusal landed in the fallback below, so adding a
                    # harmless-looking action here cost every prefix store its scoping. If a
                    # reader ever turns out to need `GetBucketLocation`, it goes in a separate
                    # statement with no `Condition` -- not back into this one.
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}"],
                    "Condition": {
                        "StringLike": {
                            "s3:prefix": [prefix, f"{prefix}/*"],
                        }
                    },
                }
            )

        return {"Version": "2012-10-17", "Statement": statements}

    def _build_general_read_policy(self, bucket_name: str, bucket_key: str) -> dict[str, object]:
        """Build a read-only session policy covering one whole datalayer bucket.

        Deliberately weaker than :meth:`_build_policy`: a *general* grant is not asked for one
        store, so there is no key to scope it to. It is still a real bound -- read-only, one
        bucket -- where these grants previously carried no policy at all and so inherited the
        service account's cluster-wide ``readwrite`` for their whole lifetime.

        Per-organization scoping, which the ``requestGeneral*Access`` callers actually want,
        is not expressible here while stores are keyed by an opaque uuid with no
        per-organization prefix. That is a change to the key layout, not to this policy.

        Args:
            bucket_name: Physical S3 bucket name.
            bucket_key: Logical datalayer store type.

        Returns:
            An IAM policy document permitting reads anywhere in the one bucket.
        """
        statements: list[dict[str, object]] = [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            }
        ]

        # Same reason as in `_build_policy`: a prefix store is a directory, and a reader that
        # cannot list it cannot discover its chunks. Unconditional here because a general
        # grant has no one prefix to condition on.
        if bucket_key in self.prefix_bucket_keys():
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}"],
                }
            )

        return {"Version": "2012-10-17", "Statement": statements}

    def _assume_role(self, action: str, duration: int, policy: dict[str, object] | None) -> tuple[str, str, str]:
        """Mint temporary credentials by assuming the configured role.

        The only way this codebase obtains scoped credentials. There is deliberately no
        ``get_session_token`` path any more: MinIO does not route that STS action at all
        (``InvalidParameterValue: Unsupported action GetSessionToken``), so it could only ever
        fail, and it failed *into* the unscoped fallback -- which is why a deployment could run
        for a long time handing out permanent keys with nothing in the logs.

        Args:
            action: Requested action, used to label the STS session.
            duration: Credential lifetime in seconds.
            policy: Inline session policy, or ``None`` for a session bounded only by the
                service account's own policy.

        Returns:
            A tuple of access key, secret key, and session token.

        Raises:
            RuntimeError: If no role is configured, or if STS refuses the request.
        """
        if not self.config.role_arn:
            raise RuntimeError("`DATALAYER.role_arn` is unset, so there is no role to assume and no scoped credentials can be issued. Against MinIO the value is ignored -- any ARN-shaped string will do -- and the session is scoped by the inline policy alone.")

        assume_role_kwargs: dict[str, object] = {
            "RoleArn": self.config.role_arn,
            "RoleSessionName": f"mikro-{action}-{uuid.uuid4().hex[:8]}",
            "DurationSeconds": duration,
        }
        if policy is not None:
            assume_role_kwargs["Policy"] = json.dumps(policy)
        if self.config.external_id:
            assume_role_kwargs["ExternalId"] = self.config.external_id

        try:
            credentials = self._sts.assume_role(**assume_role_kwargs)["Credentials"]
        except Exception as exc:
            raise RuntimeError(f"STS refused to issue credentials for a `{action}` session ({exc}).") from exc

        return (
            credentials["AccessKeyId"],
            credentials["SecretAccessKey"],
            credentials["SessionToken"],
        )

    def _unscoped_fallback(self, what: str, cause: Exception) -> tuple[str, str, str]:
        """Hand back this service's own permanent credentials, if that is explicitly allowed.

        This used to be the unconditional behaviour on *any* STS failure, and it is why the
        scoping machinery above was inert: a grant that could not be scoped was indistinguishable
        from one that was, because both returned usable credentials and neither logged. The
        credentials handed out here are the service account's -- cluster-wide ``readwrite``,
        no expiry -- so failing is almost always the better answer.

        Args:
            what: Description of the grant being issued, for the operator reading the log.
            cause: The failure that led here.

        Returns:
            The configured long-lived credentials.

        Raises:
            RuntimeError: Unless ``DATALAYER.allow_unscoped_fallback`` is set.
        """
        if not self.config.allow_unscoped_fallback:
            raise RuntimeError(f"Could not issue {what}. Refusing to fall back to this service's own permanent credentials, which are unscoped and never expire; set `DATALAYER.allow_unscoped_fallback` to accept that in development.") from cause

        logger.warning("Issuing %s with this service's own permanent credentials because no session could be minted (%s). The client receives an unscoped, non-expiring key.", what, cause)
        return (
            self.config.access_key or "",
            self.config.secret_key or "",
            self.config.session_token or "",
        )

    def _issue_temporary_credentials(self, bucket_key: str, object_path: str, action: str, expires_in: int) -> tuple[str, str, str]:
        """Issue temporary credentials scoped to one store's objects.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.
            action: Requested action such as ``read`` or ``upload``.
            expires_in: Requested credential lifetime in seconds.

        Returns:
            A tuple of access key, secret key, and session token.

        Raises:
            RuntimeError: If no scoped session could be issued and the unscoped fallback is off.
        """
        conf = self.get_bucket_config(bucket_key)
        duration = self._session_duration(expires_in)
        policy = self._build_policy(conf.bucket, bucket_key, object_path, action)

        try:
            return self._assume_role(action, duration, policy)
        except Exception as exc:
            return self._unscoped_fallback(f"a `{action}` grant on {bucket_key} store {object_path}", exc)

    def _issue_temporary_user_access_credentials(self, bucket_key: str, organization_id: str, user_id: str, expires_in: int) -> tuple[str, str, str]:
        """Issue temporary read credentials covering a whole datalayer bucket.

        Args:
            bucket_key: Logical datalayer store type.
            organization_id: The organization ID.
            user_id: The user ID.
            expires_in: Requested credential lifetime in seconds.

        Returns:
            A tuple of access key, secret key, and session token.

        Raises:
            RuntimeError: If no scoped session could be issued and the unscoped fallback is off.
        """
        conf = self.get_bucket_config(bucket_key)
        duration = self._session_duration(expires_in)
        policy = self._build_general_read_policy(conf.bucket, bucket_key)

        try:
            return self._assume_role("read", duration, policy)
        except Exception as exc:
            return self._unscoped_fallback(f"a general read grant on {bucket_key} for organization {organization_id}", exc)

    def generate_media_upload_grant(self, organization_id: int, input: base_models.RequestMediaUploadInput) -> base_models.MediaUploadGrant:
        """Create a media store and a presigned PUT URL for upload.

        The presigned URL is generated against the internal S3 endpoint, then
        the base URL is rewritten to match the client-provided addressing.
        """
        from datalayer import models

        conf = self.get_bucket_config("media")
        key = self._new_key()
        store = models.MediaStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("media", key),
            key=key,
            bucket="media",
            original_file_name=input.original_file_name,
            content_type=input.content_type,
            max_bytes=input.file_size or conf.default_max_bytes,
        )

        ttl = self._session_duration()

        access_key, secret_key, session_token = self._issue_temporary_credentials("media", store.key, "upload", ttl)
        full_key = self.build_object_key("media", store.key)

        return base_models.MediaUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("media", store.key),
            expires_in=ttl,
            datalayer="media",
            max_bytes=input.file_size or conf.default_max_bytes,
            original_file_name=store.original_file_name,
            upload_file_name=store.get_upload_file_name(),
            upload_content_type=store.content_type,
            upload_form_field="file",
            store=str(store.pk),
        )

    def generate_bigfile_upload_grant(self, organization_id: int, input: base_models.RequestBigFileUploadInput) -> base_models.BigFileUploadGrant:
        """Create a big file store and upload grant."""
        from datalayer import models

        conf = self.get_bucket_config("bigfile")
        key = self._new_key()
        store = models.BigFileStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("bigfile", key),
            key=key,
            bucket="bigfile",
            original_file_name=input.original_file_name,
            content_type=input.content_type,
            max_bytes=input.file_size or conf.default_max_bytes,
        )

        ttl = self._session_duration()

        access_key, secret_key, session_token = self._issue_temporary_credentials("bigfile", store.key, "upload", ttl)
        full_key = self.build_object_key("bigfile", store.key)

        return base_models.BigFileUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("bigfile", store.key),
            expires_in=ttl,
            datalayer="bigfile",
            max_bytes=input.file_size or conf.default_max_bytes,
            original_file_name=store.original_file_name,
            upload_file_name=store.get_upload_file_name(),
            upload_content_type=store.content_type,
            upload_form_field="file",
            store=str(store.pk),
        )

    def _build_sparse_upload_grant(self, store: "models.SparseStore") -> base_models.SparseUploadGrant:
        """Issue an upload grant for a sparse store that already exists.

        Split out so credentials can be reissued for the same store without minting a second
        one, exactly as :meth:`_build_zarr_upload_grant` is. Everything is derived from the
        store row, so a reissued grant addresses the same prefix -- only the credentials and
        their expiry are new.
        """
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("zarr", store.key, "upload", ttl)

        return base_models.SparseUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=self.build_object_key("zarr", store.key),
            path=self.build_store_path("zarr", store.key),
            expires_in=ttl,
            max_bytes=store.max_bytes if store.max_bytes is not None else conf.default_max_bytes,
            upload_file_name=store.get_upload_file_name(),
            store=str(store.pk),
        )

    def generate_sparse_upload_grant(self, organization_id: int, input: base_models.RequestSparseUploadInput) -> base_models.SparseUploadGrant:
        """Create a sparse store and a prefix upload grant.

        The grant covers the whole prefix and permits read-back and delete inside it, because a
        sparse store is written as a tree: three arrays, each in chunks. Nothing about the
        matrix is taken from the caller -- the group states it, and ``fill_info`` reads it when
        the upload is finished.

        In the zarr bucket, because a sparse matrix *is* a zarr tree. See
        :class:`~datalayer.models.SparseStore`.
        """
        from datalayer import models

        conf = self.get_bucket_config("zarr")
        key = self._new_key()
        store = models.SparseStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("zarr", key),
            key=key,
            bucket="zarr",
            max_bytes=conf.default_max_bytes,
        )
        return self._build_sparse_upload_grant(store)

    def refresh_sparse_upload_grant(self, organization_id: int, store_id: str) -> base_models.SparseUploadGrant:
        """Reissue upload credentials for a sparse store whose upload is still in flight.

        The same problem :meth:`refresh_zarr_upload_grant` solves, and a sparse matrix meets it
        sooner: the 16 um Visium HD matrix is 88 M nonzeros across three arrays, and the 2 um
        one is 128 M. A write that outlives its session token otherwise dies partway through.

        Refuses a store that is already populated: those bytes are referenced, and handing out
        write credentials for them is an overwrite path, not a resumption. A store finished with
        ``valid=False`` is not populated and stays refreshable, which is the retry case.
        """
        from datalayer import models

        store = models.SparseStore.objects.get(id=store_id, organization_id=organization_id)
        if store.populated:
            raise ValueError(
                f"Sparse store {store_id} is already populated, so its upload is finished and its bytes are referenced. "
                "Reissuing write credentials for it would be an overwrite, not a resumption -- upload a new store instead."
            )
        return self._build_sparse_upload_grant(store)

    def finish_sparse_upload(self, organization_id: int, input: base_models.FinishSparseUploadInput) -> "models.SparseStore":
        """Mark a sparse upload complete, which is when its group metadata is read.

        Not bookkeeping: ``fill_info`` fetches the group's attributes and each array's, and
        refuses the store if the encoding is absent, an array is missing, or ``indptr``
        contradicts the declared shape -- so an interrupted upload fails here rather than
        surviving as a store a reader discovers is broken.
        """
        from datalayer import models

        return self._finish_store_upload(models.SparseStore, organization_id, input.store_id, input.valid)

    def generate_sparse_access_grant(self, store: "models.SparseStore") -> base_models.SparseAccessGrant:
        """Return read credentials covering a sparse store's whole prefix."""
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("zarr", store.key, "read", ttl)

        return base_models.SparseAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=self.build_object_key("zarr", store.key),
            path=store.path or self.build_store_path("zarr", store.key),
            expires_in=ttl,
            store=str(store.pk),
        )

    def generate_general_sparse_access_grant(self, organization_id: str, user_id: str) -> base_models.GeneralSparseAccessGrant:
        """Return organization-wide read credentials for sparse stores."""
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("zarr", organization_id, user_id, ttl)

        return base_models.GeneralSparseAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            expires_in=ttl,
        )

    def generate_fabriks_upload_grant(self, organization_id: int, input: base_models.RequestFabriksUploadInput) -> base_models.FabriksUploadGrant:
        """Create a fabriks store and a prefix upload grant.

        The grant covers the whole prefix and permits read-back and delete inside it, because
        a fabriks store is written as a tree: parts first, manifest last. Nothing about the
        meshes is taken from the caller -- the manifest states it, and ``fill_info`` reads it
        when the upload is finished.
        """
        from datalayer import models

        conf = self.get_bucket_config("fabriks")
        key = self._new_key()
        store = models.FabriksStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("fabriks", key),
            key=key,
            bucket="fabriks",
            max_bytes=conf.default_max_bytes,
        )

        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("fabriks", store.key, "upload", ttl)
        full_key = self.build_object_key("fabriks", store.key)

        return base_models.FabriksUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("fabriks", store.key),
            expires_in=ttl,
            max_bytes=conf.default_max_bytes,
            upload_file_name=store.get_upload_file_name(),
            store=str(store.pk),
        )

    def finish_fabriks_upload(self, organization_id: int, input: base_models.FinishFabriksUploadInput) -> "models.FabriksStore":
        """Mark a fabriks upload complete, which is when its manifest is read.

        Unlike an object store, this is not bookkeeping: ``fill_info`` fetches ``fabriks.json``
        and refuses the store if it is absent or unreadable, so an interrupted upload fails
        here rather than surviving as a store that a renderer discovers is broken.
        """
        from datalayer import models

        return self._finish_store_upload(models.FabriksStore, organization_id, input.store_id, input.valid)

    def generate_fabriks_access_grant(self, store: "models.FabriksStore") -> base_models.FabriksAccessGrant:
        """Return read credentials covering a fabriks store's whole prefix."""
        conf = self.get_bucket_config("fabriks")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("fabriks", store.key, "read", ttl)

        return base_models.FabriksAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=self.build_object_key("fabriks", store.key),
            path=store.path or self.build_store_path("fabriks", store.key),
            expires_in=ttl,
            store=str(store.pk),
        )

    def generate_general_fabriks_access_grant(self, organization_id: str, user_id: str) -> base_models.GeneralFabriksAccessGrant:
        """Return organization-wide read credentials for fabriks stores."""
        conf = self.get_bucket_config("fabriks")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("fabriks", organization_id, user_id, ttl)

        return base_models.GeneralFabriksAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            expires_in=ttl,
        )

    def get_konnektion_metadata(self, store: "models.KonnektionStore") -> base_models.KonnektionMetadata:
        """Read a konnektion store's manifest.

        One GET of one small object, at registration only -- the same shape as
        :meth:`get_fabriks_metadata` and for the same reason: the artifact describes itself, so
        the server reads rather than asks.

        The parsing lives in :mod:`datalayer.konnektion`, which reads the wire format with `json`
        and no dependency on the `konnektion` package that writes it.

        Args:
            store: Konnektion store whose prefix should be inspected.

        Returns:
            The parsed manifest.

        Raises:
            FileNotFoundError: If ``konnektion.json`` is missing.
            ValueError: If the manifest is malformed or its version unsupported.
        """
        path = store.path or self.build_store_path("konnektion", store.key)
        bucket_name, prefix = self._parse_s3_path(path)
        manifest_key = prefix.rstrip("/") + "/" + konnektion_format.MANIFEST_NAME
        location = f"s3://{bucket_name}/{manifest_key}"

        logger.debug("Fetching konnektion manifest from bucket '%s' with key '%s'", bucket_name, manifest_key)
        try:
            manifest_file = self._s3.get_object(Bucket=bucket_name, Key=manifest_key)
        except Exception as exc:
            # A missing manifest is the ordinary shape of an interrupted upload, because the
            # writer lands it last. Naming that is more useful than "not found".
            raise FileNotFoundError(
                f"No `{konnektion_format.MANIFEST_NAME}` at {location}, so this prefix is not a readable konnektion store. A writer uploads the manifest last, so an interrupted run leaves exactly this."
            ) from exc

        manifest = konnektion_format.parse_manifest(manifest_file["Body"].read(), where=f"The konnektion manifest at {location}")

        return base_models.KonnektionMetadata(
            spec_version=manifest.spec_version,
            grid=manifest.grid,
            encoding=manifest.encoding,
            axes=manifest.axes,
            attributes=manifest.attributes,
            counts=manifest.counts,
            files=manifest.files,
        )

    def generate_konnektion_upload_grant(self, organization_id: int, input: base_models.RequestKonnektionUploadInput) -> base_models.KonnektionUploadGrant:
        """Create a konnektion store and a prefix upload grant.

        The grant covers the whole prefix and permits read-back and delete inside it, because a
        konnektion store is written as a tree: parts first, manifest last. Nothing about the
        networks is taken from the caller -- the manifest states it, and ``fill_info`` reads it
        when the upload is finished.
        """
        from datalayer import models

        conf = self.get_bucket_config("konnektion")
        key = self._new_key()
        store = models.KonnektionStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("konnektion", key),
            key=key,
            bucket="konnektion",
            max_bytes=conf.default_max_bytes,
        )

        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("konnektion", store.key, "upload", ttl)
        full_key = self.build_object_key("konnektion", store.key)

        return base_models.KonnektionUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("konnektion", store.key),
            expires_in=ttl,
            max_bytes=conf.default_max_bytes,
            upload_file_name=store.get_upload_file_name(),
            store=str(store.pk),
        )

    def finish_konnektion_upload(self, organization_id: int, input: base_models.FinishKonnektionUploadInput) -> "models.KonnektionStore":
        """Mark a konnektion upload complete, which is when its manifest is read.

        Unlike an object store, this is not bookkeeping: ``fill_info`` fetches
        ``konnektion.json`` and refuses the store if it is absent or unreadable, so an
        interrupted upload fails here rather than surviving as a store that a renderer
        discovers is broken.
        """
        from datalayer import models

        return self._finish_store_upload(models.KonnektionStore, organization_id, input.store_id, input.valid)

    def generate_konnektion_access_grant(self, store: "models.KonnektionStore") -> base_models.KonnektionAccessGrant:
        """Return read credentials covering a konnektion store's whole prefix."""
        conf = self.get_bucket_config("konnektion")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("konnektion", store.key, "read", ttl)

        return base_models.KonnektionAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=self.build_object_key("konnektion", store.key),
            path=store.path or self.build_store_path("konnektion", store.key),
            expires_in=ttl,
            store=str(store.pk),
        )

    def generate_general_konnektion_access_grant(self, organization_id: str, user_id: str) -> base_models.GeneralKonnektionAccessGrant:
        """Return organization-wide read credentials for konnektion stores."""
        conf = self.get_bucket_config("konnektion")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("konnektion", organization_id, user_id, ttl)

        return base_models.GeneralKonnektionAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            expires_in=ttl,
        )

    def _build_zarr_upload_grant(self, store: "models.ZarrStore") -> base_models.ZarrUploadGrant:
        """Issue an upload grant for a Zarr store that already exists.

        Split out from :meth:`generate_zarr_upload_grant` so credentials can be issued a second
        time for the same store without minting a second one. Everything here is derived from
        the store row, so a reissued grant addresses the same prefix as the first -- only the
        credentials and their expiry are new.
        """
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("zarr", store.key, "upload", ttl)

        return base_models.ZarrUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=self.build_object_key("zarr", store.key),
            path=self.build_store_path("zarr", store.key),
            expires_in=ttl,
            datalayer="zarr",
            max_bytes=store.max_bytes if store.max_bytes is not None else conf.default_max_bytes,
            original_file_name=store.original_file_name,
            upload_file_name=store.get_upload_file_name(),
            upload_content_type=store.content_type,
            upload_form_field="file",
            store=str(store.pk),
        )

    def generate_zarr_upload_grant(self, organization_id: int, input: base_models.RequestZarrUploadInput) -> base_models.ZarrUploadGrant:
        """Create a Zarr store and upload grant."""
        from datalayer import models

        conf = self.get_bucket_config("zarr")
        key = self._new_key()
        store = models.ZarrStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("zarr", key),
            key=key,
            bucket="zarr",
            shape=input.shape,
            chunks=input.chunks,
            version=input.version,
            # Recorded so `finish` can compare what was advertised against what was delivered.
            # Nothing rejects an overrun -- see `DatalayerStore.max_bytes`.
            max_bytes=conf.default_max_bytes,
        )

        return self._build_zarr_upload_grant(store)

    def refresh_zarr_upload_grant(self, organization_id: int, store_id: str) -> base_models.ZarrUploadGrant:
        """Reissue upload credentials for a Zarr store whose upload is still in flight.

        A grant's credentials expire (``session_duration_seconds``, an hour by default) and the
        clients holding them treat the session token as static -- there is no refresh hook in
        obstore's ``S3Store``. So a write that outlives its session dies partway through, and
        the larger the array the likelier that is. This lets a client that is still writing ask
        for a fresh session against the *same* prefix and carry on.

        Refuses a store that is already populated: those bytes are referenced, and handing out
        write credentials for them is an overwrite path, not a resumption. A store finished with
        ``valid=False`` is *not* populated and stays refreshable, which is the retry case.

        Args:
            organization_id: The organization the caller is acting in.
            store_id: Primary key of the store to reissue credentials for.

        Returns:
            A fresh upload grant addressing the same prefix.

        Raises:
            ZarrStore.DoesNotExist: If no such store belongs to this organization.
            ValueError: If the store's upload is already finished.
        """
        from datalayer import models

        store = models.ZarrStore.objects.get(id=store_id, organization_id=organization_id)
        if store.populated:
            raise ValueError(f"Zarr store {store_id} is already populated, so its upload is finished and there is nothing to resume. Request a new upload grant to write a new store, or a delete grant to replace this one's bytes.")

        return self._build_zarr_upload_grant(store)

    def generate_parquet_upload_grant(self, organization_id: int, input: base_models.RequestParquetUploadInput) -> base_models.ParquetUploadGrant:
        """Create a parquet store and upload grant."""
        from datalayer import models

        conf = self.get_bucket_config("parquet")
        key = self._new_key()
        store = models.ParquetStore.objects.create(
            organization_id=organization_id,
            path=self.build_store_path("parquet", key),
            key=key,
            bucket="parquet",
            max_bytes=conf.default_max_bytes,
        )

        ttl = self._session_duration()
        access_key, secret_key, session_token = self._issue_temporary_credentials("parquet", store.key, "upload", ttl)
        full_key = self.build_object_key("parquet", store.key)

        return base_models.ParquetUploadGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("parquet", store.key),
            expires_in=ttl,
            datalayer="parquet",
            max_bytes=conf.default_max_bytes,
            original_file_name=store.original_file_name,
            upload_file_name=store.get_upload_file_name(),
            upload_content_type=store.content_type,
            upload_form_field="file",
            store=str(store.pk),
        )

    def _finish_store_upload(self, model_class: type[StoreModel], organization_id: int, store_id: str, valid: bool) -> StoreModel:
        """Finalize a created store after upload completion.

        Args:
            model_class: Store model type to load.
            store_id: Primary key of the store row.
            valid: Whether the upload succeeded and should be marked populated.

        Returns:
            The updated store instance.
        """
        store = model_class.objects.get(id=store_id, organization_id=organization_id)
        if valid:
            store.fill_info(self)
            self._record_delivered_size(store)
        else:
            store.populated = False
            store.save(update_fields=["populated"])
        return cast(StoreModel, store)

    def _record_delivered_size(self, store: "models.DatalayerStore") -> None:
        """Measure what an upload actually delivered and record it on the store.

        The only point at which the ``max_bytes`` a grant advertised can be checked against
        what arrived: S3 enforces no size limit on a credential grant, so an upload that
        exceeds its budget succeeds and finishes valid. Recording both numbers makes that
        visible instead of merely true.

        **Measurement, not enforcement.** An overrun is logged and the store is still
        populated, because refusing here would reject uploads that were never told a real
        budget -- every zarr grant advertises the configured default, which clients do not
        size. Deciding what a cap should mean is a separate change to the grant request.

        Never fails the finish: a store whose bytes cannot be measured is still a finished
        store, and losing the upload over a failed accounting read would be a worse trade.
        """
        try:
            delivered = store.measure_bytes(self)
        except Exception:
            logger.warning("Could not measure the bytes delivered for %s store %s; leaving size_bytes unset.", store.bucket, store.pk, exc_info=True)
            return

        store.size_bytes = delivered
        store.save(update_fields=["size_bytes"])

        if store.max_bytes is not None and delivered > store.max_bytes:
            logger.warning(
                "%s store %s delivered %d bytes against an advertised budget of %d (%.1fx). Nothing rejected it: a session policy bounds what a credential may write, not how much.",
                store.bucket,
                store.pk,
                delivered,
                store.max_bytes,
                delivered / store.max_bytes,
            )

    def finish_media_upload(self, organization_id: int, input: base_models.FinishMediaUploadInput) -> "models.MediaStore":
        """Mark a media upload as complete.

        Args:
            input: Completion payload for the media store.

        Returns:
            The finalized media store.
        """
        from datalayer import models

        return self._finish_store_upload(models.MediaStore, organization_id, input.store_id, input.valid)

    def finish_bigfile_upload(self, organization_id: int, input: base_models.FinishBigFileUploadInput) -> "models.BigFileStore":
        """Mark a big file upload as complete.

        Args:
            input: Completion payload for the big file store.

        Returns:
            The finalized big file store.
        """
        from datalayer import models

        return self._finish_store_upload(models.BigFileStore, organization_id, input.store_id, input.valid)

    def finish_zarr_upload(self, organization_id: int, input: base_models.FinishZarrUploadInput) -> "models.ZarrStore":
        """Mark a Zarr upload as complete.

        Args:
            input: Completion payload for the Zarr store.

        Returns:
            The finalized Zarr store.
        """
        from datalayer import models

        return self._finish_store_upload(models.ZarrStore, organization_id, input.store_id, input.valid)

    def finish_parquet_upload(self, organization_id: int, input: base_models.FinishParquetUploadInput) -> "models.ParquetStore":
        """Mark a parquet upload as complete.

        Args:
            input: Completion payload for the parquet store.

        Returns:
            The finalized parquet store.
        """
        from datalayer import models

        return self._finish_store_upload(models.ParquetStore, organization_id, input.store_id, input.valid)

    def get_object_size(self, bucket_name: str, object_key: str) -> int:
        """Get the size of an object in bytes.

        Args:
            bucket_name: The name of the S3 bucket.
            object_key: The key of the S3 object.
        Returns:
            The size of the object in bytes.
        """
        bucket_config = self.get_bucket_config(bucket_name)
        if bucket_config is None:
            raise ValueError(f"Bucket '{bucket_name}' is not configured in datalayer.")

        try:
            response = self._s3.head_object(Bucket=bucket_config.bucket, Key=object_key)
            return response["ContentLength"]
        except Exception as exc:
            raise FileNotFoundError(f"Could not retrieve object size for s3://{bucket_name}/{object_key}.") from exc

    def generate_file_read_url(
        self,
        bucket_key: str,
        object_path: str,
        *,
        store_id: str | None = None,
        expires_in: int | None = None,
    ) -> AccessGrant:
        """Build a generic read access grant.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.
            store_id: Optional backing store identifier.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the requested object.
        """
        conf = self.get_bucket_config(bucket_key)
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials(bucket_key, object_path, "read", ttl)
        full_key = self.build_object_key(bucket_key, object_path)
        return base_models.AccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            key=full_key,
            path=self.build_store_path(bucket_key, object_path),
            action="read",
            expires_in=ttl,
            datalayer=bucket_key,
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def generate_file_delete_url(
        self,
        bucket_key: str,
        object_path: str,
        *,
        store_id: str | None = None,
        expires_in: int | None = None,
    ) -> AccessGrant:
        """Build a generic delete access grant.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key or prefix.
            store_id: Optional backing store identifier.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to deleting the requested object.
        """
        conf = self.get_bucket_config(bucket_key)
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials(bucket_key, object_path, "delete", ttl)
        full_key = self.build_object_key(bucket_key, object_path)
        return base_models.AccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            key=full_key,
            path=self.build_store_path(bucket_key, object_path),
            action="delete",
            expires_in=ttl,
            datalayer=bucket_key,
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def generate_bigfile_access_grant(
        self,
        store: "models.BigFileStore",
        *,
        expires_in: int | None = None,
    ) -> base_models.BigFileAccessGrant:
        """Build a big file read access grant.

        Args:
            store: Big file store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the big file object.
        """
        object_path = store.key
        store_id = str(store.pk) if store.pk is not None else None
        conf = self.get_bucket_config("bigfile")
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials("bigfile", object_path, "read", ttl)
        full_key = self.build_object_key("bigfile", object_path)
        return base_models.BigFileAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("bigfile", object_path),
            action="read",
            expires_in=ttl,
            datalayer="bigfile",
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def generate_media_access_grant(
        self,
        store: "models.MediaStore",
        *,
        expires_in: int | None = None,
    ) -> base_models.MediaAccessGrant:
        """Build a media read access grant.

        Args:
            store: Media store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the media object.
        """
        object_path = store.key
        store_id = str(store.pk) if store.pk is not None else None
        conf = self.get_bucket_config("media")
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials("media", object_path, "read", ttl)
        full_key = self.build_object_key("media", object_path)
        return base_models.MediaAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=self.config.region,
            bucket=conf.bucket,
            key=full_key,
            path=self.build_store_path("media", object_path),
            action="read",
            expires_in=ttl,
            datalayer="media",
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def generate_general_media_access_grant(
        self,
        organization_id: str,
        user_id: str,
        expires_in: int | None = None,
    ) -> base_models.GeneralMediaAccessGrant:
        """Build a media read access grant.

        Args:
            store: Media store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the media object.
        """
        conf = self.get_bucket_config("media")
        ttl = self._session_duration(expires_in)
        # TODO: FIX ORGANIZATION SCOPED MEDIA GRANTS
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("media", organization_id, user_id, ttl)
        return base_models.GeneralMediaAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=self.config.region,
            bucket=conf.bucket,
            action="read",
            expires_in=ttl,
            datalayer="media",
            endpoint=self.config.endpoint_url or "",
        )

    def generate_general_parquet_access_grant(
        self,
        organization_id: str,
        user_id: str,
        expires_in: int | None = None,
    ) -> base_models.GeneralParquetAccessGrant:
        """Build a media read access grant.

        Args:
            store: Media store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the media object.
        """
        conf = self.get_bucket_config("parquet")
        ttl = self._session_duration(expires_in)
        # TODO: FIX ORGANIZATION SCOPED MEDIA GRANTS
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("parquet", organization_id, user_id, ttl)
        return base_models.GeneralParquetAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=self.config.region,
            bucket=conf.bucket,
            action="read",
            expires_in=ttl,
            datalayer="parquet",
            endpoint=self.config.endpoint_url or "",
        )

    def generate_general_zarr_access_grant(
        self,
        organization_id: str,
        user_id: str,
        expires_in: int | None = None,
    ) -> base_models.GeneralZarrAccessGrant:
        """Build a media read access grant.

        Args:
            store: Media store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the media object.
        """
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration(expires_in)
        # TODO: FIX ORGANIZATION SCOPED MEDIA GRANTS
        access_key, secret_key, session_token = self._issue_temporary_user_access_credentials("zarr", organization_id, user_id, ttl)
        return base_models.GeneralZarrAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=self.config.region,
            bucket=conf.bucket,
            action="read",
            expires_in=ttl,
            datalayer="zarr",
            endpoint=self.config.endpoint_url or "",
        )

    def generate_zarr_access_grant(
        self,
        store: "models.ZarrStore",
        *,
        expires_in: int | None = None,
    ) -> base_models.ZarrAccessGrant:
        """Build a Zarr read access grant.

        Args:
            store: Zarr store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the Zarr prefix.
        """
        object_path = store.key
        store_id = str(store.pk) if store.pk is not None else None
        conf = self.get_bucket_config("zarr")
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials("zarr", object_path, "read", ttl)
        full_key = self.build_object_key("zarr", object_path)
        return base_models.ZarrAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("zarr", object_path),
            action="read",
            expires_in=ttl,
            datalayer="zarr",
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def generate_parquet_access_grant(
        self,
        store: "models.ParquetStore",
        *,
        expires_in: int | None = None,
    ) -> base_models.ParquetAccessGrant:
        """Build a parquet read access grant.

        Args:
            store: Parquet store to grant access to.
            expires_in: Optional credential lifetime override in seconds.

        Returns:
            Temporary credentials scoped to reading the parquet object.
        """
        object_path = store.key
        store_id = str(store.pk) if store.pk is not None else None
        conf = self.get_bucket_config("parquet")
        ttl = self._session_duration(expires_in)
        access_key, secret_key, session_token = self._issue_temporary_credentials("parquet", object_path, "read", ttl)
        full_key = self.build_object_key("parquet", object_path)
        return base_models.ParquetAccessGrant(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=conf.bucket,
            region=self.config.region,
            key=full_key,
            path=self.build_store_path("parquet", object_path),
            action="read",
            expires_in=ttl,
            datalayer="parquet",
            endpoint=self.config.endpoint_url or "",
            store=str(store_id) if store_id is not None else None,
        )

    def put_file(
        self,
        bucket_key: str,
        object_path: str,
        payload: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload a single object with service credentials.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key.
            payload: File bytes to upload.
            content_type: Optional MIME type for the object.
        """
        conf = self.get_bucket_config(bucket_key)
        self._s3.put_object(
            Bucket=conf.bucket,
            Key=self.build_object_key(bucket_key, object_path),
            Body=payload,
            ContentType=content_type or "application/octet-stream",
        )

    def delete_object(self, bucket_key: str, object_path: str) -> None:
        """Delete a single object with service credentials.

        Only correct for stores whose key names one object. A zarr key is a *prefix* and
        S3 answers `DeleteObject` on a prefix with a 204 having deleted nothing, so use
        :meth:`delete_prefix` for those -- or let `DatalayerStore.delete` choose.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative object key.
        """
        conf = self.get_bucket_config(bucket_key)
        self._s3.delete_object(
            Bucket=conf.bucket,
            Key=self.build_object_key(bucket_key, object_path),
        )

    def measure_prefix_bytes(self, bucket_key: str, object_path: str) -> int:
        """Sum the sizes of every object under a prefix.

        The listing half of :meth:`delete_prefix` without the deleting half, and paginated for
        the same reason: a 100k-chunk array is ~100 round trips, not 100k. An absent prefix
        measures 0 rather than raising, so an unfinished or already-purged store reports the
        truth instead of an error.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative prefix.

        Returns:
            The total size in bytes of everything under the prefix.
        """
        conf = self.get_bucket_config(bucket_key)
        prefix = self.build_object_key(bucket_key, object_path).rstrip("/") + "/"

        total = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=conf.bucket, Prefix=prefix):
            total += sum(item["Size"] for item in page.get("Contents", []))
        return total

    def delete_prefix(self, bucket_key: str, object_path: str) -> int:
        """Delete every object under a prefix, and return how many were removed.

        The only way to remove a zarr: its key is a directory holding `zarr.json` and a tree of
        chunk objects, and S3 has no recursive delete. Listing is paginated and deletion is
        batched at the API's limit of 1000 keys per call, so a 100k-chunk array costs ~100
        round trips rather than 100k.

        Idempotent, which is what makes the sweeper safe to re-run: a prefix that is already
        gone lists empty and returns 0.

        Args:
            bucket_key: Logical datalayer store type.
            object_path: Store-relative prefix.

        Returns:
            The number of objects deleted.
        """
        conf = self.get_bucket_config(bucket_key)
        prefix = self.build_object_key(bucket_key, object_path).rstrip("/") + "/"

        deleted = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=conf.bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not keys:
                continue
            for start in range(0, len(keys), 1000):
                self._s3.delete_objects(Bucket=conf.bucket, Delete={"Objects": keys[start : start + 1000], "Quiet": True})
            deleted += len(keys)

        # A store written as a bare object at the prefix itself (no trailing slash) would be
        # missed by the listing above, so clear it too. Deleting an absent key is a no-op.
        self.delete_object(bucket_key, object_path)
        return deleted


GLOBAL_DL = None


def get_current_datalayer() -> Datalayer:
    """Return the request-scoped datalayer instance.

    Returns:
        The datalayer instance currently bound to the active request context.
    """
    global GLOBAL_DL
    if GLOBAL_DL is not None:
        return GLOBAL_DL

    else:
        GLOBAL_DL = Datalayer()
        return GLOBAL_DL

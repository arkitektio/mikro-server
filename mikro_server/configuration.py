"""Typed, fully-documented configuration schema for the **mikro** service.

Owned by this service. Values resolve (highest precedence first) from init
kwargs, environment variables (nested via ``__`` — e.g. ``POSTGRES__PASSWORD``),
then the YAML file (``config.yaml`` where the service runs by default; override with
``ARKITEKT_CONFIG_FILE``). Secret fields have **no default**: loading fails fast
with a ``ValidationError`` if they are not supplied via config or environment.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ByteSize, ConfigDict, Field

from arkitekt_service.server import settings as shared
from arkitekt_service.server.settings import DjangoSettings, InstanceSettings, PostgresSettings, ServiceSettings
from authentikate.base_models import AuthentikateSettings

class RedisSettings(shared.RedisSettings):
    """Redis connection (channel layer / cache)."""

    channel_prefix: str = Field(default="mikro", description="Key prefix for the channels_redis channel layer. Must be unique per service: every service on a shared redis used to send under the same prefix, so identically-named groups (e.g. \"files\") delivered one service's events to another's subscribers.")


class DatalayerBucket(BaseModel):
    """A single S3 bucket binding within the datalayer."""

    model_config = ConfigDict(extra="allow")

    bucket: str = Field(description="S3 bucket name.")
    # Declared rather than left to `extra="allow"`: two logical buckets may point at one
    # physical bucket, and the subpath is what keeps their objects apart. It reaches the grant
    # policy through `build_object_key`, so getting it wrong widens or narrows what a client
    # can read.
    subpath: Optional[str] = Field(default=None, description="Optional key prefix within the bucket, so several logical buckets can share one physical bucket.")
    default_max_bytes: Optional[ByteSize] = Field(default=None, description="Per-upload byte budget advertised on this bucket's grants when no quota sets `max_upload_bytes`. Accepts `500GiB`-style strings. Unset: 100 MiB.")


class QuotaLimits(BaseModel):
    """Byte limits at one level of the quota tree. Unset inherits from the level above; null at every level is unlimited.

    Byte values accept ints or strings such as ``500GiB`` / ``2TB``.
    """

    model_config = ConfigDict(extra="forbid")

    max_upload_bytes: Optional[ByteSize] = Field(default=None, description="Largest single store (upload) a user may write. Advertised on the grant as `maxBytes`; a declared `fileSize` above it is refused.")
    max_user_bytes: Optional[ByteSize] = Field(default=None, description="Total bytes one user may hold in one organization. A new upload grant is refused once it would pass this.")


class OrganizationQuota(QuotaLimits):
    """Quota for one organization, plus per-user overrides inside it."""

    max_org_bytes: Optional[ByteSize] = Field(default=None, description="Total bytes the whole organization may hold.")
    users: Dict[str, QuotaLimits] = Field(default_factory=dict, description="Per-user overrides in this organization, keyed by the user's token `sub`.")


class QuotaSettings(BaseModel):
    """Upload quotas, set by the hub owner. Resolved most specific first: user in org, then org, then `default`."""

    model_config = ConfigDict(extra="forbid")

    default: OrganizationQuota = Field(default_factory=OrganizationQuota, description="Limits for every organization without its own entry (its `users` map is ignored).")
    organizations: Dict[str, OrganizationQuota] = Field(default_factory=dict, description="Per-organization quotas, keyed by the token `org` claim -- since authentikate 4.0 the lok organization *id* as a string (e.g. `3`), not a readable name.")


class DatalayerSettings(BaseModel):
    """S3 storage connection and buckets (the datalayer module; replaces the old top-level ``s3`` block)."""

    model_config = ConfigDict(extra="allow")

    access_key: str = Field(description="S3 access key. Secret — must be set.")
    secret_key: str = Field(description="S3 secret key. Secret — must be set.")
    host: Optional[str] = Field(default=None, description="S3 endpoint host.")
    port: Optional[int] = Field(default=None, description="S3 endpoint port.")
    protocol: str = Field(default="http", description="S3 endpoint protocol (http or https).")
    region: str = Field(default="us-east-1", description="S3 region name.")
    media: DatalayerBucket = Field(description="Bucket for media / general file storage. Required for this service.")
    zarr: DatalayerBucket = Field(description="Bucket for Zarr arrays. Required for this service.")
    parquet: DatalayerBucket = Field(description="Bucket for Parquet tables. Required for this service.")
    bigfile: DatalayerBucket = Field(description="Bucket for large binary files (BigFileStore). Required for this service.")
    # Optional, unlike its four siblings: a required fifth bucket would refuse to start against
    # every config.yaml written before it existed. A deployment that registers no fabriks store
    # never needs one, and one that does gets a clear error from `get_bucket_config` rather
    # than a startup failure it cannot connect to the feature it did not enable.
    fabriks: Optional[DatalayerBucket] = Field(default=None, description="Bucket for fabriks stores (mesh collections stored as a prefix of Parquet files). Optional; may share a physical bucket with another entry via `subpath`.")
    upload_roles: List[str] = Field(default_factory=lambda: ["admin", "editor", "bot"], description="Organization roles allowed to request upload grants. Holding any one of them is enough.")
    quotas: QuotaSettings = Field(default_factory=QuotaSettings, description="Per-organization, per-user and per-upload byte quotas.")


class EmbeddingsSettings(BaseModel):
    """Semantic search: a model2vec static model embeds name + description into pgvector columns.

    Every value has a default, so the block may be omitted. The model itself is not a setting:
    it is fixed by the release, and the image carries its weights.
    """

    enabled: bool = Field(default=True, description="Embed rows on save and give `search` a semantic leg. Off: `search` is lexical-only and the embedding columns stay NULL.")
    distance_threshold: float = Field(default=0.55, description="Cosine distance (0 identical, 1 unrelated) above which a row no longer counts as a semantic `search` hit.")


class RekuestHookSettings(BaseModel):
    """How this process reaches the hub's rekuest: as a service (``rekuest_service``) and as a hook agent (``rekuest_hook``)."""

    rekuest_url: str = Field(default="http://rekuest:80/rekuest", description="rekuest's base URL on the internal network; runs are reported to its `agi/http/<agent>` intake.")
    service: str = Field(default="mikro", description="The name rekuest knows this process by: its `rekuest.services[].name` (signals are sent as it) and its `rekuest.hook_agents[].name`.")
    max_skew: int = Field(default=30, description="Clock skew (seconds) tolerated on a signed request; tokens themselves live 60 s.")


class Settings(ServiceSettings):
    """Top-level, validated configuration for the mikro service."""

    django: DjangoSettings = Field(description="Core Django settings.")
    postgres: PostgresSettings = Field(description="PostgreSQL connection.")
    redis: RedisSettings = Field(description="Redis connection.")
    authentikate: AuthentikateSettings = Field(description="Token-verification config (authentikate).")
    datalayer: DatalayerSettings = Field(description="S3 storage connection and buckets.")
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings, description="Semantic search: whether it is on, and its threshold.")
    rekuest_hook: Optional[RekuestHookSettings] = Field(default=None, description="How this process reaches the hub's rekuest: as a service (structures, signals) and as a hook agent.")
    instance: Optional[InstanceSettings] = Field(default=None, description="This instance's key and the hub trust bundle (signed requests to and from rekuest, no shared secrets).")

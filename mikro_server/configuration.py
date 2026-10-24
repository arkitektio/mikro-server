"""Typed, fully-documented configuration schema for the **mikro** service.

Owned by this service. Values resolve (highest precedence first) from init
kwargs, environment variables (nested via ``__`` — e.g. ``POSTGRES__PASSWORD``),
then the YAML file (``config.yaml`` where the service runs by default; override with
``ARKITEKT_CONFIG_FILE``). Secret fields have **no default**: loading fails fast
with a ``ValidationError`` if they are not supplied via config or environment.
"""

from typing import Optional

from pydantic import BaseModel, Field

from arkitekt_service.server import settings as shared
from arkitekt_service.server.settings import DjangoSettings, InstanceSettings, PostgresSettings, ServiceSettings
from arkitekt_datalayer.configuration import DatalayerBucket, DatalayerSettings
from authentikate.base_models import AuthentikateSettings

class RedisSettings(shared.RedisSettings):
    """Redis connection (channel layer / cache)."""

    channel_prefix: str = Field(default="mikro", description="Key prefix for the channels_redis channel layer. Must be unique per service: every service on a shared redis used to send under the same prefix, so identically-named groups (e.g. \"files\") delivered one service's events to another's subscribers.")


class MikroDatalayerSettings(DatalayerSettings):
    """The datalayer block, with the four buckets mikro cannot run without."""

    session_name: str = Field(default="mikro", description="Prefix of every STS session mikro assumes, so an object store's audit log names it.")
    media: DatalayerBucket = Field(description="Bucket for media / general file storage. Required for this service.")
    zarr: DatalayerBucket = Field(description="Bucket for Zarr arrays and sparse matrices. Required for this service.")
    parquet: DatalayerBucket = Field(description="Bucket for Parquet tables. Required for this service.")
    bigfile: DatalayerBucket = Field(description="Bucket for large binary files (BigFileStore). Required for this service.")


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
    datalayer: MikroDatalayerSettings = Field(description="S3 storage connection and buckets.")
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings, description="Semantic search: whether it is on, and its threshold.")
    rekuest_hook: Optional[RekuestHookSettings] = Field(default=None, description="How this process reaches the hub's rekuest: as a service (structures, signals) and as a hook agent.")
    instance: Optional[InstanceSettings] = Field(default=None, description="This instance's key and the hub trust bundle (signed requests to and from rekuest, no shared secrets).")

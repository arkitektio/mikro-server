"""Typed, fully-documented configuration schema for the **mikro** service.

Owned by this service. Values resolve (highest precedence first) from init
kwargs, environment variables (nested via ``__`` — e.g. ``POSTGRES__PASSWORD``),
then the YAML file (the mount's ``config.yaml`` by default; override with
``ARKITEKT_CONFIG_FILE``). Secret fields have **no default**: loading fails fast
with a ``ValidationError`` if they are not supplied via config or environment.
"""

import dataclasses
import os
import typing
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

import yaml
from pydantic import AliasChoices, BaseModel, ByteSize, ConfigDict, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from authentikate.base_models import AuthentikateSettings

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


class AdminSettings(BaseModel):
    """Django superuser created on first boot."""

    username: str = Field(description="Superuser login name.")
    password: str = Field(description="Superuser password. Secret — must be set.")
    email: Optional[str] = Field(default=None, description="Superuser email address.")


class DjangoSettings(BaseModel):
    """Core Django framework settings."""

    secret_key: str = Field(description="Django SECRET_KEY for cryptographic signing. Secret — must be set.")
    debug: bool = Field(default=False, description="Enable Django debug mode (never in production).")
    log_level: str = Field(default="INFO", description="Root logger level (e.g. DEBUG, INFO, WARNING). The LOG_LEVEL env var overrides it.")
    enable_rich_logging: bool = Field(default=False, description="Render console logs with rich (colours, boxed tracebacks). A dev convenience; off by default, as plain one-line records suit container logs.")
    hosts: List[str] = Field(default_factory=lambda: ["*"], description="ALLOWED_HOSTS entries.")
    use_x_forwarded_host: bool = Field(default=True, description="Trust the X-Forwarded-Host header behind a reverse proxy.")
    admin: Optional[AdminSettings] = Field(default=None, description="Superuser provisioned on first boot.")
    csrf_trusted_origins: List[str] = Field(default_factory=lambda: ["http://localhost", "https://localhost"], description="CSRF_TRUSTED_ORIGINS for unsafe (POST) requests.")
    force_script_name: str = Field(default="", description="URL path prefix (FORCE_SCRIPT_NAME) this service is served under.")


class PostgresSettings(BaseModel):
    """PostgreSQL database connection (Django ``DATABASES['default']``)."""

    model_config = ConfigDict(extra="allow")

    engine: str = Field(default="django.db.backends.postgresql", description="Django database backend (PostgreSQL).")
    db_name: str = Field(description="Database name.")
    username: str = Field(description="Database user.")
    password: str = Field(description="Database password. Secret — must be set.")
    host: str = Field(description="Database host.")
    port: int = Field(default=5432, description="Database port.")


class RedisSettings(BaseModel):
    """Redis connection (channel layer / cache)."""

    model_config = ConfigDict(extra="allow")

    host: str = Field(description="Redis host.")
    port: int = Field(default=6379, description="Redis port.")
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

    Every value has a default, so the block may be omitted. The vector width is fixed by the
    model *and* by the database columns; see CONFIG.md before changing ``model``.
    """

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    enabled: bool = Field(default=True, description="Embed rows on save and give `search` a semantic leg. Off: `search` is lexical-only and the embedding columns stay NULL.")
    model: str = Field(default="minishlab/potion-base-8M", description="model2vec model id. Recorded on every row; rows embedded by another model are re-embedded in-process and skipped by vector search until then.")
    model_path: Optional[str] = Field(default=None, description="Directory holding the weights of `model` (save_pretrained layout). The Docker image bakes them under /opt/models and sets EMBEDDINGS__MODEL_PATH; unset, model2vec downloads from Hugging Face on first use.")
    dimensions: int = Field(default=256, description="Vector width of `model`. Also the width of the database columns, so changing it is a migration. Checked against both at startup.")
    distance_threshold: float = Field(default=0.55, description="Cosine distance (0 identical, 1 unrelated) above which a row no longer counts as a semantic `search` hit.")
    sweep_interval: int = Field(default=300, description="No longer used: `reembed_stale` (which re-embeds rows whose `embedding_model` is not `model`) is only offered as an action, and scheduling it is the organization's own automation. Kept so existing configs load.")
    sweep_batch_size: int = Field(default=200, description="Rows re-embedded per pass.")


class RekuestHookSettings(BaseModel):
    """How this process reaches the hub's rekuest: as a service (``rekuest_service``) and as a hook agent (``rekuest_hook``)."""

    rekuest_url: str = Field(default="http://rekuest:80/rekuest", description="rekuest's base URL on the internal network; runs are reported to its `agi/http/<agent>` intake.")
    service: str = Field(default="mikro", description="The name rekuest knows this process by: its `rekuest.services[].name` (signals are sent as it) and its `rekuest.hook_agents[].name`.")
    max_skew: int = Field(default=30, description="Clock skew (seconds) tolerated on a signed request; tokens themselves live 60 s.")


class InstanceTrustSettings(BaseModel):
    """Where the hub's instance public keys come from: the coord's bundle, or inline."""

    jwks_uri: Optional[str] = Field(default=None, description="The coord's hub-keys URL (the fakts `self.hub_keys_url`).")
    jwks: Optional[Dict[str, Any]] = Field(default=None, description="The bundle inline (a JWKS whose keys carry `service`), for a hub not enrolled yet.")


class InstanceSettings(BaseModel):
    """This instance's key — its only secret towards the hub's other services — and whom it trusts."""

    private_key: str = Field(description="Ed25519 private key (PKCS#8 PEM). Signs this service's requests to rekuest. Secret — must be set.")
    trust: InstanceTrustSettings = Field(default_factory=InstanceTrustSettings, description="The hub's trust bundle.")


class Settings(BaseSettings):
    """Top-level, validated configuration for the mikro service."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    django: DjangoSettings = Field(description="Core Django settings.")
    postgres: PostgresSettings = Field(description="PostgreSQL connection.")
    redis: RedisSettings = Field(description="Redis connection.")
    authentikate: AuthentikateSettings = Field(description="Token-verification config (authentikate).")
    datalayer: DatalayerSettings = Field(description="S3 storage connection and buckets.")
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings, description="Semantic search model and thresholds.")
    rekuest_hook: Optional[RekuestHookSettings] = Field(default=None, description="Let the hub's rekuest run this service's periodic work (`reembed_stale`). Without it stale embeddings are not healed.")
    instance: Optional[InstanceSettings] = Field(default=None, description="This instance's key and the hub trust bundle (signed requests to and from rekuest, no shared secrets).")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: explicit init kwargs > environment variables > YAML file.
        path = os.environ.get("ARKITEKT_CONFIG_FILE", _DEFAULT_CONFIG)
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


@dataclasses.dataclass(frozen=True)
class Unread:
    """What a config file says that this release does not read as written."""

    unknown: list[str]
    """Keys no setting claims, as dotted paths: a misspelling, or a key of another release."""
    renamed: list[tuple[str, str]]
    """Keys still read under a former name, with the name they have now."""

    def __bool__(self) -> bool:
        """Whether there is anything to say."""
        return bool(self.unknown or self.renamed)


def config_path() -> str:
    """The YAML file the settings are read from."""
    return os.environ.get("ARKITEKT_CONFIG_FILE", _DEFAULT_CONFIG)


def _models_of(annotation: object) -> list[type[BaseModel]]:
    """This module's settings models an annotation holds: itself, or inside ``Optional[...]`` / ``list[...]``.

    Only this module's: a block another package defines (``authentikate``) is that package's to
    judge, and its aliases are spellings, not former names.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation] if annotation.__module__ == __name__ else []
    return [model for inner in typing.get_args(annotation) for model in _models_of(inner)]


def _names(model: type[BaseModel]) -> dict[str, str]:
    """Every key ``model`` reads, to the field's own name: its fields and their former names."""
    names: dict[str, str] = {}
    for name, field in model.model_fields.items():
        names[name] = name
        alias = field.validation_alias
        for former in alias.choices if isinstance(alias, AliasChoices) else [alias]:
            if isinstance(former, str):
                names[former] = name
    return names


def _unread(model: type[BaseModel], written: Mapping[str, object], path: str, into: Unread) -> None:
    # A block that passes its extras on (a connection's driver options) and the top level,
    # which every service of a hub shares the shape of, are open: nothing there is unknown.
    closed = model.model_config.get("extra") != "allow" and not issubclass(model, BaseSettings)
    names = _names(model)
    for key, value in written.items():
        where = f"{path}{key}"
        name = names.get(key)
        if name is None:
            if closed:
                into.unknown.append(where)
            continue
        if name != key:
            into.renamed.append((where, f"{path}{name}"))
        for inner in _models_of(model.model_fields[name].annotation):
            for index, item in enumerate(value) if isinstance(value, list) else [(None, value)]:
                if isinstance(item, dict):
                    _unread(inner, item, f"{where}." if index is None else f"{where}[{index}].", into)


def unread(written: Mapping[str, object] | None = None) -> Unread:
    """What the config file (or ``written``) says that this release does not read as written.

    A setting nobody reads is silent by nature: the service starts, with the default. This is
    what makes it loud — a system check at boot, and ``validate_settings --strict``, which an
    installer runs against a release before it moves a hub to it.
    """
    if written is None:
        try:
            with open(config_path(), encoding="utf-8") as file:
                loaded: object = yaml.safe_load(file)
        except OSError:
            loaded = None
        written = loaded if isinstance(loaded, dict) else {}
    found = Unread(unknown=[], renamed=[])
    _unread(Settings, written, "", found)
    return found

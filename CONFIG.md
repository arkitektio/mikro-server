# Mikro — Configuration Reference

This document explains how the **mikro** service is configured, then lists every
configuration value, its environment-variable name, its default, and what it does.

The single source of truth for the schema is
[`mikro_server/configuration.py`](mikro_server/configuration.py); this file documents it for
humans. If the two ever disagree, the code wins — and you can always print the live,
resolved configuration with `python manage.py validate_settings` (see below).

---

## How configuration works

Configuration is a typed [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
schema. Values are resolved from several sources, **highest precedence first**:

1. **Init kwargs** — values passed directly in code (rarely used; tests).
2. **Environment variables** — override anything in the YAML file.
3. **The YAML file** — [`config.yaml`](config.yaml) by default.
4. **File secrets** — Docker/systemd secret files, if used.

So an environment variable always beats the YAML file, which makes containerized
overrides easy without editing the mounted config.

### The YAML file

By default the service reads `config.yaml` next to the project. Point it elsewhere with
the `ARKITEKT_CONFIG_FILE` environment variable:

```bash
ARKITEKT_CONFIG_FILE=/etc/mikro/config.yaml python manage.py runserver
```

The file is a nested mapping, one top-level key per configuration *block*:

```yaml
django:
  secret_key: "change-me"
  debug: false
postgres:
  db_name: mikro_db
  username: mikro
  password: "change-me"
  host: db
  port: 5432
redis:
  host: redis
  port: 6379
```

### Environment variables (the `__` rule)

Every value is also settable from the environment. The nesting is expressed with a
**double-underscore** (`__`) delimiter, and names are case-insensitive:

| YAML path | Environment variable |
|---|---|
| `postgres.password` | `POSTGRES__PASSWORD` |
| `postgres.port` | `POSTGRES__PORT` |
| `django.debug` | `DJANGO__DEBUG` |
| `datalayer.region` | `DATALAYER__REGION` |

Lists and nested objects (e.g. `authentikate.issuers` or `datalayer.zarr`) are awkward to
express as environment variables — prefer the YAML file for those and use env vars for the
flat scalars (hosts, ports, passwords, toggles).

### Secrets fail fast

Fields marked **secret / required** below have **no default**. If they are missing from
both the YAML file and the environment, the service refuses to start and raises a
`pydantic.ValidationError` naming the missing field. The same error blocks
`manage.py` entirely, so a broken config cannot be deployed silently.

### Validating a configuration

Run the bundled command to load the config exactly as the app would, validate it, and
print the fully-resolved result as a tree with **secrets redacted**:

```bash
python manage.py validate_settings
```

- Valid config → prints a green `Configuration valid` tree and exits `0`.
- Invalid config → prints each offending field and its error, and exits `1`.

It honors `ARKITEKT_CONFIG_FILE`, so you can validate an alternate file the same way.
(Note: because Django loads settings on startup, a fundamentally invalid config also
surfaces the same validation errors when running *any* `manage.py` command.)

---

## Configuration reference

Secret fields are flagged with 🔒. "Required" means there is no default.

### `django` — core Django framework settings

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `secret_key` 🔒 | `DJANGO__SECRET_KEY` | str | **required** | Django `SECRET_KEY` for cryptographic signing. |
| `debug` | `DJANGO__DEBUG` | bool | `false` | Enable Django debug mode. Never enable in production. |
| `hosts` | `DJANGO__HOSTS` | list[str] | `["*"]` | `ALLOWED_HOSTS` entries. |
| `use_x_forwarded_host` | `DJANGO__USE_X_FORWARDED_HOST` | bool | `true` | Trust the `X-Forwarded-Host` header behind a reverse proxy. |
| `admin` | `DJANGO__ADMIN__*` | object | `null` | Superuser provisioned on first boot (see below). |
| `csrf_trusted_origins` | `DJANGO__CSRF_TRUSTED_ORIGINS` | list[str] | `["http://localhost", "https://localhost"]` | `CSRF_TRUSTED_ORIGINS` for unsafe (POST) requests. |
| `force_script_name` | `DJANGO__FORCE_SCRIPT_NAME` | str | `""` | URL path prefix this service is served under (`FORCE_SCRIPT_NAME`). |

#### `django.admin` — superuser created on first boot

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `username` | `DJANGO__ADMIN__USERNAME` | str | **required** | Superuser login name. |
| `password` 🔒 | `DJANGO__ADMIN__PASSWORD` | str | **required** | Superuser password. |
| `email` | `DJANGO__ADMIN__EMAIL` | str | `null` | Superuser email address. |

### `postgres` — PostgreSQL database (Django `DATABASES['default']`)

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `engine` | `POSTGRES__ENGINE` | str | `django.db.backends.postgresql` | Django database backend. |
| `db_name` | `POSTGRES__DB_NAME` | str | **required** | Database name. |
| `username` | `POSTGRES__USERNAME` | str | **required** | Database user. |
| `password` 🔒 | `POSTGRES__PASSWORD` | str | **required** | Database password. |
| `host` | `POSTGRES__HOST` | str | **required** | Database host. |
| `port` | `POSTGRES__PORT` | int | `5432` | Database port. |

### `redis` — Redis connection (channel layer / cache)

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `host` | `REDIS__HOST` | str | **required** | Redis host. |
| `port` | `REDIS__PORT` | int | `6379` | Redis port. |

### `authentikate` — inbound token verification

Configures how incoming JWT access tokens are verified (the shared `authentikate`
library). At least one issuer is required.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `issuers` | — (use YAML) | list[issuer] | **required** | Trusted token issuers whose keys verify incoming tokens (see issuer kinds below). |
| `authorization_headers` | `AUTHENTIKATE__AUTHORIZATION_HEADERS` | list[str] | `["Authorization", "X-Authorization", "AUTHORIZATION", "authorization"]` | Request headers searched (in order) for a Bearer token. |
| `provenance_header` | `AUTHENTIKATE__PROVENANCE_HEADER` | list[str] | provenance task header names | Request headers searched for an inbound provenance token. |
| `static_tokens` | — (use YAML) | map | `{}` | Pre-defined tokens that bypass signature verification. **Tests only.** |
| `provenance` | — (use YAML) | object | `null` | Inbound provenance-token verification (separate issuers/`audience`/`algorithms`; `null` disables it). |

Each entry in `issuers` is discriminated by its `kind`:

- `kind: rsa` — inline PEM RSA public key. Fields: `iss`, `kid` (default `1`), `public_key`.
- `kind: rsa_file` — RSA public key read from a PEM file. Fields: `iss`, `kid`, `public_key_pem_file`.
- `kind: jwks_dict` — inline JWKS document. Fields: `iss`, `jwks` (a dict with a `keys` list).
- `kind: jwks_uri` — JWKS fetched from a remote endpoint. Fields: `iss`, `jwks_uri`.

```yaml
authentikate:
  issuers:
    - kind: rsa
      iss: lok
      kid: lok-key-1
      public_key: "ssh-rsa AAAA..."
  static_tokens: {}
```

### `datalayer` — S3 storage connection and buckets

S3 storage configuration for the datalayer module (replaces the old top-level `s3` block).
`access_key` and `secret_key` are required secrets, and **all four buckets**
(`media`, `zarr`, `parquet`, `bigfile`) are required — mikro stores image arrays, tables,
and large binaries here.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `access_key` 🔒 | `DATALAYER__ACCESS_KEY` | str | **required** | S3 access key. |
| `secret_key` 🔒 | `DATALAYER__SECRET_KEY` | str | **required** | S3 secret key. |
| `host` | `DATALAYER__HOST` | str | `null` | S3 endpoint host. |
| `port` | `DATALAYER__PORT` | int | `null` | S3 endpoint port. |
| `protocol` | `DATALAYER__PROTOCOL` | str | `http` | S3 endpoint protocol (`http` or `https`). |
| `region` | `DATALAYER__REGION` | str | `us-east-1` | S3 region name. |
| `media` | — (use YAML) | object | **required** | Bucket for media / general file storage. `{ bucket: <name> }`. |
| `zarr` | — (use YAML) | object | **required** | Bucket for Zarr arrays. `{ bucket: <name> }`. |
| `parquet` | — (use YAML) | object | **required** | Bucket for Parquet tables. `{ bucket: <name> }`. |
| `bigfile` | — (use YAML) | object | **required** | Bucket for large binary files (BigFileStore). `{ bucket: <name> }`. |
| `upload_roles` | — (use YAML) | list[str] | `[admin, editor, bot]` | Organization roles allowed to request, refresh and finish upload grants. Holding any one is enough. |
| `quotas` | — (use YAML) | object | no limits | Per-organization, per-user and per-upload byte quotas. See [Upload quotas](#upload-quotas). |

Each bucket binding is an object:

| Key | Type | Default | Description |
|---|---|---|---|
| `bucket` | str | **required** | S3 bucket name. |
| `subpath` | str | `null` | Key prefix within the bucket, so several logical buckets can share one physical bucket. |
| `default_max_bytes` | bytes | `100MiB` | Per-upload budget advertised on grants when no quota sets `max_upload_bytes`. |

```yaml
datalayer:
  access_key: "REPLACE_ME"
  secret_key: "REPLACE_ME"
  host: minio
  port: 9000
  protocol: http
  media:
    bucket: mikro-media
  zarr:
    bucket: mikro-zarr
  parquet:
    bucket: mikro-parquet
  bigfile:
    bucket: mikro-bigfile
```

#### Upload quotas

Set by the hub owner, in config only (no API changes them). Each limit resolves most
specific first: the user's entry in their organization, then the organization's entry, then
`default`. Organizations are keyed by the token `org` claim, which since authentikate 4.0 is
the lok organization **id** (as a string, e.g. `"3"`), not its name. A key that matches
nothing falls through to `default` silently. Users are keyed by the token `sub`. Unset everywhere means unlimited, except `max_upload_bytes`, which falls back
to the bucket's `default_max_bytes`. Byte values accept ints or strings like `500GiB`/`2TB`.

| Key | Levels | Description |
|---|---|---|
| `max_upload_bytes` | default, org, user | Largest single store. Advertised on the grant as `maxBytes`; a declared `fileSize` above it is refused. |
| `max_user_bytes` | default, org, user | Total bytes one user may hold in one organization. |
| `max_org_bytes` | default, org | Total bytes one organization may hold. |

Quotas are checked when a grant is issued: S3 cannot cap bytes mid-write. Usage is the measured
`size_bytes` of stores still in use (orphaned stores do not count), so an overrun is caught
by the next grant rather than prevented. Stores written before creators were tracked count
toward their organization only.

```yaml
datalayer:
  upload_roles: [admin, editor, bot]
  quotas:
    default:
      max_upload_bytes: 500GiB
    organizations:
      "3":                     # the token `org` claim: the lok organization id
        max_org_bytes: 5TiB
        max_user_bytes: 1TiB
        users:
          "<user sub>":        # the token `sub` claim
            max_user_bytes: 3TiB
```

### `embeddings` — semantic search

Folders, array datasets and table datasets embed their name + description into a pgvector
column when they are saved, using a [model2vec](https://github.com/MinishLab/model2vec)
static model that runs inside the service process (CPU, ~1 ms per row, no extra service).
The `search` argument of `folders`, `arrayDatasets` and `tableDatasets` then matches the
name lexically (full-text for folders, substring for datasets) **or** a description whose
meaning is close to the query, ranking lexical matches first and the rest by similarity.
Every key has a default; the block may be omitted.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `enabled` | `EMBEDDINGS__ENABLED` | bool | `true` | Embed rows on save and give `search` a semantic leg. Off: `search` is lexical-only and the columns stay `NULL`. |
| `model` | `EMBEDDINGS__MODEL` | str | `minishlab/potion-base-8M` | model2vec model id. Recorded on every row (`embedding_model`); rows embedded by another model are re-embedded in-process and skipped by vector search until then. |
| `model_path` | `EMBEDDINGS__MODEL_PATH` | str | `null` | Directory holding the weights of `model`. The Docker image bakes them under `/opt/models/embeddings` and sets this itself (with `HF_HUB_OFFLINE=1`); unset, model2vec downloads from Hugging Face on first use. |
| `dimensions` | `EMBEDDINGS__DIMENSIONS` | int | `256` | Vector width of `model` — and of the database columns. Checked against both at startup (`embeddings.E001` / `E002`). |
| `distance_threshold` | `EMBEDDINGS__DISTANCE_THRESHOLD` | float | `0.55` | Cosine distance (0 identical, 1 unrelated) above which a row no longer counts as a semantic hit. Lower is stricter. |
| `sweep_interval` | `EMBEDDINGS__SWEEP_INTERVAL` | int | `30` | Seconds between passes of the in-process healer that re-embeds stale rows. |
| `sweep_batch_size` | `EMBEDDINGS__SWEEP_BATCH_SIZE` | int | `200` | Rows re-embedded per batch. |

Rows that were written before embeddings were enabled, while the model could not be loaded,
or by a previous `model` are healed by a loop inside every serving process
(`mikro_server/asgi.py` starts it; `embeddings/healer.py` is the loop) in row-locked batches —
no command, no cron, any number of replicas. Until healed, such rows are found by the lexical
leg only. A re-embed is not an edit: it writes no history row.

**Changing the model.** Same `dimensions`: change `model`, restart, and the healer re-embeds
every row within a few sweeps. Different `dimensions`: the column type changes, so write a
migration that first nulls the three columns (`UPDATE core_folder SET embedding = NULL,
embedding_model = ''`, likewise `core_arraydataset` and `core_tabledataset` — Postgres refuses
to retype non-empty vectors), then `AlterField`s them to the new width, then change the
config; the healer refills them after boot. `migrate` refuses to run while a column, the
setting and the model disagree.

The Docker image bakes the default model; a different `model` needs a rebuild with
`--build-arg EMBEDDINGS_MODEL=<id>` (or a `model_path` of your own), because the running
image is offline.

---

## Minimal example

```yaml
django:
  secret_key: "REPLACE_ME"
  debug: false
  admin:
    username: admin
    password: "REPLACE_ME"
    email: admin@example.com
postgres:
  db_name: mikro_db
  username: mikro
  password: "REPLACE_ME"
  host: db
  port: 5432
redis:
  host: redis
  port: 6379
authentikate:
  issuers:
    - kind: rsa
      iss: lok
      kid: lok-key-1
      public_key: "ssh-rsa AAAA..."
# Optional — everything defaults; shown for the one knob worth tuning.
embeddings:
  distance_threshold: 0.55
datalayer:
  access_key: "REPLACE_ME"
  secret_key: "REPLACE_ME"
  host: minio
  port: 9000
  protocol: http
  media:
    bucket: mikro-media
  zarr:
    bucket: mikro-zarr
  parquet:
    bucket: mikro-parquet
  bigfile:
    bucket: mikro-bigfile
```

Validate it with `python manage.py validate_settings`.

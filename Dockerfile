# syntax=docker/dockerfile:1
# ---- builder: resolve deps into /opt/venv (all prebuilt wheels, no toolchain) ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /workspace
# Dependency layer — cached until pyproject.toml / uv.lock change:
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
# Embedding model weights (semantic `search`; see the `embeddings` package). Baked here so
# the runtime image never talks to Hugging Face, and before the source is copied so a source
# change does not download them again. Outside /workspace on purpose: the dev compose
# bind-mounts the repo over /workspace and would hide anything under it.
ARG EMBEDDINGS_MODEL=minishlab/potion-base-8M
COPY embeddings/scripts/bake.py embeddings/scripts/bake.py
RUN /opt/venv/bin/python embeddings/scripts/bake.py bake "$EMBEDDINGS_MODEL"
# Project layer:
COPY . .
RUN uv sync --frozen --no-dev
# The model is a constant of the release (`embeddings.engine.MODEL`), not a build option: the
# weights baked above must be that model's, or the image would refuse to embed at its start.
RUN /opt/venv/bin/python embeddings/scripts/bake.py check
# The service's own code, compiled like its dependencies: every container of this image
# (the migrate job, the server) would otherwise compile it again at start.
RUN /opt/venv/bin/python -m compileall -q -x '/(tests|\.venv)/' /workspace

# ---- runtime: slim base + prebuilt venv; psycopg[binary] bundles libpq ----
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HUB_OFFLINE=1 \
    ARKITEKT_SERVICE=mikro_server.contract
WORKDIR /workspace
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models
COPY --from=builder /workspace /workspace
# Where this code came from, said by the build (`--build-arg`; the release workflow passes the
# repository and the commit): `describe` reports it as `source`. Last, so that a new commit
# invalidates no layer above. Empty for a local build, which then names no source.
ARG ARKITEKT_SOURCE_REPOSITORY=""
ARG ARKITEKT_SOURCE_REVISION=""
ENV ARKITEKT_SOURCE_REPOSITORY=${ARKITEKT_SOURCE_REPOSITORY} \
    ARKITEKT_SOURCE_REVISION=${ARKITEKT_SOURCE_REVISION}
# With no command, a container of this image says what it is and stops: that is how an
# installer asks, knowing nothing of what is inside. How it serves, how it is prepared and
# what else can be run in it are in the answer (`serve`, `debug`, `jobs`).
CMD ["arkitekt-service", "describe"]

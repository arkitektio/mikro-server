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
# Project layer:
COPY . .
RUN uv sync --frozen --no-dev
# Embedding model weights (semantic `search`; see the `embeddings` package). Baked here so
# the runtime image never talks to Hugging Face. Outside /workspace on purpose: the dev
# compose bind-mounts the repo over /workspace and would hide anything under it.
ARG EMBEDDINGS_MODEL=minishlab/potion-base-8M
RUN EMBEDDINGS_MODEL="$EMBEDDINGS_MODEL" /opt/venv/bin/python -c "\
import os; from model2vec import StaticModel; \
name = os.environ['EMBEDDINGS_MODEL']; target = '/opt/models/embeddings'; \
StaticModel.from_pretrained(name).save_pretrained(target); \
open(os.path.join(target, 'MODEL_ID'), 'w').write(name)"

# ---- runtime: slim base + prebuilt venv; psycopg[binary] bundles libpq ----
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HUB_OFFLINE=1 \
    EMBEDDINGS__MODEL_PATH=/opt/models/embeddings
WORKDIR /workspace
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models
COPY . .

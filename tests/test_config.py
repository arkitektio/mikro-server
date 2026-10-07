"""Validate the mikro service's config.yaml against its bespoke schema.

Standalone — needs no database; run with ``uv run pytest tests/test_config.py``.
"""

from arkitekt_service.contract.unread import unread
from arkitekt_service.server.settings import written

from mikro_server.configuration import Settings


def test_config_yaml_validates():
    """The service's own config.yaml parses into the typed schema."""
    s = Settings()
    assert s.postgres.db_name
    assert s.redis.host


def test_env_override(monkeypatch):
    """Env vars override the YAML file (nested via ``__``)."""
    monkeypatch.setenv("POSTGRES__PASSWORD", "from-env-test")
    assert Settings().postgres.password == "from-env-test"


def test_embeddings_block_defaults_and_override(monkeypatch):
    """The ``embeddings`` block is on by default and its threshold is env-overridable; the model is not a setting."""
    s = Settings()
    assert s.embeddings.enabled is True
    assert set(type(s.embeddings).model_fields) == {"enabled", "distance_threshold"}

    monkeypatch.setenv("EMBEDDINGS__DISTANCE_THRESHOLD", "0.42")
    s = Settings()
    assert s.embeddings.distance_threshold == 0.42


def test_the_embedding_model_is_not_a_setting():
    """A config still naming the model says something this release does not read, and is told so."""
    found = unread(Settings, {"embeddings": {"enabled": True, "model": "minishlab/potion-base-8M", "dimensions": 256, "sweep_interval": 30}})
    assert found.unknown == ["embeddings.model", "embeddings.dimensions", "embeddings.sweep_interval"]


def test_the_services_own_config_is_read_as_written():
    """Nothing in the repo's config.yaml goes unread."""
    assert not unread(Settings, written())


def test_an_unknown_key_in_a_block_of_this_service_is_reported():
    """A key no setting claims is said, where the block is this service's and closed."""
    found = unread(Settings, {"django": {"secret_key": "s", "debgu": True}, "postgres": {"sslmode": "require"}, "somebody_elses": {"x": 1}})
    assert found.unknown == ["django.debgu"]
    assert found.renamed == []

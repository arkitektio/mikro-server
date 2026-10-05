"""Validate the mikro service's config.yaml against its bespoke schema.

Standalone — needs no database; run with ``uv run pytest tests/test_config.py``.
"""

from mikro_server.configuration import Settings, unread


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
    """The ``embeddings`` block defaults to potion-base-8M at 256 dims and is env-overridable."""
    s = Settings()
    assert s.embeddings.enabled is True
    assert s.embeddings.model == "minishlab/potion-base-8M"
    assert s.embeddings.dimensions == 256
    assert s.embeddings.model_path is None

    monkeypatch.setenv("EMBEDDINGS__DISTANCE_THRESHOLD", "0.42")
    monkeypatch.setenv("EMBEDDINGS__MODEL_PATH", "/opt/models/embeddings")
    s = Settings()
    assert s.embeddings.distance_threshold == 0.42
    assert s.embeddings.model_path == "/opt/models/embeddings"


def test_the_services_own_config_is_read_as_written():
    """Nothing in the repo's config.yaml goes unread."""
    assert not unread()


def test_an_unknown_key_in_a_block_of_this_service_is_reported():
    """A key no setting claims is said, where the block is this service's and closed."""
    found = unread({"django": {"secret_key": "s", "debgu": True}, "postgres": {"sslmode": "require"}, "somebody_elses": {"x": 1}})
    assert found.unknown == ["django.debgu"]
    assert found.renamed == []

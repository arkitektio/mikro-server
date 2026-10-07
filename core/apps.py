from django.apps import AppConfig


class CoreConfig(AppConfig):
    """The mikro domain app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        """Register the embedding system check.

        It asserts that the ``vector(N)`` columns are the embedding model's width; it is
        database-tagged, so ``migrate`` runs it and a mismatch fails the job that prepares the
        database, before anything serves a wrong search.
        """
        import embeddings.checks  # noqa: F401

        # The receivers behind the subscriptions (files, annotations, layers, ...): a receiver is
        # only connected once the module that defines it has been imported.
        import core.signals  # noqa: F401

        # The hub's rekuest: actions and model signals, connected in every process (web,
        # shell, management commands) — not only once the URLconf has loaded.
        import mikro_server.service  # noqa: F401

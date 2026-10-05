from django.apps import AppConfig


class CoreConfig(AppConfig):
    """The mikro domain app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        """Register the embedding system checks.

        They assert that the model's width, ``EMBEDDINGS.DIMENSIONS`` and the ``vector(N)``
        columns agree; ``migrate`` runs the database-tagged one at every boot, so a mismatch
        stops the service before it serves a wrong search.
        """
        import embeddings.checks  # noqa: F401

        # The receivers behind the subscriptions (files, annotations, layers, ...): a receiver is
        # only connected once the module that defines it has been imported.
        import core.signals  # noqa: F401

        # The hub's rekuest: actions and model signals, connected in every process (web,
        # shell, management commands) — not only once the URLconf has loaded.
        import mikro_server.service  # noqa: F401

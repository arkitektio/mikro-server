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

        # What the data layer must be told about scenes: that a space may be a scene's world,
        # and which rows a scene layer's pickers name by id. Registered rather than written
        # into the guards, through the same calls any other composition's app makes.
        from core import models
        from core.logic import compositions, pickers
        from core.logic import coordinate_system as coordinate_system_logic

        compositions.register_composition(models.Scene, world_relation="scenes", noun="scene")
        # A lens is shared by every composition over its selection, and `Layer.lens` cascades.
        compositions.register_delete_guard(models.Lens, coordinate_system_logic.assert_lens_deletable)
        compositions.register_delete_guard(models.TableDataset, pickers.assert_table_not_in_a_picker)
        compositions.register_delete_guard(models.SparseDataset, pickers.assert_sparse_dataset_not_in_a_picker)
        compositions.register_delete_guard(models.Transformation, pickers.assert_edge_not_stranding_a_picker)

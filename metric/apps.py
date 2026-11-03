from django.apps import AppConfig


class MetricConfig(AppConfig):
    """Metrics: values recorded about mikro's data, immutable, and fed to whoever reads them."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "metric"

    def ready(self) -> None:
        """Give every metric ``prune_organizations`` deletes its DELETED feed entry."""
        from arkitekt_service.server.prune import pruning

        from metric import logic, models

        pruning.connect(logic.record_pruned, sender=models.Metric, dispatch_uid="metric-pruned")

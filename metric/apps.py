from django.apps import AppConfig


class MetricConfig(AppConfig):
    """Metrics: values recorded about mikro's data, immutable, and fed to whoever reads them."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "metric"

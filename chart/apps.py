from django.apps import AppConfig


class ChartConfig(AppConfig):
    """Charts: data laid out along one metric axis. A composition over core's data layer."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chart"

    def ready(self) -> None:
        """Tell the data layer that a space may be a chart's world.

        The same call ``core`` makes for scenes. It is what makes ``deleteCoordinateSystem``
        refuse a space a chart is laid out over, and what keeps the orphan sweep from
        collecting one -- neither of which knows this app exists.
        """
        from chart import models
        from core.logic import compositions

        compositions.register_composition(models.Chart, world_relation="charts", noun="chart")

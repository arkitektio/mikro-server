from django.apps import AppConfig


class GridConfig(AppConfig):
    """Grids: data looked along by its members. A composition over core's data layer."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "grid"

    def ready(self) -> None:
        """Tell the data layer that a space may be a grid's world, and which lenses a grid draws.

        The same call ``core`` makes for scenes and ``chart`` for charts: it is what makes
        ``deleteCoordinateSystem`` refuse a space a grid is laid out over, keeps the orphan sweep
        from collecting one, and lists a grid among a table's `visualizations`.
        """
        from core.logic import compositions
        from grid import models

        compositions.register_composition(models.Grid, world_relation="grids", noun="grid", lens_relation="layers__lens")

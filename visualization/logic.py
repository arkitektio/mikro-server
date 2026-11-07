"""Which composition shows a space, and the bootstrap that makes it.

Nothing here decides what a scene, a chart or a grid *is*. The choice is read off the rules
that already gate them -- ``chart_axis``, ``grid_axes`` and the navigable-axes check of
``create_scene`` -- so a space is charted exactly when ``createChart`` would take it, gridded
exactly when ``createGrid`` would, and staged as a scene exactly when ``createScene`` would.
"""

from typing import Literal

from django.db import transaction

from chart import enums as chart_enums
from chart import models as chart_models
from chart.logic import chart as chart_logic
from core import enums as core_enums
from core import models
from core.creation import CreationContext
from core.inputs.coords import ScenePolicyInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import scene as scene_logic
from grid import enums as grid_enums
from grid import models as grid_models
from grid.logic import grid as grid_logic

Kind = Literal["CHART", "GRID", "SCENE"]


def kind_for(system: models.CoordinateSystem) -> Kind:
    """The composition that shows this space, or the refusal when neither can.

    A chart when the space is one a chart can be laid along: exactly one axis, metric, with a
    unit. That includes the one-axis space a scene would also accept -- a single line is an
    axis to read values off before it is a place to stand in. A grid when the space is two
    enumerating axes: members against members, genes against cells. Otherwise a scene, when
    there is a time or space axis to navigate. (One enumerating axis is a grid's too, a single
    column; it is not chosen here, because a table of enumerated objects is shown as a VALUES
    chart of its measurements first.)
    """
    try:
        chart_logic.chart_axis(system)
    except ValueError as not_a_chart:
        axes = list(system.axes.all())
        if len(axes) == 2 and all(axis.type in grid_logic.ENUMERATING_AXIS_TYPES for axis in axes):
            return "GRID"
        if system.axes.filter(type__in=coordinate_system_logic.NAVIGABLE_TYPES).exists():
            return "SCENE"
        raise ValueError(
            f"Coordinate system '{system.name}' can be shown as neither a chart, a grid nor a scene. {not_a_chart} "
            "It is not two enumerating axes a grid lays out, and it has no navigable (time/space) axes, so nothing could be placed in a scene over it."
        ) from not_a_chart
    return "CHART"


def auto_visualize(system: models.CoordinateSystem, *, name: str | None, ctx: CreationContext) -> "models.Scene | chart_models.Chart | grid_models.Grid":
    """A scene or a chart over an existing space, drawn from what already resides in it.

    Each bootstrap keeps its own defaults and its own reading of what resides in a space. The
    one policy set here is that a scene skips a source its world does not place affinely:
    nobody named that source, so refusing the whole build over it would be answering a
    question that was not asked.
    """
    shown_as = kind_for(system)
    if shown_as == "CHART":
        return chart_logic.bootstrap_chart_from_system(system, name=name, policy=chart_logic.Policy(), ctx=ctx)
    if shown_as == "GRID":
        return grid_logic.bootstrap_grid_from_system(system, name=name, policy=grid_logic.Policy(), ctx=ctx)
    return scene_logic.bootstrap_scene_from_system(system, ScenePolicyInputModel(skip_unplaceable=True), ctx, name=name)


#: The chart layer a lens of each kind is drawn as. A kind that is not here has none.
_CHART_LAYER_KIND: dict[type, str] = {
    models.ArrayLens: chart_enums.ChartLayerKindChoices.TRACE.value,
    models.TableLens: chart_enums.ChartLayerKindChoices.SERIES.value,
    models.AnnotationLens: chart_enums.ChartLayerKindChoices.ANNOTATION.value,
}


def _chart_of_values(lens: models.TableLens, *, name: str | None, ctx: CreationContext) -> chart_models.Chart | None:
    """A chart of a table lens' values, over its table's space: a scatter of its first two numeric attribute columns, or a histogram of its only one. None when it has none."""
    table = lens.table_dataset
    measured = [column.name for column in table.columns.all() if column.role == core_enums.ColumnRoleChoices.ATTRIBUTE.value and chart_logic.is_numeric(column)]
    if not measured:
        return None
    label = table.name if not lens.name else f"{table.name} · {lens.name}"
    with transaction.atomic():
        chart = chart_logic.create_chart(name=name or label, world=table.coordinate_system, ctx=ctx)
        if len(measured) > 1:
            chart_logic.create_layer(chart, kind=chart_enums.ChartLayerKindChoices.POINTS.value, source=lens, x_column=measured[0], y_column=measured[1], name=lens.name or table.name)
        else:
            chart_logic.create_layer(chart, kind=chart_enums.ChartLayerKindChoices.DISTRIBUTION.value, source=lens, value_column=measured[0], name=lens.name or table.name)
    return chart


def auto_visualize_lens(lens: models.Lens, *, name: str | None, ctx: CreationContext) -> "models.Scene | chart_models.Chart | grid_models.Grid":
    """A scene or a chart of one lens: its selection, and nothing else of its container.

    The lens-first twin of :func:`auto_visualize`, for the reason ``bootstrap_scene_from_lens``
    gives: which lens a layer reads is not a fact of the space, so showing a crop's or a
    window's space draws its whole container again. The choice between the two compositions is
    still read off the space the selection lives in, by the same rule. A scene is the one
    ``createSceneFromLens`` makes with its defaults; a chart is laid along the lens' own space
    with the lens as its one layer, and a lens no chart layer reads is refused in prose.
    """
    system = lens.coordinate_system
    try:
        shown_as = kind_for(system)
    except ValueError:
        # A table whose rows have no place -- enumerated objects and their measurements --
        # is still a table of values: shown as a chart of them, its first two measurements
        # against each other, or the distribution of its only one.
        if isinstance(lens, models.TableLens):
            chart = _chart_of_values(lens, name=name, ctx=ctx)
            if chart is not None:
                return chart
        raise
    if shown_as == "SCENE":
        return scene_logic.bootstrap_scene_from_lens(lens, ctx, name=name)
    if shown_as == "GRID":
        # Members against members: a sparse matrix or an array as a heatmap of its values, a
        # long-form table as cells coloured by its one numeric column.
        container = lens.container
        label = getattr(container, "name", None) or getattr(container, "label", None) or f"{type(container).__name__} {container.pk}"
        kind = grid_enums.GridLayerKindChoices.CELLS.value if isinstance(lens, models.TableLens) else grid_enums.GridLayerKindChoices.HEATMAP.value
        with transaction.atomic():
            grid = grid_logic.create_grid(name=name or (f"{label} · {lens.name}" if lens.name else label), world=system, ctx=ctx)
            grid_logic.create_layer(grid, kind=kind, source=lens, name=lens.name or label)
        return grid

    kind = next((kind for model, kind in _CHART_LAYER_KIND.items() if isinstance(lens, model)), None)
    if kind is None:
        raise ValueError(f"Lens {lens.pk} over {type(lens.container).__name__} {lens.container.pk} cannot be charted: no chart layer reads a {type(lens).__name__}.")
    container = lens.container
    label = getattr(container, "name", None) or getattr(container, "label", None) or f"{type(container).__name__} {container.pk}"
    with transaction.atomic():
        chart = chart_logic.create_chart(name=name or (f"{label} · {lens.name}" if lens.name else label), world=system, ctx=ctx)
        chart_logic.create_layer(chart, kind=kind, source=lens, name=lens.name or label)
    return chart

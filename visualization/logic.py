"""Which composition shows a space, and the bootstrap that makes it.

Nothing here decides what a scene or a chart *is*. The choice is read off the two rules that
already gate them -- ``chart_axis`` and the navigable-axes check of ``create_scene`` -- so a
space is charted exactly when ``createChart`` would take it, and staged as a scene exactly
when ``createScene`` would.
"""

from typing import Literal

from chart import models as chart_models
from chart.logic import chart as chart_logic
from core import models
from core.creation import CreationContext
from core.inputs.coords import ScenePolicyInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import scene as scene_logic

Kind = Literal["CHART", "SCENE"]


def kind_for(system: models.CoordinateSystem) -> Kind:
    """The composition that shows this space, or the refusal when neither can.

    A chart when the space is one a chart can be laid along: exactly one axis, metric, with a
    unit. That includes the one-axis space a scene would also accept -- a single line is an
    axis to read values off before it is a place to stand in. Otherwise a scene, when there
    is a time or space axis to navigate.
    """
    try:
        chart_logic.chart_axis(system)
    except ValueError as not_a_chart:
        if system.axes.filter(type__in=coordinate_system_logic.NAVIGABLE_TYPES).exists():
            return "SCENE"
        raise ValueError(
            f"Coordinate system '{system.name}' can be shown neither as a chart nor as a scene. {not_a_chart} "
            "And it has no navigable (time/space) axes, so nothing could be placed in a scene over it."
        ) from not_a_chart
    return "CHART"


def auto_visualize(system: models.CoordinateSystem, *, name: str | None, ctx: CreationContext) -> models.Scene | chart_models.Chart:
    """A scene or a chart over an existing space, drawn from what already resides in it.

    Each bootstrap keeps its own defaults and its own reading of what resides in a space. The
    one policy set here is that a scene skips a source its world does not place affinely:
    nobody named that source, so refusing the whole build over it would be answering a
    question that was not asked.
    """
    if kind_for(system) == "CHART":
        return chart_logic.bootstrap_chart_from_system(system, name=name, policy=chart_logic.Policy(), ctx=ctx)
    return scene_logic.bootstrap_scene_from_system(system, ScenePolicyInputModel(skip_unplaceable=True), ctx, name=name)

"""createTablePlot: a chart of a table's columns, in one call."""

import kante
import strawberry
from kante.types import Info
from pydantic import BaseModel

from chart import enums, types
from chart.logic import table_plot as table_plot_logic
from core import models as core_models
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.scoping import get_for_org


class CreateTablePlotInputModel(BaseModel):
    table: str
    lens: str | None = None
    kind: enums.ChartLayerKind
    x_column: str
    y_column: str | None = None
    z_column: str | None = None
    mark: enums.ChartMark | None = None
    group_column: str | None = None
    bins: int | None = None
    name: str | None = None


@prose_errors
@kante.pydantic_input(
    CreateTablePlotInputModel,
    description=(
        "Input for plotting a table's columns in one call: a VALUES chart over the table's space (whose rows, not drawn) and one POINTS or DISTRIBUTION layer through the table's "
        "whole lens or a window of it, in one transaction, so a refused layer makes nothing. `area` against `volume` is `{kind: POINTS, xColumn: \"area\", yColumn: "
        "\"volume\"}`; a histogram of `area` per `condition` is `{kind: DISTRIBUTION, xColumn: \"area\", groupColumn: \"condition\"}`"
    ),
)
class CreateTablePlotInput:
    """Input for creating a table plot."""

    table: strawberry.ID = strawberry.field(description="The table dataset to plot")
    lens: strawberry.ID | None = strawberry.field(default=None, description="A window of the table to draw. Omit for every row (the whole lens)")
    kind: enums.ChartLayerKind = strawberry.field(description="POINTS (a scatter of x against y, and z for depth) or DISTRIBUTION (a histogram or box of x)")
    x_column: str = strawberry.field(description="The numeric column drawn horizontally: a point's x, or the values a distribution summarises")
    y_column: str | None = strawberry.field(default=None, description="(POINTS) The numeric column drawn vertically")
    z_column: str | None = strawberry.field(default=None, description="(POINTS) The numeric column drawn as depth, for a three-dimensional scatter")
    mark: enums.ChartMark | None = strawberry.field(default=None, description="The look: MARKERS or LINE for points, HISTOGRAM or BOX for a distribution. Defaults to the first")
    group_column: str | None = strawberry.field(default=None, description="(DISTRIBUTION) A column that sorts rows -- a condition, a cluster -- or a long-form CHANNEL coordinate, drawn as one summary per value")
    bins: int | None = strawberry.field(default=None, description="(DISTRIBUTION, HISTOGRAM) How many bins")
    name: str | None = strawberry.field(default=None, description="The chart's name. Defaults to the table's and the columns'")


def create_table_plot(info: Info, input: CreateTablePlotInput) -> types.Chart:
    """Plot a table's columns: a VALUES chart over its space, and the layer."""
    model = input.to_pydantic()
    table = get_for_org(core_models.TableDataset, info, id=model.table)
    lens = get_for_org(core_models.TableLens, info, id=model.lens) if model.lens is not None else None
    settings = {"mark": model.mark.value} if model.mark is not None else {}
    return table_plot_logic.create_table_plot(
        table,
        kind=model.kind.value,
        lens=lens,
        x_column=model.x_column,
        y_column=model.y_column,
        z_column=model.z_column,
        group_column=model.group_column,
        bins=model.bins,
        name=model.name,
        ctx=CreationContext.from_info(info),
        **settings,
    )

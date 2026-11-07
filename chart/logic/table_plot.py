"""The "Create plot" button: a chart over one table's space, and one value layer, in one transaction.

Nothing new is decided here. A table's rows live in its space (its frame: an INDEX `cell_id`,
the synthetic `object`, a localization table's `(t, y, x)`) and carry values (its other
columns). A plot's world is that space -- whose rows, not drawn -- and the rows are drawn at the
values of the columns named, `area` against `volume`. The layer reads the table's whole lens or
a window of it, and :func:`chart.logic.chart.create_layer` makes every check it makes for any
value layer.
"""

from django.db import transaction

from chart import enums, models
from chart.logic import chart as chart_logic
from core import models as core_models
from core.creation import CreationContext
from core.logic import coordinate_system as coordinate_system_logic


def default_name(table: "core_models.TableDataset", *, kind: str, x_column: str, y_column: str | None, z_column: str | None = None) -> str:
    """What a plot is called when nobody names it: the table, and what is drawn against what."""
    if kind == enums.ChartLayerKindChoices.DISTRIBUTION.value:
        return f"{table.name}: distribution of {x_column}"
    against = " against ".join(name for name in (x_column, y_column, z_column) if name is not None)
    return f"{table.name}: {against}"


def create_table_plot(
    table: "core_models.TableDataset",
    *,
    kind: str,
    x_column: str,
    y_column: str | None,
    ctx: CreationContext,
    lens: "core_models.TableLens | None" = None,
    z_column: str | None = None,
    group_column: str | None = None,
    bins: int | None = None,
    name: str | None = None,
    **settings: object,
) -> "models.Chart":
    """A chart over one table's space, and its value layer through ``lens`` (the table's whole lens when omitted).

    POINTS reads ``x_column``/``y_column`` (and ``z_column``); a DISTRIBUTION reads
    ``x_column`` as its value column, the horizontal axis, and ``group_column``.
    """
    if kind not in enums.VALUE_KINDS:
        raise ValueError(f"A table plot draws a table's rows as POINTS or as a DISTRIBUTION, not as {kind.upper()}.")
    with transaction.atomic():
        if lens is None:
            lens = coordinate_system_logic.whole_lens(table)
        elif lens.table_dataset_id != table.pk:
            raise ValueError(f"Table lens {lens.pk} is a lens of another table; a table plot draws a lens of '{table.name}'.")
        # The world is whose rows: the table's own space, not drawn.
        chart = chart_logic.create_chart(
            name=name or default_name(table, kind=kind, x_column=x_column, y_column=y_column, z_column=z_column),
            world=table.coordinate_system,
            ctx=ctx,
        )
        if kind == enums.ChartLayerKindChoices.POINTS.value:
            columns = {"x_column": x_column, "y_column": y_column, "z_column": z_column}
            if group_column is not None or bins is not None:
                raise ValueError("`groupColumn` and `bins` are a distribution's; a POINTS plot reads `xColumn`, `yColumn` and `zColumn`.")
        else:
            if y_column is not None or z_column is not None:
                raise ValueError("A DISTRIBUTION summarises one column, `xColumn`; `yColumn` and `zColumn` are a POINTS plot's.")
            columns = {"value_column": x_column, "group_column": group_column, "bins": bins}
        chart_logic.create_layer(chart, kind=kind, source=lens, name=lens.label, **columns, **settings)
    return chart

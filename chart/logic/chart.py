"""What a chart is and what may be drawn in one. The rules, and the writers that obey them.

A chart draws values as position. **Every chart has a world -- the domain its layers' rows
live in -- and at most one axis of it is drawn.** An AXIS chart's world is one metric axis,
and its TRACE, SERIES and ANNOTATION layers are laid along it. A VALUES chart's world is
whose rows it draws (a table's space, not drawn); its POINTS and DISTRIBUTION layers are
placed into it and drawn at the values of the columns they name. For a value layer the
questions are: is its table placed in the world, do its columns exist and hold numbers, and
do their units agree with the chart's other layers.

For a placed layer, three questions decide whether it may exist, asked in this order:

1. **Is the data placed in the chart's world?** Mikro's strict rule
   (:func:`core.logic.graph.assert_placeable_in`, rfc10): a route of edges that composes
   into one affine map. Reaching the world is not enough.
2. **Which of the data's axes runs along the chart?** Not a setting. It is the one source
   axis the composed map reads the world's axis from (:func:`along_axis_of`), so it is the
   edges that say it, and a layer has nowhere to say otherwise.
3. **Does the data have the shape its kind reads?** A trace needs that axis free and at most
   one enumerating axis beside it; a series needs a numeric value column.
"""

from dataclasses import dataclass

from django.db import transaction

from chart import enums, models
from core import enums as core_enums
from core import models as core_models
from core.creation import CreationContext
from core.inputs.coords import AxisInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from core.logic import placement
from kanne_server import scalars as kanne_scalars

#: The axis types that measure, and the ones that enumerate. A chart's axis is metric, and
#: so is the axis a trace is read along; an enumerating axis is the one a trace may leave
#: free beside it. Stated in ``core``, where a dataset's TRACE spec reads the same two sets:
#: what the spec calls a trace and what a chart draws whole cannot come apart.
METRIC_AXIS_TYPES = coords_logic.METRIC_AXIS_TYPES
ENUMERATING_AXIS_TYPES = coords_logic.ENUMERATING_AXIS_TYPES

#: The name of the value axis in a chart-minted drawing space.
VALUE_AXIS_NAME = "value"

# --- the world ----------------------------------------------------------------


def chart_axis(world: "core_models.CoordinateSystem") -> "core_models.Axis":
    """The one axis a chart over this space is laid out along, or the refusal if it has none.

    Exactly one axis, because a chart has one direction to lay data along and a second axis
    in its world would be a direction nothing here draws. Carrying a unit, because the axis
    is what positions are read off and a pixel index is not a position. Of a metric type,
    because laying data *along* an enumeration says nothing: there is no distance between
    object 3 and object 4.
    """
    axes = list(world.axes.all())
    if len(axes) != 1:
        raise ValueError(
            f"Coordinate system '{world.name}' has {len(axes)} axes ({', '.join(axis.name for axis in axes)}), and a chart is laid out along exactly one. "
            "Create a space with the one axis the data should be laid along, and register the data into it."
        )
    axis = axes[0]
    if axis.type not in METRIC_AXIS_TYPES:
        raise ValueError(
            f"Axis '{axis.name}' of coordinate system '{world.name}' is of type {axis.type}, which does not measure anything. "
            f"A chart is laid out along a metric axis: one of {', '.join(sorted(METRIC_AXIS_TYPES))}."
        )
    if axis.unit is None:
        raise ValueError(
            f"Axis '{axis.name}' of coordinate system '{world.name}' carries no unit, so positions along it are indices rather than measurements. "
            "A chart is laid out along an axis with a unit."
        )
    return axis


def create_chart(
    *,
    name: str,
    ctx: CreationContext,
    description: str | None = None,
    world: "core_models.CoordinateSystem | None" = None,
    axis: object | None = None,
    kind: str = enums.ChartKindChoices.AXIS.value,
) -> "models.Chart":
    """Create a chart over a world: an adopted existing space, or one minted from a single axis.

    Adopting lays the chart out over the space as it is. Minting is convenience only: the
    space it makes is an ordinary shared one, which other charts can adopt, which outlives
    every chart over it, and which only ``deleteCoordinateSystem`` removes. An AXIS chart's
    world must be one metric axis with a unit, which it draws; a VALUES chart's may be any
    space -- whose rows it draws -- and is not drawn.
    """
    if (world is None) == (axis is None):
        raise ValueError("A chart is created over exactly one of `coordinateSystem` (an existing space to adopt) or `axis` (the one axis of a space to create).")

    with transaction.atomic():
        if world is None:
            world = coordinate_system_logic.create_world_space(name=f"{name}/axis", axes=[axis], ctx=ctx)
        if kind == enums.ChartKindChoices.AXIS.value:
            chart_axis(world)
        return models.Chart.objects.create(
            name=name,
            description=description,
            kind=kind,
            world=world,
            creator=ctx.user,
            organization=ctx.organization,
        )


# --- placement ----------------------------------------------------------------


def layer_source_system(layer: "models.ChartLayer") -> "core_models.CoordinateSystem":
    """The space a layer's data is expressed in: its lens' system, whatever the kind."""
    return layer.lens.coordinate_system


def along_axis_of(condensed: "graph_logic.CondensedPlacement | None") -> str | None:
    """The source axis a composed placement reads the chart's axis from, or None.

    The composed map into a one-axis world is one row: a coefficient per source axis and a
    shift. The axis laid along the chart is the one with a coefficient. None when the map
    says nothing about the world's axis, and None when the row reads *several* source axes
    -- an oblique map lays a plane of the data onto the axis, and no single axis of the
    data runs along it.
    """
    if condensed is None or not condensed.total or len(condensed.matrix) != 1:
        return None
    coefficients = condensed.matrix[0][:-1]
    read = [name for name, coefficient in zip(condensed.input_axes, coefficients) if coefficient != 0]
    return read[0] if len(read) == 1 else None


def resolve_along_axis(chart: "models.Chart", source_system: "core_models.CoordinateSystem | None", *, what: str) -> str:
    """Refuse a source that is not laid along this chart's axis; return the axis that is.

    The strict gate first, so the three refusals it already tells apart -- nothing registered,
    declared unmappable, registered but with no closed form -- are the ones a caller reads.
    Then the two questions only a chart asks, both of the same composed map.
    """
    world = chart.world
    if chart.kind == enums.ChartKindChoices.VALUES.value:
        raise ValueError(f"Chart '{chart.name}' is a VALUES chart: it draws rows at their values, and has no drawn axis to lay {what} along. Draw it in an AXIS chart, or draw a table's rows as POINTS or a DISTRIBUTION here.")
    graph_logic.assert_placeable_in(world, source_system, destination=f"the axis of chart '{chart.name}'")

    axis = chart_axis(world)
    condensed = placement.PlacementGraph(world, seed_systems=[source_system.pk]).condensed(source_system)
    if condensed is None or not condensed.total:
        raise ValueError(
            f"{what} is registered into the space of chart '{chart.name}', but its registration says nothing about the axis '{axis.name}' the chart is laid out along. "
            f"Author a registration that maps one of its axes onto '{axis.name}'."
        )

    if not condensed.matrix:
        raise ValueError(
            f"{what} is placed on the axis '{axis.name}' of chart '{chart.name}' by a field, not by a map with a coefficient per axis, so no one of its axes runs along the chart. "
            "A trace, a series or an annotation is laid along a chart by exactly one of its axes."
        )
    along = along_axis_of(condensed)
    if along is None:
        coefficients = condensed.matrix[0][:-1]
        read = [name for name, coefficient in zip(condensed.input_axes, coefficients) if coefficient != 0]
        raise ValueError(
            f"{what} is placed in chart '{chart.name}' by a map that reads its axis '{axis.name}' from {len(read)} of the data's axes ({', '.join(read) or 'none'}). "
            "Data is laid along a chart by exactly one of its axes."
        )
    return along


# --- what each kind reads -----------------------------------------------------


def series_axis_of(lens: "core_models.Lens", along: str) -> str | None:
    """The enumerating axis a trace leaves free beside the one it is read along, or None.

    Never raises: the reading of a row that already exists. :func:`assert_trace` is the
    refusal.
    """
    types = {spec.name: spec.type for spec in lens.axis_specs}
    sizes = dict(zip(lens.axis_names, lens.shape_list))
    free = [name for name in lens.axis_names if name != along and sizes[name] > 1]
    if len(free) == 1 and types[free[0]] in ENUMERATING_AXIS_TYPES:
        return free[0]
    return None


def assert_trace(lens: "core_models.Lens", along: str) -> None:
    """Refuse a lens that is not a trace along this axis.

    A trace is one metric axis left free -- the one laid along the chart -- and optionally one
    enumerating axis left free beside it, drawn as one line per position. A lens never drops
    an axis, so "left free" is an extent above one and "fixed" is an extent of exactly one.

    Everything else must be fixed, because a second free metric axis is not several lines, it
    is a surface: that is a different reading of the data, and a different kind.
    """
    types = {spec.name: spec.type for spec in lens.axis_specs}
    sizes = dict(zip(lens.axis_names, lens.shape_list))

    if types[along] not in METRIC_AXIS_TYPES:
        raise ValueError(f"Lens {lens.pk} is laid along the chart by its axis '{along}', which is of type {types[along]} and does not measure anything. A trace is read along a metric axis.")
    if sizes[along] <= 1:
        raise ValueError(f"Lens {lens.pk} selects a single position along '{along}', the axis laid along the chart, so there is no trace to draw. Leave '{along}' free.")

    free = [name for name in lens.axis_names if name != along and sizes[name] > 1]
    surfaces = [name for name in free if types[name] not in ENUMERATING_AXIS_TYPES]
    if surfaces:
        raise ValueError(
            f"Lens {lens.pk} leaves {', '.join(f'{name!r} ({sizes[name]})' for name in surfaces)} free beside '{along}'. A trace is one line along '{along}', "
            "or one line per position of a single CHANNEL or INDEX axis; slice the lens to one position along every other axis."
        )
    if len(free) > 1:
        raise ValueError(
            f"Lens {lens.pk} leaves {len(free)} enumerating axes free ({', '.join(free)}). A trace draws one line per position of at most one of them; "
            "slice the lens to one position along the others."
        )


def is_numeric(column: "core_models.Column") -> bool:
    """Whether a column's stored type is a number."""
    return coords_logic.is_numeric_dtype(column.dtype)


def value_candidates(table: "core_models.TableDataset", along: str) -> list["core_models.Column"]:
    """The columns of a table that could be a series' value: numeric attributes."""
    return [column for column in table.columns.all() if column.name != along and column.role == core_enums.ColumnRoleChoices.ATTRIBUTE.value and is_numeric(column)]


def resolve_value_column(table: "core_models.TableDataset", along: str, requested: str | None) -> str:
    """The value column of a series: the one named, checked, or the only one there could be.

    Inferred only when exactly one column qualifies. With several, picking the first would be
    a chart of whichever column happened to be declared first.
    """
    candidates = value_candidates(table, along)
    if requested is None:
        if len(candidates) != 1:
            raise ValueError(
                f"Table dataset '{table.name}' has {len(candidates)} numeric attribute columns ({', '.join(column.name for column in candidates) or 'none'}), "
                "so the value of a series over it cannot be inferred. Name it with `valueColumn`."
            )
        return candidates[0].name

    if requested == along:
        raise ValueError(f"Column '{requested}' is the coordinate column table dataset '{table.name}' is laid along the chart by. A series' value is another column.")
    if requested not in {column.name for column in candidates}:
        raise ValueError(f"Column '{requested}' is not a numeric attribute column of table dataset '{table.name}'. A series' value is one of: {', '.join(column.name for column in candidates) or 'none'}.")
    return requested


# --- values -------------------------------------------------------------------


#: The roles a grouping column may have: what sorts rows rather than measures them.
_GROUPING_ROLES = frozenset({core_enums.ColumnRoleChoices.ATTRIBUTE.value, core_enums.ColumnRoleChoices.LABEL.value, core_enums.ColumnRoleChoices.GROUP_ID.value})


def _column(table: "core_models.TableDataset", name: str, *, role: str) -> "core_models.Column":
    column = next((column for column in table.columns.all() if column.name == name), None)
    if column is None:
        raise ValueError(f"Table dataset '{table.name}' has no column '{name}' to read as {role}; its columns are {', '.join(column.name for column in table.columns.all())}.")
    return column


def numeric_column(table: "core_models.TableDataset", name: str, *, role: str) -> "core_models.Column":
    """A column a value layer reads a position from: it exists, and holds numbers."""
    column = _column(table, name, role=role)
    if not is_numeric(column):
        raise ValueError(f"Column '{name}' of table dataset '{table.name}' holds {column.dtype}, not numbers, so it has no value to draw as {role}. A text column is what a distribution groups by.")
    return column


def grouping_column(table: "core_models.TableDataset", name: str) -> "core_models.Column":
    """A column a distribution draws one summary per value of: what sorts rows -- a condition, a cluster -- or a long-form channel."""
    column = _column(table, name, role="the grouping")
    sorts = column.role in _GROUPING_ROLES and (column.role != core_enums.ColumnRoleChoices.ATTRIBUTE.value or not is_numeric(column))
    channel = column.axis_type == core_enums.AxisTypeChoices.CHANNEL.value
    if not (sorts or channel):
        raise ValueError(
            f"Column '{name}' of table dataset '{table.name}' is a {'numeric ' if is_numeric(column) else ''}{column.role} column; a distribution is drawn once per value of a "
            "column that sorts rows -- a text or boolean ATTRIBUTE, a LABEL, a GROUP_ID -- or of a long-form CHANNEL coordinate. A measurement has no values to count."
        )
    return column


def _units_agree(first: "str | None", second: "str | None") -> bool:
    """Whether two declared units name one dimension: both absent, or both present and convertible."""
    if first is None or second is None:
        return first is None and second is None
    registry = kanne_scalars.get_registry()
    try:
        return dict(registry.Unit(kanne_scalars.normalize_compact_units(first)).dimensionality) == dict(registry.Unit(kanne_scalars.normalize_compact_units(second)).dimensionality)
    except Exception:  # noqa: BLE001 - an unparseable stored unit agrees with nothing
        return False


def _bindings(kind: str, *, x: str | None, y: str | None, z: str | None, value: str | None) -> dict[str, str | None]:
    """The position a value layer reads each of its columns into: a distribution's value is its horizontal axis."""
    if kind == enums.ChartLayerKindChoices.POINTS.value:
        return {"x": x, "y": y, "z": z}
    return {"x": value}


def assert_value_layer(
    chart: "models.Chart",
    *,
    kind: str,
    lens: "core_models.TableLens",
    x: str | None,
    y: str | None,
    z: str | None,
    value: str | None,
    group: str | None,
    bins: int | None,
    mark: str,
    updating: "models.ChartLayer | None" = None,
    check_placement: bool = True,
) -> dict[str, object]:
    """Check a value layer's column bindings and return them as the row's fields.

    The lens' rows are placed into the chart's world -- its own table's space, or one its table
    is registered or derived into, so two layers are about the same rows -- and drawn at the
    values of the columns named. Each must exist and hold numbers. The layers of one chart draw
    one kind, and agree on the units of each position -- `area` in µm² and `area_mm2` may
    share x; a volume may not, because a chart's axis is one quantity.

    ``updating`` is the layer being edited: it is compared with the chart's OTHER layers, never
    with its own old columns. ``check_placement`` is false when an edit keeps the lens: an
    existing layer is not re-litigated for a placement it already has.
    """
    table = lens.table_dataset
    if check_placement:
        graph_logic.assert_placeable_in(chart.world, lens.coordinate_system, destination=f"the world of chart '{chart.name}' (whose rows it draws)")
    if kind == enums.ChartLayerKindChoices.POINTS.value:
        if x is None or y is None:
            raise ValueError("A POINTS layer places each row at the values of `xColumn` and `yColumn` (and `zColumn` for depth); name both.")
        if value is not None or group is not None or bins is not None:
            raise ValueError("A POINTS layer reads `xColumn`, `yColumn` and `zColumn`; `valueColumn`, `groupColumn` and `bins` are a distribution's.")
        names = [name for name in (x, y, z) if name is not None]
        if len(set(names)) != len(names):
            raise ValueError(f"A POINTS layer reads a different column for each position, but names {names}.")
    else:
        if value is None:
            raise ValueError("A DISTRIBUTION summarises the values of one column; name it with `valueColumn`.")
        if x is not None or y is not None or z is not None:
            raise ValueError("A DISTRIBUTION reads `valueColumn` (and `groupColumn`); `xColumn`, `yColumn` and `zColumn` are points'.")
        if bins is not None:
            if mark != enums.ChartMarkChoices.HISTOGRAM.value:
                raise ValueError(f"`bins` is how a HISTOGRAM is cut; a {mark.upper()} has none.")
            if bins < 1:
                raise ValueError(f"A histogram has at least one bin, not {bins}.")
        if group is not None:
            grouping_column(table, group)
            if group == value:
                raise ValueError(f"A distribution of '{value}' is not grouped by '{value}' itself.")

    bound = _bindings(kind, x=x, y=y, z=z, value=value)
    units = {position: numeric_column(table, name, role=f"the {position} position").unit for position, name in bound.items() if name is not None}

    others = [other for other in chart.layers.all() if updating is None or other.pk != updating.pk]
    # The lens is polymorphic: read the table lenses as themselves, not through the base row.
    tables = {lens.pk: lens.table_dataset for lens in core_models.TableLens.objects.filter(pk__in=[other.lens_id for other in others]).select_related("table_dataset").prefetch_related("table_dataset__columns")}
    for other in others:
        if other.kind != kind:
            raise ValueError(f"Chart '{chart.name}' draws {other.kind.upper()} layers; a VALUES chart draws one kind, and a {kind.upper()} reads its axes differently. Make another chart.")
        other_table = tables.get(other.lens_id)
        if other_table is None:
            continue
        other_bound = _bindings(other.kind, x=other.x_column, y=other.y_column, z=other.z_column, value=other.value_column)
        for position, unit in units.items():
            name = other_bound.get(position)
            if name is None:
                continue
            other_unit = next((column.unit for column in other_table.columns.all() if column.name == name), None)
            if not _units_agree(unit, other_unit):
                raise ValueError(
                    f"Chart '{chart.name}' draws '{name}' ({other_unit or 'no unit'}) at its {position} position, and '{bound[position]}' is in {unit or 'no unit'}. "
                    "One axis of a chart is one quantity: draw columns whose units convert, or make another chart."
                )

    fields: dict[str, object] = {}
    if kind == enums.ChartLayerKindChoices.POINTS.value:
        fields.update(x_column=x, y_column=y, z_column=z)
    else:
        fields.update(value_column=value, group_column=group, bins=bins)
    return fields


# --- writers ------------------------------------------------------------------


def create_layer(
    chart: "models.Chart",
    *,
    kind: str,
    source: object,
    value_column: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    z_column: str | None = None,
    group_column: str | None = None,
    bins: int | None = None,
    order: int | None = None,
    **settings: object,
) -> "models.ChartLayer":
    """Draw a source in a chart, refusing one its kind cannot read.

    A placed kind needs the chart's world and its data laid along it; a value kind needs a
    VALUES chart whose world its rows are placed in, and columns that hold numbers. ``settings`` are the view-state columns,
    passed through as given; a look its kind does not take is refused, and an omitted one is
    the kind's first. Appended after the chart's last layer unless an order is named.
    """
    what = f"{type(source).__name__} {source.pk}"
    allowed = enums.MARKS.get(kind)
    mark = settings.get("mark")
    if mark is not None and allowed is not None and mark not in allowed:
        raise ValueError(f"A {kind.upper()} layer is drawn as one of {', '.join(look.upper() for look in allowed)}, not {str(mark).upper()}: a look of another kind.")
    if mark is None and allowed is not None:
        settings["mark"] = allowed[0]

    values: dict[str, object] = {}
    if kind in enums.VALUE_KINDS:
        if chart.kind != enums.ChartKindChoices.VALUES.value:
            raise ValueError(
                f"Chart '{chart.name}' is an AXIS chart, laid along '{chart_axis(chart.world).name}', and a {kind.upper()} layer draws rows at their values. "
                "Draw it in a VALUES chart over the table's space (`createChart(kind: VALUES)`, or `createTablePlot`)."
            )
        if not isinstance(source, core_models.TableLens):
            raise ValueError(f"{what} is not a table lens; a {kind.upper()} layer reads a table's rows and the values of its columns.")
        values.update(assert_value_layer(chart, kind=kind, lens=source, x=x_column, y=y_column, z=z_column, value=value_column, group=group_column, bins=bins, mark=str(settings["mark"])))
    else:
        if any(binding is not None for binding in (x_column, y_column, z_column, group_column, bins)):
            raise ValueError(f"A {kind.upper()} layer is placed along the chart's axis by the graph; column bindings are a POINTS or DISTRIBUTION layer's.")
        along = resolve_along_axis(chart, source.coordinate_system, what=what)

    if kind == enums.ChartLayerKindChoices.TRACE.value:
        if not isinstance(source, core_models.ArrayLens):
            raise ValueError(f"{what} is not an array lens; a trace reads an array along one of its axes.")
        assert_trace(source, along)
    elif kind == enums.ChartLayerKindChoices.SERIES.value:
        if not isinstance(source, core_models.TableLens):
            raise ValueError(f"{what} is not a table lens; a series reads a table's value column.")
        values["value_column"] = resolve_value_column(source.table_dataset, along, value_column)
    elif kind == enums.ChartLayerKindChoices.ANNOTATION.value and not isinstance(source, core_models.AnnotationLens):
        raise ValueError(f"{what} is not an annotation lens; an annotation layer draws a collection's marks.")

    if order is None:
        last = chart.layers.order_by("-order").values_list("order", flat=True).first()
        order = 0 if last is None else last + 1

    return models.ChartLayer.objects.create(chart=chart, kind=kind, order=order, **{enums.SOURCE_FIELD[kind]: source}, **values, **settings)


#: The bindings an update may clear, by the kind that has them: each is optional at creation.
CLEARABLE: dict[str, str] = {
    "z_column": enums.ChartLayerKindChoices.POINTS.value,
    "group_column": enums.ChartLayerKindChoices.DISTRIBUTION.value,
    "bins": enums.ChartLayerKindChoices.DISTRIBUTION.value,
}


def update_layer(
    layer: "models.ChartLayer",
    *,
    source: "core_models.Lens | None" = None,
    value_column: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    z_column: str | None = None,
    group_column: str | None = None,
    bins: int | None = None,
    clear: frozenset[str] = frozenset(),
    **settings: object,
) -> "models.ChartLayer":
    """Retune a layer: rebind its lens or columns, restyle it, and refuse what creating it so would refuse.

    The checks run on the MERGED state -- what is stored, with the patch over it -- so an edit
    to one binding is held to the others it now sits beside. A lens is re-placed only when it
    changes: resending the current one does not re-litigate an existing layer. A distribution
    turned into a BOX drops its stored ``bins``, which a box does not have; naming bins with a
    BOX is refused.
    """
    kind = str(layer.kind)
    chart = layer.chart
    rebound = source is not None and source.pk != layer.lens_id
    lens = source if source is not None else core_models.Lens.objects.with_sources().get(pk=layer.lens_id)
    what = f"{type(lens).__name__} {lens.pk}"

    allowed = enums.MARKS.get(kind)
    mark = settings.get("mark")
    if mark is not None and allowed is not None and mark not in allowed:
        raise ValueError(f"A {kind.upper()} layer is drawn as one of {', '.join(look.upper() for look in allowed)}, not {str(mark).upper()}: a look of another kind.")
    for field in clear:
        if CLEARABLE.get(field) != kind:
            raise ValueError(f"A {kind.upper()} layer has no `{field}` to clear.")
    named = {"value_column": value_column, "x_column": x_column, "y_column": y_column, "z_column": z_column, "group_column": group_column, "bins": bins}
    for field in clear:
        if named[field] is not None:
            raise ValueError(f"`{field}` is both set and cleared; do one.")

    fields: dict[str, object] = {}
    if kind in enums.VALUE_KINDS:
        if not isinstance(lens, core_models.TableLens):
            raise ValueError(f"{what} is not a table lens; a {kind.upper()} layer reads a table's rows and the values of its columns.")
        merged_mark = str(mark if mark is not None else layer.mark)

        def merged(field: str) -> object:
            if field in clear:
                return None
            given = named[field]
            return given if given is not None else getattr(layer, field)

        merged_bins = merged("bins")
        if merged_mark == enums.ChartMarkChoices.BOX.value and bins is None:
            merged_bins = None  # a box has no bins: the stored ones go with the histogram
        changed = rebound or bool(clear) or mark is not None or any(named[field] is not None for field in named)
        if changed:
            is_points = kind == enums.ChartLayerKindChoices.POINTS.value
            fields.update(
                assert_value_layer(
                    chart,
                    kind=kind,
                    lens=lens,
                    x=merged("x_column") if is_points else None,
                    y=merged("y_column") if is_points else None,
                    z=merged("z_column") if is_points else None,
                    value=None if is_points else merged("value_column"),
                    group=None if is_points else merged("group_column"),
                    bins=None if is_points else merged_bins,
                    mark=merged_mark,
                    updating=layer,
                    check_placement=rebound,
                )
            )
    else:
        bound = [field for field in ("x_column", "y_column", "z_column", "group_column", "bins") if named[field] is not None]
        if bound:
            raise ValueError(f"A {kind.upper()} layer is placed along the chart's axis by the graph; {', '.join(f'`{field}`' for field in bound)} are a POINTS or DISTRIBUTION layer's.")
        if value_column is not None and kind != enums.ChartLayerKindChoices.SERIES.value:
            raise ValueError(f"A {kind.upper()} layer reads no value column; a SERIES does.")
        if kind == enums.ChartLayerKindChoices.TRACE.value and rebound:
            if not isinstance(lens, core_models.ArrayLens):
                raise ValueError(f"{what} is not an array lens; a trace reads an array along one of its axes.")
            assert_trace(lens, resolve_along_axis(chart, lens.coordinate_system, what=what))
        elif kind == enums.ChartLayerKindChoices.SERIES.value and (rebound or value_column is not None):
            if not isinstance(lens, core_models.TableLens):
                raise ValueError(f"{what} is not a table lens; a series reads a table's value column.")
            along = resolve_along_axis(chart, lens.coordinate_system, what=what)
            # A new table keeps the stored column only if it has one by that name; else the one it can be.
            requested = value_column if value_column is not None else (layer.value_column if not rebound or any(c.name == layer.value_column for c in lens.table_dataset.columns.all()) else None)
            fields["value_column"] = resolve_value_column(lens.table_dataset, along, requested)
        elif kind == enums.ChartLayerKindChoices.ANNOTATION.value and rebound:
            if not isinstance(lens, core_models.AnnotationLens):
                raise ValueError(f"{what} is not an annotation lens; an annotation layer draws a collection's marks.")
            resolve_along_axis(chart, lens.coordinate_system, what=what)

    if rebound:
        layer.lens = lens
        fields["lens"] = lens
    fields.update(settings)
    for field, value in fields.items():
        setattr(layer, field, value)
    if fields:
        layer.save(update_fields=list(fields))
    return layer


def mint_annotation_collection(chart: "models.Chart", *, name: str | None, ctx: CreationContext) -> "core_models.AnnotationCollection":
    """A new drawing surface for a chart: a collection, its space, and the edge that places it.

    The space has two axes: the chart's own, by name and type, and a VALUE axis -- so a mark
    is drawn at a position *and a height*, on a trace rather than merely under it. The edge
    into the world names only the chart's axis, which is exactly what is true: the two agree
    about position, and the world has nothing to say about height.

    Exact by construction, so VALIDATED. The VALUE axis has no unit, as no VALUE axis does: a
    chart may hold values in several units, and which scale a height is read against is the
    viewer's binding, not a fact about the drawing.
    """
    if chart.kind == enums.ChartKindChoices.VALUES.value:
        raise ValueError(f"Chart '{chart.name}' is a VALUES chart, with no drawn axis to draw marks along. Annotate an AXIS chart.")
    axis = chart_axis(chart.world)
    collection = core_models.AnnotationCollection.objects.create(
        name=(name or f"{chart.name}/annotations")[:255],
        creator=ctx.user,
        organization=ctx.organization,
        **ctx.provenance_kwargs(),
    )
    system = graph_logic.create_collection_system(
        name=f"{collection.name}/drawing"[:255],
        axes=[
            AxisInputModel(name=axis.name, type=core_enums.AxisType(axis.type), long_name=axis.long_name, description=axis.description),
            AxisInputModel(name=VALUE_AXIS_NAME, type=core_enums.AxisType.VALUE),
        ],
        owner=collection,
        drawing=True,
        ctx=ctx,
    )
    graph_logic.create_identity_registration(
        input_system=system,
        world=chart.world,
        shared=[axis.name],
        name=f"{collection.name} -> {chart.name} (drawn)"[:255],
        validity=core_enums.PlacementValidityChoices.VALIDATED.value,
        ctx=ctx,
    )
    # After the registration, for the reason the scene's minting gives: the frame a box is
    # denominated in is asked once everything that could change the answer is written.
    graph_logic.record_bbox_frame(collection, system)
    return collection


# --- bootstrap ----------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """How a chart is drawn from what is already placed in a space."""

    #: At most this many layers.
    nchildren: int = 32


def _candidates(world: "core_models.CoordinateSystem", organization: object) -> list[tuple[str, object]]:
    """Everything strictly placed in a space that a chart layer could read, as ``(kind, container)``.

    Arrays first, then tables, then drawings -- data under the marks made on it. An array is
    offered whole: which slice of a larger array is the trace is a choice, and a bootstrap
    makes none.
    """
    placed = graph_logic.placeable_system_ids_in(world) | {world.pk}
    found: list[tuple[str, object]] = []
    for dataset in core_models.ArrayDataset.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.ChartLayerKindChoices.TRACE.value, dataset))
    for table in core_models.TableDataset.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.ChartLayerKindChoices.SERIES.value, table))
    for collection in core_models.AnnotationCollection.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.ChartLayerKindChoices.ANNOTATION.value, collection))
    return found


def bootstrap_chart_from_system(system: "core_models.CoordinateSystem", *, name: str | None, policy: Policy, ctx: CreationContext) -> "models.Chart":
    """A chart over an existing space, drawing what is already laid along its axis.

    Authors no edges. A source becomes a layer exactly when ``create_layer`` would accept it,
    and is left out otherwise: an image registered into the space is placed there and is
    still not a trace. Leaving it out is not an error to report -- nobody asked for it by
    name.
    """
    with transaction.atomic():
        chart = create_chart(name=name or system.name, world=system, ctx=ctx)
        drawn = 0
        for kind, container in _candidates(system, ctx.organization):
            if drawn >= policy.nchildren:
                break
            try:
                # A savepoint per candidate: a refusal part-way through one layer must not
                # poison the transaction the others are written in. The whole lens an array
                # is drawn through is the dataset's own, shared row -- nothing to take back.
                with transaction.atomic():
                    create_layer(chart, kind=kind, source=coordinate_system_logic.whole_lens(container), name=container.name)
            except ValueError:
                continue
            drawn += 1
    return chart

"""What a chart is and what may be drawn in one. The rules, and the writers that obey them.

A chart is a world with one metric axis, and a layer is a reading of data laid along it.
Three questions decide whether a layer may exist, and they are asked in this order:

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
from core.logic import graph as graph_logic
from core.logic import placement

#: The axis types that measure: a position along one is a quantity, and the distance between
#: two positions means something. A chart's axis is one of these, and so is the axis a trace
#: is read along. ``core`` has no single predicate for this -- it has several lists, each
#: answering a narrower question -- so the set is stated here, where it is the rule.
METRIC_AXIS_TYPES: frozenset[str] = frozenset(
    {
        core_enums.AxisTypeChoices.SPACE.value,
        core_enums.AxisTypeChoices.TIME.value,
        core_enums.AxisTypeChoices.MICROTIME.value,
        core_enums.AxisTypeChoices.SPECTRUM.value,
    }
)

#: The axis types that enumerate: their positions are members, not measurements. The one
#: axis a trace may leave free beside the one it is read along -- a line per channel, a line
#: per object.
ENUMERATING_AXIS_TYPES: frozenset[str] = frozenset(
    {
        core_enums.AxisTypeChoices.CHANNEL.value,
        core_enums.AxisTypeChoices.INDEX.value,
    }
)

#: The name of the value axis in a chart-minted drawing space.
VALUE_AXIS_NAME = "value"

#: The DuckDB type names a value column may have. A DECIMAL carries its precision in the
#: name, so it is matched by prefix.
_NUMERIC_DTYPES: frozenset[str] = frozenset(
    {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "REAL", "DOUBLE"}
)


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
) -> "models.Chart":
    """Create a chart over a world: an adopted existing space, or one minted from a single axis.

    Adopting lays the chart out along the space as it is. Minting is convenience only: the
    space it makes is an ordinary shared one, which other charts can adopt, which outlives
    every chart over it, and which only ``deleteCoordinateSystem`` removes.
    """
    if (world is None) == (axis is None):
        raise ValueError("A chart is created over exactly one of `coordinateSystem` (an existing space to adopt) or `axis` (the one axis of a space to create).")

    with transaction.atomic():
        if world is None:
            world = coordinate_system_logic.create_world_space(name=f"{name}/axis", axes=[axis], ctx=ctx)
        chart_axis(world)
        return models.Chart.objects.create(
            name=name,
            description=description,
            world=world,
            creator=ctx.user,
            organization=ctx.organization,
        )


# --- placement ----------------------------------------------------------------


def source_of(layer: "models.ChartLayer") -> "core_models.Lens | core_models.TableDataset | core_models.AnnotationCollection":
    """The row a layer reads: the one its kind names."""
    return getattr(layer, enums.SOURCE_FIELD[layer.kind])


def source_system_of(source: object) -> "core_models.CoordinateSystem | None":
    """The space a source's data is expressed in.

    A lens is the one source that may borrow its space: an unsliced lens lives in its
    dataset's grid. Everything else owns the space it is in.
    """
    if isinstance(source, core_models.Lens):
        return graph_logic.lens_source_system(source)
    return source.coordinate_system


def layer_source_system(layer: "models.ChartLayer") -> "core_models.CoordinateSystem | None":
    """The space a layer's data is expressed in."""
    return source_system_of(source_of(layer))


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
    graph_logic.assert_placeable_in(world, source_system, destination=f"the axis of chart '{chart.name}'")

    axis = chart_axis(world)
    condensed = placement.PlacementGraph(world, seed_systems=[source_system.pk]).condensed(source_system)
    if condensed is None or not condensed.total:
        raise ValueError(
            f"{what} is registered into the space of chart '{chart.name}', but its registration says nothing about the axis '{axis.name}' the chart is laid out along. "
            f"Author a registration that maps one of its axes onto '{axis.name}'."
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
    dtype = column.dtype.upper()
    return dtype in _NUMERIC_DTYPES or dtype.startswith("DECIMAL")


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


# --- writers ------------------------------------------------------------------


def create_layer(
    chart: "models.Chart",
    *,
    kind: str,
    source: object,
    value_column: str | None = None,
    order: int | None = None,
    **settings: object,
) -> "models.ChartLayer":
    """Draw a source in a chart, refusing one that is not laid along its axis or not its kind's shape.

    ``settings`` are the view-state columns, passed through as given. Appended after the
    chart's last layer unless an order is named.
    """
    what = f"{type(source).__name__} {source.pk}"
    along = resolve_along_axis(chart, source_system_of(source), what=what)

    values: dict[str, object] = {}
    if kind == enums.ChartLayerKindChoices.TRACE.value:
        assert_trace(source, along)
    elif kind == enums.ChartLayerKindChoices.SERIES.value:
        values["value_column"] = resolve_value_column(source, along, value_column)

    if order is None:
        last = chart.layers.order_by("-order").values_list("order", flat=True).first()
        order = 0 if last is None else last + 1

    return models.ChartLayer.objects.create(chart=chart, kind=kind, order=order, **{enums.SOURCE_FIELD[kind]: source}, **values, **settings)


def whole_lens(dataset: "core_models.ArrayDataset", ctx: CreationContext) -> "core_models.Lens":
    """The lens selecting all of a dataset: the one that exists, or a new one."""
    existing = dataset.lenses.filter(slices=[]).order_by("pk").first()
    return existing or coordinate_system_logic.create_lens(dataset, [], ctx)


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
                # poison the transaction the others are written in -- and it takes back the
                # whole lens made for an array that turned out not to be a trace.
                with transaction.atomic():
                    source = whole_lens(container, ctx) if kind == enums.ChartLayerKindChoices.TRACE.value else container
                    create_layer(chart, kind=kind, source=source, name=container.name)
            except ValueError:
                continue
            drawn += 1
    return chart

"""The chart vocabulary: what a layer reads, and how a line is drawn.

Kept apart from ``core.enums`` because none of it is the data layer's: a kind names a
*reading* of data that exists whether or not anything charts it.
"""

from enum import Enum

import strawberry
from django.db.models import TextChoices


class ChartKindChoices(TextChoices):
    """History only: what `Chart.kind` held until migration 0006 dropped it. Migrations 0002-0005 still import it.

    A chart has no kind any more -- each layer says where its positions come from
    (:class:`ChartLayerPositioning`). Do not use this in new code.
    """

    AXIS = "axis", "Axis (laid along its world's one measured axis)"
    VALUES = "values", "Values (rows drawn at the values of their columns; the world is whose rows)"


class ChartLayoutChoices(TextChoices):
    STACKED = "stacked", "Stacked (one row or panel per layer, each on its own scale)"
    SHARED = "shared", "Shared (layers measuring the same thing overlaid on one scale; the rest beside them)"
    OVERLAY = "overlay", "Overlay (every along-axis layer in one plot, each on its own scale; value layers lay out as shared)"


class ChartLayerKindChoices(TextChoices):
    TRACE = "trace", "Trace (an array read along one axis)"
    SERIES = "series", "Series (a table's value column against a coordinate column)"
    ANNOTATION = "annotation", "Annotation (drawn marks)"
    POINTS = "points", "Points (a table's rows at the values of two or three of its columns)"
    DISTRIBUTION = "distribution", "Distribution (a table's rows summarised by the values of one of its columns)"


class ChartMarkerShapeChoices(TextChoices):
    CIRCLE = "circle", "Circle"
    SQUARE = "square", "Square"
    TRIANGLE = "triangle", "Triangle"
    DIAMOND = "diamond", "Diamond"
    CROSS = "cross", "Cross"
    PLUS = "plus", "Plus"


@strawberry.enum(description="The symbol each marker is drawn as. A look: it changes nothing about what the layer reads")
class ChartMarkerShape(str, Enum):
    """The symbol a marker is drawn as."""

    CIRCLE = "circle"
    SQUARE = "square"
    TRIANGLE = "triangle"
    DIAMOND = "diamond"
    CROSS = "cross"
    PLUS = "plus"


class ChartMarkChoices(TextChoices):
    LINE = "line", "Line"
    MARKERS = "markers", "Markers"
    LINE_MARKERS = "line_markers", "Line and markers"
    STEPS = "steps", "Steps"
    HISTOGRAM = "histogram", "Histogram"
    BOX = "box", "Box"


@strawberry.enum(
    description=(
        "How a chart's layers share the screen, remembered with the chart. STACKED: one row (an along-axis layer) or one panel (a value layer) per layer, each on its own "
        "scale. SHARED: layers measuring the same thing overlaid on one scale -- along the axis, the same value unit; by their values, one kind, one mark and the same x and y "
        "units -- and the rest beside them. OVERLAY: every along-axis layer in one plot, each on its own scale; value layers lay out as SHARED, since there a layer's scale is "
        "its axis"
    )
)
class ChartLayout(str, Enum):
    """How a chart's layers share the screen."""

    STACKED = "stacked"
    SHARED = "shared"
    OVERLAY = "overlay"


@strawberry.enum(
    description=(
        "The kind of a chart layer, which says what it reads -- never how it looks. Three are laid along the chart's world axis by the graph (`ChartLayerPositioning` ALONG_AXIS): TRACE "
        "reads an array along one axis, SERIES one value column of a table along its coordinate, ANNOTATION drawn marks. Two are drawn by their values (BY_VALUES), their rows placed "
        "in the chart's world: POINTS at two or three columns (a scatter), DISTRIBUTION summarising one column (a histogram, a box), once per value of a "
        "grouping column. One chart may hold both. A line, markers, a histogram or a box is `ChartMark`, a setting on the layer."
    )
)
class ChartLayerKind(str, Enum):
    """The kind of a chart layer: what it reads."""

    TRACE = "trace"
    SERIES = "series"
    ANNOTATION = "annotation"
    POINTS = "points"
    DISTRIBUTION = "distribution"


@strawberry.enum(
    description=(
        "Where a chart layer's positions come from. ALONG_AXIS: laid along the chart's world axis by the graph's edges -- TRACE, SERIES and ANNOTATION, which need a world "
        "of one measured axis (`Chart.axis`). BY_VALUES: drawn at the values of the columns it names, its rows placed in the chart's world -- POINTS and DISTRIBUTION. One "
        "chart may hold both; its layout lays them out (along-axis layers share the axis, value layers their panels)"
    )
)
class ChartLayerPositioning(str, Enum):
    """Where a chart layer's positions come from."""

    ALONG_AXIS = "along_axis"
    BY_VALUES = "by_values"


@strawberry.enum(
    description=(
        "How a layer is drawn. A look, not a kind: changing it changes nothing about what the layer reads. A trace or a series is a LINE, MARKERS, both or STEPS; points "
        "are MARKERS or a LINE through them in row order sorted along x; a distribution is a HISTOGRAM or a BOX."
    )
)
class ChartMark(str, Enum):
    """How a layer is drawn."""

    LINE = "line"
    MARKERS = "markers"
    LINE_MARKERS = "line_markers"
    STEPS = "steps"
    HISTOGRAM = "histogram"
    BOX = "box"


#: The source foreign key each kind names, and the only one a layer of that kind may set.
#: Every kind reads through a lens; what differs is the lens kind the chart layer accepts.
SOURCE_FIELD: dict[str, str] = {
    ChartLayerKindChoices.TRACE.value: "lens",
    ChartLayerKindChoices.SERIES.value: "lens",
    ChartLayerKindChoices.ANNOTATION.value: "lens",
    ChartLayerKindChoices.POINTS.value: "lens",
    ChartLayerKindChoices.DISTRIBUTION.value: "lens",
}

#: The layer kinds laid along the chart's world axis (ALONG_AXIS): they need a world of one measured axis.
PLACED_KINDS: frozenset[str] = frozenset({ChartLayerKindChoices.TRACE.value, ChartLayerKindChoices.SERIES.value, ChartLayerKindChoices.ANNOTATION.value})

#: The layer kinds drawn by their values (BY_VALUES): rows placed in the chart's world, drawn at their columns' values.
VALUE_KINDS: frozenset[str] = frozenset({ChartLayerKindChoices.POINTS.value, ChartLayerKindChoices.DISTRIBUTION.value})

#: The looks each kind may take, the first its default. ANNOTATION draws marks, which have no look here.
MARKS: dict[str, tuple[str, ...]] = {
    ChartLayerKindChoices.TRACE.value: (ChartMarkChoices.LINE.value, ChartMarkChoices.MARKERS.value, ChartMarkChoices.LINE_MARKERS.value, ChartMarkChoices.STEPS.value),
    ChartLayerKindChoices.SERIES.value: (ChartMarkChoices.LINE.value, ChartMarkChoices.MARKERS.value, ChartMarkChoices.LINE_MARKERS.value, ChartMarkChoices.STEPS.value),
    ChartLayerKindChoices.POINTS.value: (ChartMarkChoices.MARKERS.value, ChartMarkChoices.LINE.value),
    ChartLayerKindChoices.DISTRIBUTION.value: (ChartMarkChoices.HISTOGRAM.value, ChartMarkChoices.BOX.value),
}

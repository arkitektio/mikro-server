"""The chart vocabulary: what a layer reads, and how a line is drawn.

Kept apart from ``core.enums`` because none of it is the data layer's: a kind names a
*reading* of data that exists whether or not anything charts it.
"""

from enum import Enum

import strawberry
from django.db.models import TextChoices


class ChartLayerKindChoices(TextChoices):
    TRACE = "trace", "Trace (an array read along one axis)"
    SERIES = "series", "Series (a table's value column against a coordinate column)"
    ANNOTATION = "annotation", "Annotation (drawn marks)"


class ChartMarkChoices(TextChoices):
    LINE = "line", "Line"
    MARKERS = "markers", "Markers"
    LINE_MARKERS = "line_markers", "Line and markers"
    STEPS = "steps", "Steps"


@strawberry.enum(
    description=(
        "The kind of a chart layer, which says what it reads -- never how it looks. TRACE reads an array along one axis; SERIES reads one column of a table against another; "
        "ANNOTATION reads drawn marks. A line, markers, both or steps is `ChartMark`, a setting on the layer."
    )
)
class ChartLayerKind(str, Enum):
    """The kind of a chart layer: what it reads."""

    TRACE = "trace"
    SERIES = "series"
    ANNOTATION = "annotation"


@strawberry.enum(description="How a trace or a series is drawn. A look, not a kind: changing it changes nothing about what the layer reads.")
class ChartMark(str, Enum):
    """How a trace or a series is drawn."""

    LINE = "line"
    MARKERS = "markers"
    LINE_MARKERS = "line_markers"
    STEPS = "steps"


#: The source foreign key each kind names, and the only one a layer of that kind may set.
SOURCE_FIELD: dict[str, str] = {
    ChartLayerKindChoices.TRACE.value: "lens",
    ChartLayerKindChoices.SERIES.value: "table_dataset",
    ChartLayerKindChoices.ANNOTATION.value: "annotation_collection",
}

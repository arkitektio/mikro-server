"""Charts and their layers. Nothing here stores where data sits: that is the graph's answer."""

from authentikate.models import Organization
from django.contrib.auth import get_user_model
from arkitekt_service.service.described import Described
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django_choices_field import TextChoicesField

from chart import enums


class Chart(Described):
    """A composition that shows how much: where on screen a point sits is a measurement, not a place.

    The second kind of composition beside a scene. A scene answers *where*: every screen axis
    is a world axis, data is placed by the graph's edges, and values are drawn as appearance
    (a colour, a size). A chart answers *how much*: values are drawn as position. Both name
    data through lenses and own none of it.

    **Every chart has a world -- the domain its layers live in -- and each layer says where its
    positions come from** (``enums.ChartLayerPositioning``). An *along-axis* layer (TRACE,
    SERIES, ANNOTATION) is laid along the world's one measured axis by the edges into it --
    intensity against `t` -- so it needs a world of exactly one measured axis with a unit. A
    *value* layer (POINTS, DISTRIBUTION) has its rows placed in the world -- a table's space,
    its INDEX axis or its frame -- and draws them at the values of the columns it names:
    nuclei at `area` against `volume`. Two layers in one world are about the same domain (one
    clock, the same rows); a chart may hold both kinds, and its ``layout`` lays them out: the
    along-axis layers share the axis, the value layers their panels. What positions a layer
    is a property of the layer, never of the chart.
    """

    name = models.CharField(max_length=255)
    description = models.CharField(max_length=1000, null=True, blank=True)
    layout = TextChoicesField(
        choices_enum=enums.ChartLayoutChoices,
        default=enums.ChartLayoutChoices.SHARED.value,
        db_default=enums.ChartLayoutChoices.SHARED.value,
        help_text="How the layers share the screen: one row or panel each (STACKED), overlaid where they measure the same thing (SHARED), or all in one plot (OVERLAY, AXIS only). A remembered view choice",
    )
    world = models.ForeignKey(
        "core.CoordinateSystem",
        on_delete=models.RESTRICT,
        related_name="charts",
        help_text=(
            "The domain this chart's layers live in: one metric, unit-carrying axis its along-axis layers are laid along, the space whose rows its value layers draw -- "
            "a table's -- or both at once. Never owned by the chart: several charts can share it, it outlives each of them, and deleting a chart never deletes it. "
            "RESTRICT: while a chart is laid out over a space, the space cannot be deleted"
        ),
    )
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, related_name="created_charts")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        ordering = ["-created_at"]
        indexes = [GinIndex(fields=["stored_descriptors"], name="chart_desc_gin", opclasses=["jsonb_path_ops"])]


class ChartLayer(models.Model):
    """One reading of data, drawn in a chart. View state only.

    One table discriminated by ``kind``, and a kind is defined by what it reads: every layer
    reads a lens -- a TRACE an array lens, a SERIES a table lens, an ANNOTATION an annotation
    lens. How a line looks is ``mark``, a setting.

    **No column says where the data sits.** There is no axis name, no offset and no scale
    here. Which of the source's axes runs along the chart, and by what scale and shift, is
    read from the path of edges between the source's space and the chart's world every time
    it is asked -- so two charts over one world cannot disagree about it, and refining a
    registration moves every layer that looks through it.

    Deleting a layer deletes nothing it drew. Deleting what it drew takes the layer.
    """

    chart = models.ForeignKey(Chart, on_delete=models.CASCADE, related_name="layers")
    kind = TextChoicesField(choices_enum=enums.ChartLayerKindChoices, help_text="What this layer reads. Fixed for the life of the row")
    name = models.CharField(max_length=255, null=True, blank=True, help_text="A human-readable name for the layer. Null when nobody has named it")
    visible = models.BooleanField(default=True, help_text="Whether the layer is drawn")
    order = models.IntegerField(default=0, help_text="Explicit drawing order, back to front")
    opacity = models.FloatField(default=1.0, help_text="Layer alpha (0..1)")
    color = models.JSONField(null=True, blank=True, help_text="The colour the layer is drawn in, as RGBA. Null to let the viewer choose")

    # --- the source: one lens, whatever the kind ---
    lens = models.ForeignKey("core.Lens", on_delete=models.CASCADE, related_name="chart_layers", help_text="The lens this layer reads through: an array lens for a trace, a table lens for a series, an annotation lens for marks")

    # --- series, distribution ---
    value_column = models.CharField(max_length=255, null=True, blank=True, help_text="(series) The numeric column read as the value, against the coordinate the table's placement lays along the chart's axis. (distribution) The numeric column whose values are summarised")

    # --- points: the columns whose values are the position ---
    x_column = models.CharField(max_length=255, null=True, blank=True, help_text="(points) The numeric column whose value is a row's horizontal position")
    y_column = models.CharField(max_length=255, null=True, blank=True, help_text="(points) The numeric column whose value is a row's vertical position")
    z_column = models.CharField(max_length=255, null=True, blank=True, help_text="(points) The numeric column whose value is a row's depth, for a three-dimensional scatter. Null for a flat one")

    # --- distribution ---
    group_column = models.CharField(max_length=255, null=True, blank=True, help_text="(distribution) The column whose values split the rows into one summary each -- a condition, a cluster, a long-form channel. Null for one summary over every row")
    bins = models.PositiveIntegerField(null=True, blank=True, help_text="(distribution, histogram) How many bins. Null lets the viewer choose")

    # --- style (trace, series) ---
    mark = TextChoicesField(choices_enum=enums.ChartMarkChoices, default=enums.ChartMarkChoices.LINE.value, help_text="(trace/series) How the values are drawn: a line, markers, both, or steps")
    line_width = models.FloatField(null=True, blank=True, help_text="(trace/series/points/distribution) How thick lines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_size = models.FloatField(null=True, blank=True, help_text="(trace/series/points) How big markers are, as a multiple of the chart's default: 1 is the default, 2 twice as big, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_shape = TextChoicesField(choices_enum=enums.ChartMarkerShapeChoices, null=True, blank=True, help_text="(trace/series/points) The symbol a marker is drawn as. Null to let the viewer choose (a circle)")


    class Meta:
        ordering = ["order", "id"]
        constraints = [
            # Each kind's columns, and only its own: a layer reads what its kind reads.
            models.CheckConstraint(
                condition=(
                    (Q(kind="points") & Q(x_column__isnull=False) & Q(y_column__isnull=False) & Q(value_column__isnull=True) & Q(group_column__isnull=True) & Q(bins__isnull=True))
                    | (Q(kind="distribution") & Q(value_column__isnull=False) & Q(x_column__isnull=True) & Q(y_column__isnull=True) & Q(z_column__isnull=True))
                    | (~Q(kind__in=["points", "distribution"]) & Q(x_column__isnull=True) & Q(y_column__isnull=True) & Q(z_column__isnull=True) & Q(group_column__isnull=True) & Q(bins__isnull=True))
                ),
                name="chart_layer_columns_per_kind",
            ),
            # One annotation layer per *lens* in a chart: one selection's marks are drawn once.
            # A window and the whole lens of one collection are two selections, and may both be.
            models.UniqueConstraint(
                fields=["chart", "lens"],
                condition=Q(kind="annotation"),
                name="one_chart_layer_per_annotation_lens",
            ),
        ]

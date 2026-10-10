"""Charts and their layers. Nothing here stores where data sits: that is the graph's answer."""

from authentikate.models import Organization
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django_choices_field import TextChoicesField
from koherent.fields import ProvenanceField

from chart import enums


class Chart(models.Model):
    """A composition of layers along one metric axis.

    The second kind of composition beside a scene. A scene is a place, drawn as an image; a
    chart is an axis, with values read off it. Both name data by id and own none of it, and
    both adopt a ``world`` they never own.

    The world is a coordinate system with exactly one axis, which carries a unit and is of a
    metric type. That axis is what data is laid out along -- a distance, a time, a
    wavelength -- and the chart assumes nothing about which.

    There are no units here, no extent and no placement: the unit is the world axis's, and
    where each layer's data sits along it is read through the edges into the world.
    """

    name = models.CharField(max_length=255)
    description = models.CharField(max_length=1000, null=True, blank=True)
    world = models.ForeignKey(
        "core.CoordinateSystem",
        on_delete=models.RESTRICT,
        related_name="charts",
        help_text=(
            "The space this chart lays its layers out along: a coordinate system with one metric, unit-carrying axis. Never owned by the chart: several charts can share "
            "it, it outlives each of them, and deleting a chart never deletes it. RESTRICT: while a chart is laid out over a space, the space cannot be deleted"
        ),
    )
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, related_name="created_charts")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    provenance = ProvenanceField()

    class Meta:
        ordering = ["-created_at"]


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

    # --- series ---
    value_column = models.CharField(max_length=255, null=True, blank=True, help_text="(series) The numeric column read as the value. The coordinate column is not stored: it is the column the table's placement lays along the chart's axis")

    # --- style (trace, series) ---
    mark = TextChoicesField(choices_enum=enums.ChartMarkChoices, default=enums.ChartMarkChoices.LINE.value, help_text="(trace/series) How the values are drawn: a line, markers, both, or steps")
    line_width = models.FloatField(null=True, blank=True, help_text="(trace/series) Line width in screen pixels. Null to let the viewer choose")
    marker_size = models.FloatField(null=True, blank=True, help_text="(trace/series) Marker size in screen pixels. Null to let the viewer choose")

    provenance = ProvenanceField()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            # One annotation layer per *lens* in a chart: one selection's marks are drawn once.
            # A window and the whole lens of one collection are two selections, and may both be.
            models.UniqueConstraint(
                fields=["chart", "lens"],
                condition=Q(kind="annotation"),
                name="one_chart_layer_per_annotation_lens",
            ),
        ]

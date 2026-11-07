"""Grids and their layers. Nothing here stores where data sits: that is the graph's answer."""

from arkitekt_service.service.described import Described
from authentikate.models import Organization
from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django_choices_field import TextChoicesField

from core import enums as core_enums
from grid import enums


class Grid(Described):
    """A composition that shows members in order: its screen axes enumerate, with no distance between positions.

    The third kind beside a scene and a chart. A scene shows *where* -- its screen axes are a
    place; a chart shows *how much* -- values are position. A grid shows *which*: rows and
    columns are the members of its world's one or two enumerating axes (INDEX, CHANNEL,
    CATEGORY) -- genes and cells, objects and channels, plate rows and columns -- and values
    are drawn as the colour of each cell. Row 3 comes after row 2; it is not one unit from it,
    which is why this is neither a scene nor a chart.

    Like both, it names data through lenses and owns none of it, and adopts its ``world`` -- the
    space whose members it lays out: a sparse dataset's, a long-form table's. Rows are the
    world's first axis, columns its second.
    """

    name = models.CharField(max_length=255)
    description = models.CharField(max_length=1000, null=True, blank=True)
    world = models.ForeignKey(
        "core.CoordinateSystem",
        on_delete=models.RESTRICT,
        related_name="grids",
        help_text=(
            "The space whose members this grid lays out: one or two enumerating axes (INDEX, CHANNEL, CATEGORY), rows then columns. Never owned by the grid: several "
            "compositions can share it, it outlives each of them, and deleting a grid never deletes it. RESTRICT: while a grid is laid out over a space, it cannot be deleted"
        ),
    )
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True, related_name="created_grids")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [GinIndex(fields=["stored_descriptors"], name="grid_desc_gin", opclasses=["jsonb_path_ops"])]


class GridLayer(models.Model):
    """One reading of data, drawn in a grid. View state only, beside what it reads.

    Every layer reads a lens placed in the grid's world, and covers each of its axes: a cell
    exists exactly where the data has a position along the rows and the columns. Deleting a
    layer deletes nothing it drew.
    """

    grid = models.ForeignKey(Grid, on_delete=models.CASCADE, related_name="layers")
    kind = TextChoicesField(choices_enum=enums.GridLayerKindChoices, help_text="What this layer reads. Fixed for the life of the row")
    name = models.CharField(max_length=255, null=True, blank=True, help_text="A human-readable name for the layer. Null when nobody has named it")
    visible = models.BooleanField(default=True, help_text="Whether the layer is drawn")
    order = models.IntegerField(default=0, help_text="Explicit drawing order, back to front")
    opacity = models.FloatField(default=1.0, help_text="Layer alpha (0..1)")

    lens = models.ForeignKey("core.Lens", on_delete=models.CASCADE, related_name="grid_layers", help_text="The lens this layer reads through: an array or sparse lens for a heatmap, a table lens for cells")

    # --- cells ---
    value_column = models.CharField(max_length=255, null=True, blank=True, help_text="(cells) The numeric column whose value colours each row's cell")

    # --- colour ---
    colormap = TextChoicesField(choices_enum=core_enums.ColorMapChoices, default=core_enums.ColorMapChoices.VIRIDIS.value, help_text="How a value becomes a colour")
    min = models.FloatField(null=True, blank=True, help_text="The value drawn at the bottom of the colormap. Null lets the viewer choose")
    max = models.FloatField(null=True, blank=True, help_text="The value drawn at the top of the colormap. Null lets the viewer choose")

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="cells") & Q(value_column__isnull=False)) | (Q(kind="heatmap") & Q(value_column__isnull=True)),
                name="grid_layer_value_column_per_kind",
            ),
        ]

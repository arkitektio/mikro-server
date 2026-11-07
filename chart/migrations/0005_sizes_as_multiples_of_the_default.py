"""Line widths and marker sizes were screen pixels; they are now multiples of the chart's default.

The viewer's defaults when they were pixels were 1.25 px for a line and 5 px for a marker, so
a stored pixel value becomes that many defaults: ``px / 1.25`` and ``px / 5``. A null stays
null -- "let the viewer choose" means the same in both readings.
"""

from django.db import migrations
from django.db.models import F

LINE_DEFAULT_PX = 1.25
MARKER_DEFAULT_PX = 5.0


def to_multiples(apps, schema_editor):
    ChartLayer = apps.get_model("chart", "ChartLayer")
    ChartLayer.objects.filter(line_width__isnull=False).update(line_width=F("line_width") / LINE_DEFAULT_PX)
    ChartLayer.objects.filter(marker_size__isnull=False).update(marker_size=F("marker_size") / MARKER_DEFAULT_PX)


def to_pixels(apps, schema_editor):
    ChartLayer = apps.get_model("chart", "ChartLayer")
    ChartLayer.objects.filter(line_width__isnull=False).update(line_width=F("line_width") * LINE_DEFAULT_PX)
    ChartLayer.objects.filter(marker_size__isnull=False).update(marker_size=F("marker_size") * MARKER_DEFAULT_PX)


class Migration(migrations.Migration):
    dependencies = [("chart", "0004_marker_shape_and_relative_sizes")]

    operations = [migrations.RunPython(to_multiples, to_pixels)]

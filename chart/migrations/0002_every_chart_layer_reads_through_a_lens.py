"""Every chart layer reads through a lens: an array lens for a trace, a table lens for a series, an annotation lens for marks."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q

from core.migrations._whole_lens import whole_lens_id


def repoint_chart_layers(apps, schema_editor) -> None:
    """Give every chart layer that read a container the container's whole lens."""
    ChartLayer = apps.get_model("chart", "ChartLayer")
    for field, container_model in (("table_dataset", "TableDataset"), ("annotation_collection", "AnnotationCollection")):
        for layer in ChartLayer.objects.filter(**{f"{field}__isnull": False}, lens__isnull=True).iterator():
            layer.lens_id = whole_lens_id(apps, container_model, getattr(layer, f"{field}_id"))
            layer.save(update_fields=["lens"])
    ChartLayer.objects.filter(lens__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_a_layer_keeps_only_its_lens"),
        ("chart", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="chartlayer", name="chart_layer_has_the_source_its_kind_names"),
        migrations.RemoveConstraint(model_name="chartlayer", name="one_chart_layer_per_annotation_collection"),
        migrations.RunPython(repoint_chart_layers, migrations.RunPython.noop),
    ]

"""The schema half of 0002: the two container keys go, `lens` is required, and one annotation layer per collection holds on the lens."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("chart", "0002_every_chart_layer_reads_through_a_lens"),
    ]

    operations = [
        migrations.RemoveField(model_name="historicalchartlayer", name="annotation_collection"),
        migrations.RemoveField(model_name="historicalchartlayer", name="table_dataset"),
        migrations.RemoveField(model_name="chartlayer", name="annotation_collection"),
        migrations.RemoveField(model_name="chartlayer", name="table_dataset"),
        migrations.AlterField(
            model_name="chartlayer",
            name="lens",
            field=models.ForeignKey(help_text="The lens this layer reads through: an array lens for a trace, a table lens for a series, an annotation lens for marks", on_delete=django.db.models.deletion.CASCADE, related_name="chart_layers", to="core.lens"),
        ),
        migrations.AlterField(
            model_name="historicalchartlayer",
            name="lens",
            field=models.ForeignKey(blank=True, db_constraint=False, help_text="The lens this layer reads through: an array lens for a trace, a table lens for a series, an annotation lens for marks", null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="core.lens"),
        ),
        migrations.AddConstraint(
            model_name="chartlayer",
            constraint=models.UniqueConstraint(fields=("chart", "lens"), condition=Q(kind="annotation"), name="one_chart_layer_per_annotation_collection"),
        ),
    ]

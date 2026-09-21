"""A coordinate anchor pins into an array, table or sparse dataset.

The third container, beside the array and the table: a sparse matrix's axes are
enumerations, so an anchor's coordinates are positions along them, keyed by axis name. The
exactly-one-container check grows a third arm; nothing needs backfilling.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_coordinate_anchor_table"),
    ]

    operations = [
        migrations.AddField(
            model_name="coordinateanchor",
            name="sparse",
            field=models.ForeignKey(
                blank=True,
                help_text="The sparse dataset this anchor pins into. Null otherwise",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="anchors",
                to="core.sparsedataset",
            ),
        ),
        migrations.AlterField(
            model_name="coordinateanchor",
            name="dataset",
            field=models.ForeignKey(
                blank=True,
                help_text="The array dataset this anchor pins into. Null otherwise",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="anchors",
                to="core.arraydataset",
            ),
        ),
        migrations.AlterField(
            model_name="coordinateanchor",
            name="table",
            field=models.ForeignKey(
                blank=True,
                help_text="The table dataset this anchor pins into. Null otherwise",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="anchors",
                to="core.tabledataset",
            ),
        ),
        migrations.AlterField(
            model_name="coordinateanchor",
            name="coordinates",
            field=models.JSONField(
                default=dict,
                help_text=(
                    "The coordinates this anchor is pinned to, keyed by axis name, e.g. {'c': 0, 't': 5}. For an array dataset these are level-0 pixel indices (its INTRINSIC space); "
                    "for a table dataset they are values of its coordinate columns, keyed by column name; for a sparse dataset they are positions along its enumerated axes. An omitted axis "
                    "means global along it"
                ),
            ),
        ),
        migrations.RemoveConstraint(
            model_name="coordinateanchor",
            name="coordinate_anchor_has_exactly_one_container",
        ),
        migrations.AddConstraint(
            model_name="coordinateanchor",
            constraint=models.CheckConstraint(
                condition=(
                    Q(("dataset__isnull", False), ("sparse__isnull", True), ("table__isnull", True))
                    | Q(("dataset__isnull", True), ("sparse__isnull", True), ("table__isnull", False))
                    | Q(("dataset__isnull", True), ("sparse__isnull", False), ("table__isnull", True))
                ),
                name="coordinate_anchor_has_exactly_one_container",
            ),
        ),
    ]

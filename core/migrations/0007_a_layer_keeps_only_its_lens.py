"""The schema half of 0005: the four container keys go, and `lens` is required."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_every_layer_draws_through_a_lens"),
    ]

    operations = [
        migrations.RemoveField(model_name="historicallayer", name="annotation_collection"),
        migrations.RemoveField(model_name="historicallayer", name="mesh_collection"),
        migrations.RemoveField(model_name="historicallayer", name="network_collection"),
        migrations.RemoveField(model_name="historicallayer", name="table_dataset"),
        migrations.RemoveField(model_name="layer", name="annotation_collection"),
        migrations.RemoveField(model_name="layer", name="mesh_collection"),
        migrations.RemoveField(model_name="layer", name="network_collection"),
        migrations.RemoveField(model_name="layer", name="table_dataset"),
        migrations.AlterField(
            model_name="historicallayer",
            name="lens",
            field=models.ForeignKey(blank=True, db_constraint=False, help_text="The lens this layer draws through: the selection over its container, and the space the data is placed from. An array lens for the array kinds, a table lens for point and track layers, a mesh, network or annotation lens for the collection kinds", null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="core.lens"),
        ),
        migrations.AlterField(
            model_name="layer",
            name="lens",
            field=models.ForeignKey(help_text="The lens this layer draws through: the selection over its container, and the space the data is placed from. An array lens for the array kinds, a table lens for point and track layers, a mesh, network or annotation lens for the collection kinds", on_delete=django.db.models.deletion.CASCADE, related_name="layers", to="core.lens"),
        ),
    ]

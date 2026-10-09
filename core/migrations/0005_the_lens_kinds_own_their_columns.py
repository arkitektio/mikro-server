"""The schema half of 0003: the base loses `slices` and `dataset`, the array kind takes them, and one row per selection is enforced."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentikate", "0007_user_profile"),
        ("core", "0004_the_rows_become_array_lenses"),
    ]

    operations = [
        migrations.RemoveField(model_name="lens", name="slices"),
        migrations.RemoveField(model_name="lens", name="dataset"),
        migrations.RenameField(model_name="arraylens", old_name="array_slices", new_name="slices"),
        migrations.RenameField(model_name="arraylens", old_name="source_dataset", new_name="dataset"),
        migrations.AlterField(
            model_name="arraylens",
            name="dataset",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.arraydataset"),
        ),
        migrations.AlterField(
            model_name="lens",
            name="organization",
            field=models.ForeignKey(help_text="The organization of the container this lens selects over", on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="authentikate.organization"),
        ),
        migrations.DeleteModel(name="HistoricalLens"),
        migrations.AddConstraint(model_name="arraylens", constraint=models.UniqueConstraint(fields=("dataset", "slices"), name="one_array_lens_per_selection")),
        migrations.AddConstraint(model_name="tablelens", constraint=models.UniqueConstraint(fields=("table_dataset", "windows"), name="one_table_lens_per_selection")),
        migrations.AddConstraint(model_name="sparselens", constraint=models.UniqueConstraint(fields=("sparse_dataset", "windows"), name="one_sparse_lens_per_selection")),
        migrations.AddConstraint(model_name="meshlens", constraint=models.UniqueConstraint(fields=("mesh_collection", "windows"), name="one_mesh_lens_per_selection")),
        migrations.AddConstraint(model_name="networklens", constraint=models.UniqueConstraint(fields=("network_collection", "windows"), name="one_network_lens_per_selection")),
        migrations.AddConstraint(model_name="annotationlens", constraint=models.UniqueConstraint(fields=("annotation_collection", "windows"), name="one_annotation_lens_per_selection")),
    ]

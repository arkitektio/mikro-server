"""A lens selects over any container, one row per selection, held to it by the database.

`Lens` becomes a polymorphic base with one child table per container kind; every row written
so far was an array lens and becomes an `ArrayLens`. Before the unique constraint lands, the
rows written under the old rule -- a lens per scene bootstrap, a lens per `createLens` call,
slices spelled as the caller spelled them -- are folded onto one row per (dataset, normalized
slices), repointing the layers that drew through the duplicates. A sliced duplicate's own space
goes with it when nothing else is attached to that space; otherwise the space stays, as a bare
space with the edge it had.

This is the schema half that comes first: the base's new columns and the six kind tables.
The rows move in 0004, and the base loses its old columns in 0005 -- three migrations, because
Postgres will not alter or index a table in the transaction that just rewrote its rows.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentikate", "0007_user_profile"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("chart", "0001_initial"),
        ("core", "0002_a_spec_counts_only_axes_with_extent"),
    ]

    operations = [
        migrations.AddField(
            model_name="lens",
            name="organization",
            field=models.ForeignKey(null=True, help_text="The organization of the container this lens selects over", on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="authentikate.organization"),
        ),
        migrations.AddField(
            model_name="lens",
            name="polymorphic_ctype",
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="polymorphic_%(app_label)s.%(class)s_set+", to="contenttypes.contenttype"),
        ),
        migrations.AlterField(
            model_name="lens",
            name="coordinate_system",
            field=models.ForeignKey(blank=True, help_text="The coordinate system this lens' selection is expressed in: a sliced array lens' own crop space, with a stored edge carrying the shift; otherwise the container's own system", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lenses", to="core.coordinatesystem"),
        ),
        # The child's two columns are born under other names: a child may not carry a field of
        # its base's name, and the base keeps `slices` and `dataset` until the rows have moved.
        migrations.CreateModel(
            name="ArrayLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("array_slices", models.JSONField(default=list, help_text="The selection this lens makes over its dataset, as a list of per-dimension slices in the one normalized spelling")),
                ("source_dataset", models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.arraydataset")),
            ],
            bases=("core.lens",),
        ),
        migrations.CreateModel(
            name="TableLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("table_dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.tabledataset")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="SparseLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("sparse_dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.sparsedataset")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="MeshLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("mesh_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.meshcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="NetworkLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("network_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.networkcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
        migrations.CreateModel(
            name="AnnotationLens",
            fields=[
                ("lens_ptr", models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to="core.lens")),
                ("windows", models.JSONField(default=list, help_text="The selection this lens makes, as a list of per-axis value windows in the container's own axes, in the one normalized spelling")),
                ("annotation_collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lenses", to="core.annotationcollection")),
            ],
            bases=("core.lens", models.Model),
        ),
    ]

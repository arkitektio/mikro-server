"""A lens always has a coordinate system: the column every writer has set since the beginning becomes NOT NULL. 0011 placed the rows."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_every_lens_is_placed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lens",
            name="coordinate_system",
            field=models.ForeignKey(
                help_text="The coordinate system this lens' selection is expressed in: a sliced array lens' own crop space, with a stored edge carrying the shift; otherwise the container's own system",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lenses",
                to="core.coordinatesystem",
            ),
        ),
    ]

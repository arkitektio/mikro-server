"""Every edge joins two systems: `parent` and `order` go, and the endpoints are required.

The schema half that comes last, after 0028 has deleted the only rows that ever lacked an
endpoint.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0028_the_children_become_steps")]

    operations = [
        migrations.RemoveField(model_name="transformation", name="parent"),
        migrations.RemoveField(model_name="transformation", name="order"),
        migrations.RemoveField(model_name="historicaltransformation", name="parent"),
        migrations.RemoveField(model_name="historicaltransformation", name="order"),
        migrations.AlterField(
            model_name="transformation",
            name="input",
            field=models.ForeignKey(default=None, help_text="The coordinate system this transformation maps from", on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.coordinatesystem"),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="transformation",
            name="output",
            field=models.ForeignKey(default=None, help_text="The coordinate system this transformation maps to", on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.coordinatesystem"),
            preserve_default=False,
        ),
    ]

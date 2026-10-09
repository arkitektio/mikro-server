"""A lens says who made it, when, and under which task.

The same four facts every container carries, on the polymorphic base so every kind has them.
Nullable, as the containers' are; the rows that exist are stamped in 0010 -- a sliced array
lens from the system it was made with, every other lens from its container -- which is the
data step, alone in its own migration.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("koherent", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0008_a_lens_has_a_name_and_may_nominate_a_scene"),
    ]

    operations = [
        migrations.AddField(
            model_name="lens",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now, help_text="When this selection was first asked for"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="lens",
            name="creator",
            field=models.ForeignKey(
                blank=True,
                help_text="Who made this selection: the first to ask for it. The container's creator for a whole lens, which exists from the container's creation",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_lenses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="lens",
            name="created_through",
            field=models.ForeignKey(blank=True, help_text="The task this selection was made under, if any", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_lenses", to="koherent.task"),
        ),
        migrations.AddField(
            model_name="lens",
            name="created_through_by",
            field=models.ForeignKey(
                blank=True,
                help_text="The assigner of the creating task, denormalized for fast filtering",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_lenses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]

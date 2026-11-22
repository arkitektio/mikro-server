"""Give every scene and annotation made before 0013 the time its history first records.

Alone in its migration (see CLAUDE.md, "Migrations and jobs"): a data step beside the
`AddField` that made the column would fail on a populated hub. Needs only the database, so it
is a data migration rather than a job. A row with no history (a bulk draw writes none) keeps
null: the migration's own time would be a creation time nobody had.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):  # noqa: ANN001 - Django's migration signature
    for model_name in ("Scene", "Annotation"):
        model = apps.get_model("core", model_name)
        history = apps.get_model("core", f"Historical{model_name}")
        first = history.objects.filter(id=OuterRef("pk")).order_by("history_date").values("history_date")[:1]
        model.objects.filter(created_at__isnull=True).update(created_at=Subquery(first))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_scene_annotation_created_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

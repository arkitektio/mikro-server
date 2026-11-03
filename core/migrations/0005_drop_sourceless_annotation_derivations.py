"""Links whose source was deleted while ``AnnotationDerivation.source`` was SET_NULL.

They say only that a shape was made from *something*, and the next migration makes the
source required. Alone in its migration (see CLAUDE.md, "Migrations and jobs").
"""

from django.db import migrations


def drop_sourceless(apps, schema_editor):  # noqa: ANN001 - Django's migration signature
    apps.get_model("core", "AnnotationDerivation").objects.filter(source__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0004_annotation_derivation")]

    operations = [migrations.RunPython(drop_sourceless, migrations.RunPython.noop)]

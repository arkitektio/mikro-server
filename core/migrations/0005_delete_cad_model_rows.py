"""Delete what 10.2.0 stored as CAD models, before 0006 drops their tables for the fixture ones.

Nothing is carried over: a CAD model was hours old when it became a fixture, and a fixture is
one `.glb` registered again in a moment. A lens is a polymorphic child of `Lens`, so deleting
the child rows takes their base rows with them, and the layers drawing through them go first.
The content types go too, so no row is left pointing at a model that no longer exists.
"""

from django.db import migrations


def delete_cad_models(apps, schema_editor) -> None:
    # By lens, not by `kind`: the kind field checks values against today's enum, which no longer
    # knows "cad_model".
    lenses = apps.get_model("core", "CadModelLens").objects.all()
    apps.get_model("core", "Layer").objects.filter(lens__in=lenses.values("pk")).delete()
    lenses.delete()
    apps.get_model("core", "CadModel").objects.all().delete()
    apps.get_model("core", "HistoricalCadModel").objects.all().delete()
    apps.get_model("contenttypes", "ContentType").objects.filter(app_label="core", model__in=["cadmodel", "cadmodellens", "historicalcadmodel"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_cad_model"),
    ]

    operations = [migrations.RunPython(delete_cad_models, migrations.RunPython.noop)]

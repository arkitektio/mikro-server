"""The version a caller typed becomes the collection's name.

It was the one label a collection had, so it is worth keeping as the one it now has. The data
step alone, between 0024 (the column it writes) and 0026 (the columns it reads).
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F


def version_becomes_name(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Copy the typed version into the name on both collections and their history twins."""
    for model_name in ("MeshCollection", "HistoricalMeshCollection", "NetworkCollection", "HistoricalNetworkCollection"):
        apps.get_model("core", model_name).objects.filter(name__isnull=True).update(name=F("version"))


class Migration(migrations.Migration):
    dependencies = [("core", "0024_a_collection_may_have_a_name")]

    operations = [migrations.RunPython(version_becomes_name, migrations.RunPython.noop)]

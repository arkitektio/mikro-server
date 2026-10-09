"""Every layer draws through a lens.

A point or track layer drew a table, a mesh, network or annotation layer its collection, each
through a foreign key of its own; the array kinds drew a lens. Now a layer of any kind draws
through the lens over its container -- the container's whole lens, which is the row that has
existed since the container was made. The four container keys go, and `lens` is required.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

from core.migrations._whole_lens import whole_lens_id


def repoint_layers(apps, schema_editor) -> None:
    """Give every layer that drew a container the container's whole lens."""
    Layer = apps.get_model("core", "Layer")
    for field, container_model in (("table_dataset", "TableDataset"), ("mesh_collection", "MeshCollection"), ("network_collection", "NetworkCollection"), ("annotation_collection", "AnnotationCollection")):
        for layer in Layer.objects.filter(**{f"{field}__isnull": False}, lens__isnull=True).iterator():
            layer.lens_id = whole_lens_id(apps, container_model, getattr(layer, f"{field}_id"))
            layer.save(update_fields=["lens"])
    # A layer with no source at all cannot be drawn and never could; it goes with its keys.
    Layer.objects.filter(lens__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0005_the_lens_kinds_own_their_columns"),
    ]

    operations = [
        migrations.RunPython(repoint_layers, migrations.RunPython.noop),
    ]

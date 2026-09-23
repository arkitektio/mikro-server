"""Unnest folders whose parent belongs to another organization.

``createFolder`` and ``ensureFolder`` took the parent as a raw id, so a folder could be nested
under another organization's folder, and ``parent`` / ``children`` then read across the
boundary. The mutations now look the parent up in the request's organization; this clears the
rows written before that. Unnesting only moves a folder to the root of its own organization,
so nothing is lost. There is no way back: the reverse is a no-op.

``attachUnstructuredMeta`` had the same hole for its ``schema``: a forward FK is never scoped
by a type's ``get_queryset``, so metadata pointing at another organization's schema kept
reading that schema out. The pointer is nullable and descriptive only, so it is cleared too.
"""

from django.db import migrations
from django.db.models import F


def unnest_cross_org_folders(apps, schema_editor):
    Folder = apps.get_model("core", "Folder")
    Folder.objects.filter(parent__isnull=False).exclude(parent__organization=F("organization")).update(parent=None)


def detach_cross_org_meta_schemas(apps, schema_editor):
    UnstructuredMeta = apps.get_model("core", "UnstructuredMeta")
    UnstructuredMeta.objects.filter(schema__isnull=False).exclude(schema__organization=F("file__organization")).update(schema=None)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_coordinate_anchor_sparse"),
    ]

    operations = [
        migrations.RunPython(unnest_cross_org_folders, migrations.RunPython.noop),
        migrations.RunPython(detach_cross_org_meta_schemas, migrations.RunPython.noop),
    ]

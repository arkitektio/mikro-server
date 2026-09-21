"""A coordinate anchor pins into an array dataset *or* a table dataset.

``dataset`` goes nullable, ``table`` arrives beside it, and a check constraint keeps exactly
one of them set. ``organization`` is denormalised onto the anchor because tenant scoping
(``core.scoping``) walks only non-nullable foreign keys, and with two nullable containers
there is no required path left to walk: it is backfilled from the dataset every existing
anchor has, then made required.

Written by hand: ``makemigrations`` would prompt for a one-off default on the required
``organization`` column, and the backfill has to run between "nullable" and "required".
The ``SET CONSTRAINTS ALL IMMEDIATE`` flushes the deferred foreign-key checks the backfill
queued, without which Postgres refuses the ``SET NOT NULL`` on the same table in this
transaction ("cannot ALTER TABLE because it has pending trigger events").
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Q, Subquery


def backfill_organization(apps, schema_editor):
    CoordinateAnchor = apps.get_model("core", "CoordinateAnchor")
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    CoordinateAnchor.objects.filter(organization__isnull=True).update(
        organization_id=Subquery(ArrayDataset.objects.filter(pk=OuterRef("dataset_id")).values("organization_id")[:1]),
    )
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [
        ("authentikate", "0006_alter_app_identifier_alter_release_unique_together"),
        ("core", "0011_description_embeddings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="coordinateanchor",
            name="dataset",
            field=models.ForeignKey(
                blank=True,
                help_text="The array dataset this anchor pins into. Null for a table anchor",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="anchors",
                to="core.arraydataset",
            ),
        ),
        migrations.AddField(
            model_name="coordinateanchor",
            name="table",
            field=models.ForeignKey(
                blank=True,
                help_text="The table dataset this anchor pins into. Null for an array anchor",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="anchors",
                to="core.tabledataset",
            ),
        ),
        migrations.AddField(
            model_name="coordinateanchor",
            name="organization",
            field=models.ForeignKey(
                help_text="The organization of the anchor's container, denormalised so tenant scoping has a required path to walk",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="coordinate_anchors",
                to="authentikate.organization",
            ),
        ),
        migrations.RunPython(backfill_organization, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="coordinateanchor",
            name="organization",
            field=models.ForeignKey(
                help_text="The organization of the anchor's container, denormalised so tenant scoping has a required path to walk",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="coordinate_anchors",
                to="authentikate.organization",
            ),
        ),
        migrations.AlterField(
            model_name="coordinateanchor",
            name="coordinates",
            field=models.JSONField(
                default=dict,
                help_text=(
                    "The coordinates this anchor is pinned to, keyed by axis name, e.g. {'c': 0, 't': 5}. For an array dataset these are level-0 pixel indices (its INTRINSIC space); "
                    "for a table dataset they are values of its coordinate columns, keyed by column name. An omitted axis means global along it"
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="coordinateanchor",
            constraint=models.CheckConstraint(
                condition=Q(("dataset__isnull", False), ("table__isnull", True)) | Q(("dataset__isnull", True), ("table__isnull", False)),
                name="coordinate_anchor_has_exactly_one_container",
            ),
        ),
    ]

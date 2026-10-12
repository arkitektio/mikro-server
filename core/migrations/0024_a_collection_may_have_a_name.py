"""A collection may have a name.

`MeshCollection` and `NetworkCollection` lose `version` -- a label a caller typed, derived from
nothing, standing in for an identity the store key already is and a history `provenance` already
carries -- and `spec_version`, a copy of what the store read off its manifest. In their place an
optional `name`; a collection without one is labelled by its store's key.

This is the schema half that comes first: the new column. 0025 copies the typed version into
it, 0026 drops the two old columns -- three migrations, because Postgres will not alter a table
in the transaction that just rewrote its rows.
"""

from django.db import migrations, models

_HELP = "What someone called this collection. Optional: a collection is identified by its store, and labelled by this or, failing that, by the store's key"


class Migration(migrations.Migration):
    dependencies = [("core", "0023_a_lens_states_its_spec")]

    operations = [
        migrations.AddField(model_name="meshcollection", name="name", field=models.CharField(blank=True, help_text=_HELP, max_length=255, null=True)),
        migrations.AddField(model_name="historicalmeshcollection", name="name", field=models.CharField(blank=True, help_text=_HELP, max_length=255, null=True)),
        migrations.AddField(model_name="networkcollection", name="name", field=models.CharField(blank=True, help_text=_HELP, max_length=255, null=True)),
        migrations.AddField(model_name="historicalnetworkcollection", name="name", field=models.CharField(blank=True, help_text=_HELP, max_length=255, null=True)),
    ]

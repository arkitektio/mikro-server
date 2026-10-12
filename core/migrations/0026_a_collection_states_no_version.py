"""A collection states no version of its own and no copy of its store's.

The schema half that comes last: `version` and `spec_version` go from both collections and
their history twins. `specVersion` is answered from the store from here on.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0025_a_typed_version_becomes_the_name")]

    operations = [
        migrations.RemoveField(model_name="meshcollection", name="version"),
        migrations.RemoveField(model_name="meshcollection", name="spec_version"),
        migrations.RemoveField(model_name="historicalmeshcollection", name="version"),
        migrations.RemoveField(model_name="historicalmeshcollection", name="spec_version"),
        migrations.RemoveField(model_name="networkcollection", name="version"),
        migrations.RemoveField(model_name="networkcollection", name="spec_version"),
        migrations.RemoveField(model_name="historicalnetworkcollection", name="version"),
        migrations.RemoveField(model_name="historicalnetworkcollection", name="spec_version"),
    ]

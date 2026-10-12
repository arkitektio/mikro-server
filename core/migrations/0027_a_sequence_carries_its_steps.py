"""A composite edge carries its steps, not child rows.

A SEQUENCE kept its map on child `Transformation` rows (a SCALE, then a TRANSLATION) that had
no endpoints, no identity a client used and a history of their own that nothing read -- so a
refinement wrote history on the child and the wrapper's `version` never moved. A BY_DIMENSION
kept its map in its own `params` and a projection of it as children, written for a client that
could read the numbers nowhere else. From here a SEQUENCE carries its steps as `steps`, a
BY_DIMENSION carries nothing but its `params`, and every row of the table joins two systems.

The schema half that comes first: the `steps` column. 0028 copies the children into it and
deletes them, 0029 drops `parent` and `order` and makes the endpoints non-null -- three
migrations, because Postgres will not alter a table in the transaction that just rewrote it.
"""

from django.db import migrations, models

_STEPS_HELP = "(SEQUENCE) The ordered steps, applied first to last, each {'kind': ..., 'params': {...}} and, for a FIELD step, 'field': the pk of the array's system. Empty for every other kind"
_PARAMS_HELP = "The transformation's parameters, keyed by kind: SCALE {'scale': [...]}, TRANSLATION {'translation': [...]}, AFFINE {'affine': [[...], ...]} (M x (N+1), rows outermost), BY_DIMENSION any of 'scale', 'translation', 'affine' over the axes it names (none: the identity on them), UNMAPPABLE {'reason': '...'} (optional, purely descriptive)"


class Migration(migrations.Migration):
    dependencies = [("core", "0026_a_collection_states_no_version")]

    operations = [
        migrations.AlterModelOptions(name="transformation", options={"ordering": ["id"]}),
        migrations.AddField(model_name="transformation", name="steps", field=models.JSONField(blank=True, default=list, help_text=_STEPS_HELP)),
        migrations.AddField(model_name="historicaltransformation", name="steps", field=models.JSONField(blank=True, default=list, help_text=_STEPS_HELP)),
        migrations.AlterField(model_name="transformation", name="params", field=models.JSONField(default=dict, help_text=_PARAMS_HELP)),
        migrations.AlterField(model_name="historicaltransformation", name="params", field=models.JSONField(default=dict, help_text=_PARAMS_HELP)),
    ]

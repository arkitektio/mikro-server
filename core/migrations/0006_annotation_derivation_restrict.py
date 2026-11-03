import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_drop_sourceless_annotation_derivations")]

    operations = [
        migrations.RemoveConstraint(model_name="annotationderivation", name="annotation_derived_once"),
        migrations.AlterField(
            model_name="annotationderivation",
            name="source",
            field=models.ForeignKey(
                help_text="The shape it was made from. It cannot be deleted while this shape stands, unless the delete cascades to it",
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="derived_links",
                to="core.annotation",
            ),
        ),
        migrations.AddConstraint(
            model_name="annotationderivation",
            constraint=models.UniqueConstraint(fields=("child", "source"), name="annotation_derived_once"),
        ),
    ]

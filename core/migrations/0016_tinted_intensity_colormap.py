from django.db import migrations


def tinted_layers_take_the_intensity_colormap(apps, schema_editor):
    """Bring the rows written under "both may be set, and the colour wins" to either/or.

    A tinted intensity layer used to keep whatever colormap was sent or defaulted beside the
    tint -- a map it never drew. The mutations now store INTENSITY there, so the rows that
    predate the rule are given the same value rather than left as the one place it is false.
    """
    Layer = apps.get_model("core", "Layer")
    Layer.objects.filter(kind="intensity", color__isnull=False).exclude(colormap="intensity").update(colormap="intensity")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_layer_white_balance'),
    ]

    operations = [
        # Nothing to undo: the colormap a tinted row held before was never drawn.
        migrations.RunPython(tinted_layers_take_the_intensity_colormap, migrations.RunPython.noop),
    ]

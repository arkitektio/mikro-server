# Generated by Django 3.2.8 on 2021-11-03 14:15

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('grunnlag', '0005_representation_omeroa'),
    ]

    operations = [
        migrations.RenameField(
            model_name='representation',
            old_name='omeroa',
            new_name='omero',
        ),
    ]
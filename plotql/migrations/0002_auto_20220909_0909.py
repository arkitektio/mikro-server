# Generated by Django 3.2.14 on 2022-09-09 09:09

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('plotql', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='plot',
            old_name='created',
            new_name='created_at',
        ),
        migrations.RenameField(
            model_name='plot',
            old_name='updated',
            new_name='updated_at',
        ),
    ]

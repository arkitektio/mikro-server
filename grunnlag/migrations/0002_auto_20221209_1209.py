# Generated by Django 3.2.16 on 2022-12-09 12:09

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('grunnlag', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='position',
            name='c',
        ),
        migrations.RemoveField(
            model_name='position',
            name='t',
        ),
    ]

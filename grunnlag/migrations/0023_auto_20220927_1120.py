# Generated by Django 3.2.14 on 2022-09-27 11:20

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('grunnlag', '0022_thumbnail_major_color'),
    ]

    operations = [
        migrations.CreateModel(
            name='Instrument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=1000, unique=True)),
                ('detectors', models.JSONField(blank=True, default=list, null=True)),
                ('dichroics', models.JSONField(blank=True, default=list, null=True)),
                ('filters', models.JSONField(blank=True, default=list, null=True)),
                ('lot_number', models.CharField(blank=True, max_length=1000, null=True)),
                ('manufacturer', models.CharField(blank=True, max_length=1000, null=True)),
                ('model', models.CharField(blank=True, max_length=1000, null=True)),
                ('serial_number', models.CharField(blank=True, max_length=1000, null=True)),
            ],
        ),
        migrations.RenameField(
            model_name='omero',
            old_name='physicalSize',
            new_name='physical_size',
        ),
        migrations.AddField(
            model_name='omero',
            name='imaging_environment',
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
        migrations.AddField(
            model_name='omero',
            name='objective_settings',
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
        migrations.AddField(
            model_name='representation',
            name='description',
            field=models.CharField(blank=True, max_length=1000, null=True),
        ),
        migrations.AddField(
            model_name='omero',
            name='instrument',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='grunnlag.instrument'),
        ),
    ]

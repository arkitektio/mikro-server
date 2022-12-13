# Generated by Django 3.2.16 on 2022-12-12 12:57

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('lok', '0001_initial'),
        ('grunnlag', '0003_auto_20221212_1213'),
    ]

    operations = [
        migrations.AddField(
            model_name='animal',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='animal_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='animal',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='animal_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='antibody',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='antibody_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='antibody',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='antibody_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='experiment',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='experiment_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='experiment',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='experiment_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='experimentalgroup',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='experimentalgroup_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='experimentalgroup',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='experimentalgroup_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='feature',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='feature_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='feature',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='feature_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='instrument',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='instrument_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='instrument',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='instrument_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='label',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='label_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='label',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='label_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='metric',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='metric_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='metric',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='metric_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='objective',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='objective_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='objective',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='objective_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='omero',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='omero_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='omero',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='omero_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='omerofile',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='omerofile_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='omerofile',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='omerofile_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='position',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='position_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='position',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='position_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='representation',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='representation_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='representation',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='representation_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='roi',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='roi_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='roi',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='roi_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='sample',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sample_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='sample',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sample_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='stage',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stage_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stage',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stage_created_through', to='lok.lokclient'),
        ),
        migrations.AddField(
            model_name='thumbnail',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='thumbnail_created_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='thumbnail',
            name='created_through',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='thumbnail_created_through', to='lok.lokclient'),
        ),
    ]

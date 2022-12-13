# Generated by Django 3.2.16 on 2022-12-09 11:50

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import taggit.managers


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('bord', '0001_initial'),
        ('taggit', '0005_auto_20220424_2025'),
        ('grunnlag', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='table',
            name='experiment',
            field=models.ForeignKey(blank=True, help_text='The Experiment this Table belongs to.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tables', to='grunnlag.experiment'),
        ),
        migrations.AddField(
            model_name='table',
            name='pinned_by',
            field=models.ManyToManyField(related_name='pinned_tables', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='table',
            name='representation',
            field=models.ForeignKey(blank=True, help_text='The Representation this Table belongs to', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tables', to='grunnlag.representation'),
        ),
        migrations.AddField(
            model_name='table',
            name='sample',
            field=models.ForeignKey(blank=True, help_text='Sample this table belongs to', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tables', to='grunnlag.sample'),
        ),
        migrations.AddField(
            model_name='table',
            name='tags',
            field=taggit.managers.TaggableManager(help_text='A comma-separated list of tags.', through='taggit.TaggedItem', to='taggit.Tag', verbose_name='Tags'),
        ),
    ]

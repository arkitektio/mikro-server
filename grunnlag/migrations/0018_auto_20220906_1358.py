# Generated by Django 3.2.14 on 2022-09-06 13:58

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('grunnlag', '0017_auto_20220905_1253'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='representation',
            name='omero',
        ),
        migrations.AlterField(
            model_name='feature',
            name='key',
            field=models.CharField(help_text='The sKeys', max_length=1000),
        ),
        migrations.CreateModel(
            name='OmeroMeta',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('planes', models.JSONField(blank=True, default=list, null=True)),
                ('channels', models.JSONField(blank=True, default=list, null=True)),
                ('scale', models.JSONField(blank=True, default=list, null=True)),
                ('physicalSize', models.JSONField(blank=True, default=list, null=True)),
                ('acquisition_date', models.DateTimeField(blank=True, null=True)),
                ('representation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='grunnlag.representation')),
            ],
        ),
    ]

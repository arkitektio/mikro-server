# Generated by Django 3.2.14 on 2022-09-05 12:53

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('grunnlag', '0016_representation_files'),
    ]

    operations = [
        migrations.CreateModel(
            name='Feature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(help_text='The Key', max_length=1000)),
                ('value', models.JSONField(blank=True, null=True)),
                ('creator', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('label', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='features', to='grunnlag.label')),
            ],
        ),
        migrations.DeleteModel(
            name='SizeFeature',
        ),
    ]
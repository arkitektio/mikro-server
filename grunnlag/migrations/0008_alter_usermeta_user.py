# Generated by Django 3.2.10 on 2022-03-23 14:48

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('grunnlag', '0007_auto_20220323_1434'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usermeta',
            name='user',
            field=models.OneToOneField(blank=True, on_delete=django.db.models.deletion.CASCADE, related_name='meta', to=settings.AUTH_USER_MODEL),
        ),
    ]

# Generated by Django 3.2.9 on 2021-11-19 13:35

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('grunnlag', '0009_omerofile_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='omerofile',
            name='type',
            field=models.CharField(choices=[('TIFF', 'Tiff'), ('JPEG', 'Jpeg'), ('MSR', 'MSR File'), ('CZI', 'Zeiss Microscopy File'), ('UNKNOWN', 'Unwknon File Format')], default='UNKNOWN', max_length=400),
        ),
    ]

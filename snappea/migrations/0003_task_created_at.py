# Generated by Django 4.2.16 on 2024-11-15 10:42

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("snappea", "0002_create_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
    ]

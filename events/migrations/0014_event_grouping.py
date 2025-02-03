# Generated by Django 4.2.18 on 2025-01-31 14:31

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("issues", "0001_initial"),  # This defines the Grouping model, which the below FKs to
        ("events", "0013_harmonize_foogested_at"),  # This is the previous migration

        # Previous version:
        # ("issues", "0007_alter_turningpoint_options"),  # seems unnecessary, given that Grouping is there since 0001
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="grouping",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="issues.grouping",
            ),
        ),
    ]

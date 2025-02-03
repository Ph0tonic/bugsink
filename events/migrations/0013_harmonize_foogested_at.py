# Generated by Django 4.2.13 on 2024-07-18 11:34

from django.db import migrations
from django.db.models import F


def harmonize_ingested_at(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.update(ingested_at=F('digested_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0012_event_ingested_at'),

        # The following migrations were previously explicitly listed as dependencies; as part of the "depend on
        # everything" pattern, but the actual RunPython command above does not depend on them so we simplify.
        # ('ingest', '0001_set_sqlite_wal'),
        # ('issues', '0006_issue_next_unmute_check'),
        # ('projects', '0008_project_next_quota_check'),
        # ('releases', '0001_initial'),
        # ('teams', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(harmonize_ingested_at),
    ]

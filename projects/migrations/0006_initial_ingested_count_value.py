# Generated by Django 4.2.13 on 2024-07-16 13:43

from django.db import migrations


def initial_ingested_count_value(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    for project in Project.objects.all():
        # this is the best guess we have; which should be good enough to avoid surprises on the small installed base.
        project.ingested_event_count = project.event_set.count()
        project.save()


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0003_initial'),  # this defines Event.project, and by implication event_set.count()
        ('projects', '0005_project_ingested_event_count'),  # this is the previous migration
    ]

    operations = [
        migrations.RunPython(initial_ingested_count_value),
    ]

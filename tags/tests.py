from unittest import TestCase as RegularTestCase
from django.test import TestCase as DjangoTestCase

from projects.models import Project
from issues.factories import get_or_create_issue, denormalized_issue_fields
from events.factories import create_event
from issues.models import Issue
from events.models import Event

from .models import store_tags
from .utils import deduce_tags
from .search import search_events, search_issues, parse_query


class DeduceTagsTestCase(RegularTestCase):

    def test_deduce_tags(self):
        self.assertEqual(deduce_tags({}), {})
        self.assertEqual(deduce_tags({"tags": {"foo": "bar"}}), {"foo": "bar"})

        # finally, a more complex example (more or less real-world)
        event_data = {
            "server_name": "server",
            "release": "1.0",
            "environment": "prod",
            "mechanism": {
                "type": "exception",
                "handled": False,
            },
            "transaction": "main",
            "contexts": {
                "trace": {
                    "trace_id": "1f2d3e4f5a6b5c8df9e0a1b2c3d4e5f",
                    "span_id": "9a8b7c6d5e4f3a2c",
                },
                "browser": {
                    "name": "Chrome",
                    "version": "88",
                },
                "os": {
                    "name": "Windows",
                    "version": "10",
                },
            },
        }
        self.assertEqual(deduce_tags(event_data), {
            "server_name": "server",
            "release": "1.0",
            "environment": "prod",
            "handled": "false",
            "transaction": "main",
            "trace": "1f2d3e4f5a6b5c8df9e0a1b2c3d4e5f",
            "trace.span": "9a8b7c6d5e4f3a2c",
            "trace.ctx": "1f2d3e4f5a6b5c8df9e0a1b2c3d4e5f.9a8b7c6d5e4f3a2c",
            "browser.name": "Chrome",
            "browser.version": "88",
            "browser": "Chrome 88",
            "os.name": "Windows",
            "os.version": "10",
            "os": "Windows 10",
        })


class StoreTagsTestCase(DjangoTestCase):
    # NOTE: I do quite a few assertNumQueries() in the below; super-brittle and opaque, of course. But at least the
    # brittle part is quick to fix (a single number) and provides a canary for performance regressions.

    def setUp(self):
        self.project = Project.objects.create(name="Test Project")
        self.issue, _ = get_or_create_issue(self.project)
        self.event = create_event(self.project, issue=self.issue)

    def test_store_0_tags(self):
        with self.assertNumQueries(0):
            store_tags(self.event, self.issue, {})

        self.assertEqual(self.event.tags.count(), 0)

    def test_store_1_tags(self):
        with self.assertNumQueries(7):
            store_tags(self.event, self.issue, {"foo": "bar"})

        self.assertEqual(self.event.tags.count(), 1)
        self.assertEqual(self.issue.tags.count(), 1)

        self.assertEqual(self.event.tags.first().value.value, "bar")

        self.assertEqual(self.issue.tags.first().count, 1)
        self.assertEqual(self.issue.tags.first().value.key.key, "foo")

    def test_store_5_tags(self):
        with self.assertNumQueries(7):
            store_tags(self.event, self.issue, {f"k-{i}": f"v-{i}" for i in range(5)})

        self.assertEqual(self.event.tags.count(), 5)
        self.assertEqual(self.issue.tags.count(), 5)

        self.assertEqual({"k-0", "k-1", "k-2", "k-3", "k-4"}, {tag.value.key.key for tag in self.event.tags.all()})
        self.assertEqual({"v-0", "v-1", "v-2", "v-3", "v-4"}, {tag.value.value for tag in self.event.tags.all()})

    def test_store_single_tag_twice_on_issue(self):
        store_tags(self.event, self.issue, {"foo": "bar"})
        store_tags(create_event(self.project, self.issue), self.issue, {"foo": "bar"})

        self.assertEqual(self.issue.tags.first().count, 2)
        self.assertEqual(self.issue.tags.first().value.key.key, "foo")


class SearchParserTestCase(RegularTestCase):

    def test_parser(self):
        # we don't actually do the below, empty queries are never parsed
        # self.assertEquals(({}, ""), parse_query(""))

        self.assertEquals(({}, "FindableException"), parse_query("FindableException"))
        self.assertEquals(({}, "findable value"), parse_query("findable value"))

        self.assertEquals(({"key": "value"}, ""),  parse_query("key:value"))
        self.assertEquals(
            ({"key": "value", "anotherkey": "anothervalue"}, ""),
            parse_query("key:value anotherkey:anothervalue"))

        self.assertEquals(
            ({"keys.may.have.dots": "values.may.have.dots.too"}, ""),
            parse_query("keys.may.have.dots:values.may.have.dots.too"))

        self.assertEquals(
            ({"key": "value"}, "some text goes here"),
            parse_query("key:value some text goes here"))

        self.assertEquals(
            ({}, "text  with  spaces  everywhere"),
            parse_query("text  with  spaces  everywhere"))

        self.assertEquals(
            ({}, "key: preceded by space"),
            parse_query("key: preceded by space"))

        self.assertEquals(
            ({"key": "quoted value"}, ""),
            parse_query('key:"quoted value"'))

        self.assertEquals(
            ({"key": "quoted value"}, "and further text"),
            parse_query('key:"quoted value" and further text'))

        # This is the kind of test that just documents "what is" rather than "what I believe is right". The weirdness
        # here is mostly the double space "on  both" which is the result of just cutting out the key:value bits. But...
        # I'm not invested in getting this more precise (yet), because this whole case is a bit weird. I'd much rather
        # point people in the direction of "put k:v at the beginning, and any free text at the end" (which is something
        # we could even validate on at some later point).
        self.assertEquals(
            ({"key": "value"}, "text on  both sides"),
            parse_query("text on key:value both sides"))


class SearchTestCase(DjangoTestCase):
    """'Integration'-test; assuming Tags are stored correctly in the DB, can we search for them?"""

    def setUp(self):
        self.project = Project.objects.create(name="Test Project")

        issue_with_tags_and_text = Issue.objects.create(project=self.project, **denormalized_issue_fields())
        event_with_tags_and_text = create_event(self.project, issue=issue_with_tags_and_text)

        issue_with_tags_no_text = Issue.objects.create(project=self.project, **denormalized_issue_fields())
        event_with_tags_no_text = create_event(self.project, issue=issue_with_tags_no_text)

        store_tags(event_with_tags_and_text, issue_with_tags_and_text, {f"k-{i}": f"v-{i}" for i in range(5)})
        store_tags(event_with_tags_no_text, issue_with_tags_no_text, {f"k-{i}": f"v-{i}" for i in range(5)})

        issue_without_tags = Issue.objects.create(project=self.project, **denormalized_issue_fields())
        event_without_tags = create_event(self.project, issue=issue_without_tags)

        for obj in [issue_with_tags_and_text, event_with_tags_and_text, issue_without_tags, event_without_tags]:
            obj.calculated_type = "FindableException"
            obj.calculated_value = "findable value"
            obj.save()

        issue_with_nothing = Issue.objects.create(project=self.project, **denormalized_issue_fields())
        create_event(self.project, issue=issue_with_nothing)

    def _test_search(self, search_x, clz):
        # we create 2 items with tags
        self.assertEqual(search_x(self.project, clz.objects.all(), "k-0:v-0").count(), 2)

        # non-matching tag: no results
        self.assertEqual(search_x(self.project, clz.objects.all(), "k-0:nosuchthing").count(), 0)

        # findable-by-text: 2 such items
        self.assertEqual(search_x(self.project, clz.objects.all(), "findable value").count(), 2)
        self.assertEqual(search_x(self.project, clz.objects.all(), "FindableException").count(), 2)

        # non-matching text: no results
        self.assertEqual(search_x(self.project, clz.objects.all(), "nosuchthing").count(), 0)
        self.assertEqual(search_x(self.project, clz.objects.all(), "k-0:v-0 nosuchthing").count(), 0)

        # findable-by-text, tagged: 1 such item
        self.assertEqual(search_x(self.project, clz.objects.all(), "findable value k-0:v-0").count(), 1)

    def test_search_events(self):
        self._test_search(search_events, Event)

    def test_search_issues(self):
        self._test_search(search_issues, Issue)

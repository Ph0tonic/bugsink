from collections import namedtuple
import json

from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.utils.safestring import mark_safe
from django.template.defaultfilters import date

from events.models import Event
from bugsink.decorators import project_membership_required, issue_membership_required
from compat.timestamp import format_timestamp

from .models import (
    Issue, IssueQuerysetStateManager, IssueStateManager, TurningPoint, TurningPointKind, add_periods_to_datetime)


MuteOption = namedtuple("MuteOption", ["for_or_until", "period_name", "nr_of_periods", "gte_threshold"])

# I imagine that we may make this configurable at the installation, organization and/or project level, but for now we
# just have a global constant.
GLOBAL_MUTE_OPTIONS = [
    MuteOption("for", "day", 1, None),
    MuteOption("for", "week", 1, None),
    MuteOption("for", "month", 1, None),
    MuteOption("for", "month", 3, None),

    MuteOption("until", "hour", 1, 5),
    MuteOption("until", "hour", 24, 5),
    MuteOption("until", "hour", 24, 100),
]


def _is_valid_action(action, issue):
    """We take the 'strict' approach of complaining even when the action is simply a no-op, because you're already in
    the desired state."""

    if issue.is_resolved:
        # any action is illegal on resolved issues (as per our current UI)
        return False

    if action.startswith("resolved_release:"):
        release_version = action.split(":", 1)[1]
        if release_version + "\n" in issue.events_at:
            return False

    elif action.startswith("mute"):
        if issue.is_muted:
            return False

        # TODO muting with a VBC that is already met should be invalid. See 'Exception("The unmute condition is already'

    elif action == "unmute":
        if not issue.is_muted:
            return False

    return True


def _q_for_invalid_for_action(action):
    """returns a Q obj of issues for which the action is not valid."""

    illegal_conditions = Q(is_resolved=True)  # any action is illegal on resolved issues (as per our current UI)

    if action.startswith("resolved_release:"):
        release_version = action.split(":", 1)[1]
        illegal_conditions = illegal_conditions | Q(events_at__contains=release_version + "\n")

    elif action.startswith("mute"):
        illegal_conditions = illegal_conditions | Q(is_muted=True)

    elif action == "unmute":
        illegal_conditions = illegal_conditions | Q(is_muted=False)

    return illegal_conditions


def _make_history(issue_or_qs, action, user):
    if action == "resolve":
        kind = TurningPointKind.FIRST_SEEN
    elif action.startswith("resolved"):
        kind = TurningPointKind.RESOLVED
    elif action.startswith("mute"):
        kind = TurningPointKind.MUTED
    elif action == "unmute":
        kind = TurningPointKind.UNMUTED
    else:
        raise ValueError(f"unknown action: {action}")

    if action.startswith("mute_for:"):
        mute_for_params = action.split(":", 1)[1]
        period_name, nr_of_periods, _ = mute_for_params.split(",")
        unmute_after = add_periods_to_datetime(timezone.now(), int(nr_of_periods), period_name)
        metadata = {"mute_for": {
            "period_name": period_name, "nr_of_periods": int(nr_of_periods),
            "unmute_after": format_timestamp(unmute_after)}}

    elif action.startswith("mute_until:"):
        mute_for_params = action.split(":", 1)[1]
        period_name, nr_of_periods, gte_threshold = mute_for_params.split(",")
        metadata = {"mute_until": {
            "period_name": period_name, "nr_of_periods": int(nr_of_periods), "gte_threshold": gte_threshold}}

    elif action == "mute":
        metadata = {"mute_unconditionally": True}

    elif action.startswith("resolved_release:"):
        release_version = action.split(":", 1)[1]
        metadata = {"resolved_release": release_version}
    elif action == "resolved_next":
        metadata = {"resolve_by_next": True}
    elif action == "resolve":
        metadata = {"resolved_unconditionally": True}
    else:
        metadata = {}

    now = timezone.now()
    if isinstance(issue_or_qs, Issue):
        TurningPoint.objects.create(
            issue=issue_or_qs, kind=kind, user=user, metadata=json.dumps(metadata), timestamp=now)
    else:
        TurningPoint.objects.bulk_create([
            TurningPoint(issue=issue, kind=kind, user=user, metadata=json.dumps(metadata), timestamp=now)
            for issue in issue_or_qs
        ])


def _apply_action(manager, issue_or_qs, action, user):
    _make_history(issue_or_qs, action, user)

    if action == "resolve":
        manager.resolve(issue_or_qs)
    elif action.startswith("resolved_release:"):
        release_version = action.split(":", 1)[1]
        manager.resolve_by_release(issue_or_qs, release_version)
    elif action == "resolved_next":
        manager.resolve_by_next(issue_or_qs)
    # elif action == "reopen":  # not allowed from the UI
    #     manager.reopen(issue_or_qs)
    elif action == "mute":
        manager.mute(issue_or_qs)
    elif action.startswith("mute_for:"):
        mute_for_params = action.split(":", 1)[1]
        period_name, nr_of_periods, _ = mute_for_params.split(",")
        unmute_after = add_periods_to_datetime(timezone.now(), int(nr_of_periods), period_name)
        manager.mute(issue_or_qs, unmute_after=unmute_after)

    elif action.startswith("mute_until:"):
        mute_for_params = action.split(":", 1)[1]
        period_name, nr_of_periods, gte_threshold = mute_for_params.split(",")

        manager.mute(issue_or_qs, unmute_on_volume_based_conditions=json.dumps([{
            "period": period_name,
            "nr_of_periods": int(nr_of_periods),
            "volume": int(gte_threshold),
        }]))
    elif action == "unmute":
        manager.unmute(issue_or_qs)


@project_membership_required
def issue_list(request, project, state_filter="open"):
    if request.method == "POST":
        issue_ids = request.POST.getlist('issue_ids[]')
        issue_qs = Issue.objects.filter(pk__in=issue_ids)
        illegal_conditions = _q_for_invalid_for_action(request.POST["action"])
        # list() is necessary because we need to evaluate the qs before any actions are actually applied (if we don't,
        # actions are always marked as illegal, because they are applied first, then checked (and applying twice is
        # illegal)
        unapplied_issue_ids = list(issue_qs.filter(illegal_conditions).values_list("id", flat=True))
        _apply_action(
            IssueQuerysetStateManager, issue_qs.exclude(illegal_conditions), request.POST["action"], request.user)

    else:
        unapplied_issue_ids = None

    d_state_filter = {
        "open": lambda qs: qs.filter(is_resolved=False, is_muted=False),
        "unresolved": lambda qs: qs.filter(is_resolved=False),
        "resolved": lambda qs: qs.filter(is_resolved=True),
        "muted": lambda qs: qs.filter(is_muted=True),
        "all": lambda qs: qs,
    }

    issue_list = d_state_filter[state_filter](
        Issue.objects.filter(project=project)
    ).order_by("-last_seen")

    return render(request, "issues/issue_list.html", {
        "project": project,
        "issue_list": issue_list,
        "state_filter": state_filter,
        "mute_options": GLOBAL_MUTE_OPTIONS,

        "unapplied_issue_ids": unapplied_issue_ids,

        # design decision: we statically determine some disabledness (i.e. choices that will never make sense are
        # disallowed), but we don't have any dynamic disabling based on the selected issues.
        "disable_resolve_buttons": state_filter in ("resolved"),
        "disable_mute_buttons": state_filter in ("resolved", "muted"),
        "disable_unmute_buttons": state_filter in ("resolved", "open"),
    })


@issue_membership_required
def issue_last_event(request, issue):
    last_event = issue.event_set.order_by("timestamp").last()

    return redirect(issue_event_stacktrace, issue_pk=issue.pk, event_pk=last_event.pk)


def _handle_post(request, issue):
    if _is_valid_action(request.POST["action"], issue):
        _apply_action(IssueStateManager, issue, request.POST["action"], request.user)
        issue.save()

    # note that if the action is not valid, we just ignore it (i.e. we don't show any error message or anything)
    # this is probably what you want, because the most common case of action-not-valid is 'it already happened
    # through some other UI path'. The only case I can think of where this is not the case is where you try to
    # resolve an issue for a specific release, and while you where thinking about that, it occurred for that
    # release. In that case it will probably stand out that your buttons don't become greyed out, and that the
    # dropdown no longer functions. already-true-vbc-unmute may be another exception to this rule.
    return HttpResponseRedirect(request.path_info)


def _get_event(issue, event_pk, ingest_order):
    if event_pk is not None:
        # we match on both internal and external id, trying internal first
        try:
            return Event.objects.get(pk=event_pk)
        except Event.DoesNotExist:
            return get_object_or_404(Event, issue=issue, event_id=event_pk)

    elif ingest_order is not None:
        return get_object_or_404(Event, issue=issue, ingest_order=ingest_order)
    else:
        raise ValueError("either event_pk or ingest_order must be provided")


@issue_membership_required
def issue_event_stacktrace(request, issue, event_pk=None, ingest_order=None):
    if request.method == "POST":
        return _handle_post(request, issue)

    event = _get_event(issue, event_pk, ingest_order)

    parsed_data = json.loads(event.data)

    # sentry/glitchtip have some code here to deal with the case that "values" is not present, and exception itself is
    # the list of exceptions, but we don't aim for endless backwards compat (yet) so we don't.
    exceptions = parsed_data["exception"]["values"] if "exception" in parsed_data else None

    # NOTE: I considered making this a clickable button of some sort, but decided against it in the end. Getting the UI
    # right is quite hard (https://ux.stackexchange.com/questions/1318) but more generally I would assume that having
    # your whole screen turned upside down is not something you do willy-nilly. Better to just have good defaults and
    # (possibly later) have this as something that is configurable at the user level.
    stack_of_plates = event.platform != "python"  # Python is the only platform that has chronological stacktraces

    if exceptions is not None and len(exceptions) > 0:
        if exceptions[-1].get('stacktrace') and exceptions[-1]['stacktrace'].get('frames'):
            exceptions[-1]['stacktrace']['frames'][-1]['raise_point'] = True

        if stack_of_plates:
            # NOTE manipulation of parsed_data going on here, this could be a trap if other parts depend on it
            # (e.g. grouper)
            exceptions = [e for e in reversed(exceptions)]
            for exception in exceptions:
                if not exception.get('stacktrace'):
                    continue
                exception['stacktrace']['frames'] = [f for f in reversed(exception['stacktrace']['frames'])]

    return render(request, "issues/stacktrace.html", {
        "tab": "stacktrace",
        "this_view": "event_stacktrace",
        "project": issue.project,
        "issue": issue,
        "event": event,
        "is_event_page": True,
        "parsed_data": parsed_data,
        "exceptions": exceptions,
        "stack_of_plates": stack_of_plates,
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })


@issue_membership_required
def issue_event_breadcrumbs(request, issue, event_pk=None, ingest_order=None):
    if request.method == "POST":
        return _handle_post(request, issue)

    event = _get_event(issue, event_pk, ingest_order)

    parsed_data = json.loads(event.data)

    return render(request, "issues/breadcrumbs.html", {
        "tab": "breadcrumbs",
        "this_view": "event_breadcrumbs",
        "project": issue.project,
        "issue": issue,
        "event": event,
        "is_event_page": True,
        "parsed_data": parsed_data,
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })


def _date_with_milis_html(timestamp):
    return mark_safe(
        date(timestamp, "j M G:i:s") + "." +
        '<span class="text-xs">' + date(timestamp, "u")[:3] + '</span>')


@issue_membership_required
def issue_event_details(request, issue, event_pk=None, ingest_order=None):
    if request.method == "POST":
        return _handle_post(request, issue)

    event = _get_event(issue, event_pk, ingest_order)
    parsed_data = json.loads(event.data)

    key_info = [
        ("title", event.title()),
        ("event_id", event.event_id),
        ("bugsink_internal_id", event.id),
        ("issue_id", issue.id),
        ("timestamp", _date_with_milis_html(event.timestamp)),
        ("server_side_timestamp", _date_with_milis_html(event.server_side_timestamp)),
    ]
    if parsed_data.get("logger"):
        key_info.append(("logger", parsed_data["logger"]))

    deployment_info = \
        ([("release", parsed_data["release"])] if "release" in parsed_data else []) + \
        ([("environment", parsed_data["environment"])] if "environment" in parsed_data else []) + \
        ([("server_name", parsed_data["server_name"])] if "server_name" in parsed_data else [])

    return render(request, "issues/event_details.html", {
        "tab": "event-details",
        "this_view": "event_details",
        "project": issue.project,
        "issue": issue,
        "event": event,
        "is_event_page": True,
        "parsed_data": parsed_data,
        "key_info": key_info,
        "deployment_info": deployment_info,
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })


@issue_membership_required
def issue_history(request, issue):
    if request.method == "POST":
        return _handle_post(request, issue)

    last_event = issue.event_set.order_by("timestamp").last()  # the template needs this for the tabs, we pick the last
    return render(request, "issues/history.html", {
        "tab": "history",
        "project": issue.project,
        "issue": issue,
        "event": last_event,
        "is_event_page": False,
        "parsed_data": json.loads(last_event.data),
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })


@issue_membership_required
def issue_grouping(request, issue):
    if request.method == "POST":
        return _handle_post(request, issue)

    last_event = issue.event_set.order_by("timestamp").last()  # the template needs this for the tabs, we pick the last
    return render(request, "issues/grouping.html", {
        "tab": "grouping",
        "project": issue.project,
        "issue": issue,
        "event": last_event,
        "is_event_page": False,
        "parsed_data": json.loads(last_event.data),
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })


@issue_membership_required
def issue_event_list(request, issue):
    if request.method == "POST":
        return _handle_post(request, issue)

    event_list = issue.event_set.all()

    last_event = issue.event_set.order_by("timestamp").last()  # the template needs this for the tabs, we pick the last
    return render(request, "issues/event_list.html", {
        "tab": "event-list",
        "project": issue.project,
        "issue": issue,
        "event": last_event,
        "event_list": event_list,
        "is_event_page": False,
        "parsed_data": json.loads(last_event.data),
        "mute_options": GLOBAL_MUTE_OPTIONS,
    })

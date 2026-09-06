"""Derive tool overlap groups from normalized intervals, without changing source events."""

from collections import defaultdict

from .domain import stable_id
from .timestamps import timestamp_ns

METHOD = "tool-overlap-v1"
NOTE = (
    "Parallel groups are inferred from overlapping recorded tool intervals within the same source, "
    "timing quality, turn, trace, and parent span. A group can be connected by successive overlaps; "
    "peak concurrency shows how many intervals overlap at once. This does not prove simultaneous "
    "process execution or membership in the same model-request batch. Open and zero-length intervals "
    "are excluded. Missing parent metadata cannot distinguish nested operations."
)
SCOPE = ("session_id", "source", "timing", "turn_id", "trace_id", "parent_span_id")


def summarize(members, scope):
    edges = defaultdict(int)
    for event in members:
        edges[timestamp_ns(event["start_time"])] += 1
        edges[timestamp_ns(event["end_time"])] -= 1
    active = peak = overlap = 0
    previous = None
    for instant, delta in sorted(edges.items()):
        if previous is not None and active >= 2:
            overlap += instant - previous
        active += delta
        peak = max(peak, active)
        previous = instant
    member_ids = [event["id"] for event in members]
    return {
        "id": stable_id(METHOD, scope, sorted(member_ids)),
        **dict(zip(SCOPE, scope, strict=True)),
        "origin": "inferred",
        "method": METHOD,
        "start_time": min(event["start_time"] for event in members),
        "end_time": max(event["end_time"] for event in members),
        "event_ids": member_ids,
        "tool_count": len(members),
        "max_concurrency": peak,
        "overlap_ms": overlap / 1e6,
    }


def parallel_groups(events):
    scopes = defaultdict(list)
    for event in events:
        if (
            event["kind"] == "tool"
            and event["end_time"] is not None
            and event["end_time"] > event["start_time"]
        ):
            scopes[tuple(event.get(key) for key in SCOPE)].append(event)
    groups = []
    for scope, candidates in scopes.items():
        members, end = [], None
        for event in sorted(candidates, key=lambda e: (e["start_time"], e["end_time"], e["id"])):
            # Half-open intervals: a call starting exactly at another's end does not overlap.
            if end is not None and event["start_time"] >= end:
                if len(members) > 1:
                    groups.append(summarize(members, scope))
                members, end = [], None
            members.append(event)
            end = max(end or event["end_time"], event["end_time"])
        if len(members) > 1:
            groups.append(summarize(members, scope))
    # Labels are chronological within a source and independent of page/search filters.
    numbers = defaultdict(int)
    for group in sorted(groups, key=lambda g: (g["source"], g["start_time"], g["id"])):
        numbers[group["source"]] += 1
        group["label"] = f"P{numbers[group['source']]}"
    return sorted(groups, key=lambda g: (g["start_time"], g["source"], g["id"]))

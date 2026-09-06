"""Synthetic interval cases; overlap is not a model-batch or process-execution claim."""

import pytest

from agentboard.domain import Event, Session
from agentboard.parallel import parallel_groups
from agentboard.timestamps import format_timestamp

BASE = 1788604920000000000


def tool(eid, start, end, **kwargs):
    fields = {
        "id": eid,
        "session_id": "parallel-test",
        "sequence": 1,
        "kind": "tool",
        "name": "exec_command",
        "source": "codex_jsonl",
        "timing": "estimated",
        "turn_id": "turn-1",
        "start_time": format_timestamp(BASE + start * 1000000000),
        "end_time": format_timestamp(BASE + end * 1000000000) if end is not None else None,
    }
    fields.update(kwargs)
    return Event(**fields).model_dump()


def test_parallel_chain_is_not_all_simultaneous():
    a, b, c = tool("a", 0, 3), tool("b", 2, 5), tool("c", 4, 7)
    group = parallel_groups([c, a, b])[0]
    assert group["label"] == "P1"
    assert group["event_ids"] == ["a", "b", "c"]
    assert group["tool_count"] == 3 and group["max_concurrency"] == 2
    assert group["overlap_ms"] == 2000
    assert group["start_time"] == a["start_time"] and group["end_time"] == c["end_time"]
    assert group["origin"] == "inferred" and group["method"] == "tool-overlap-v1"
    assert parallel_groups([a, b, c]) == [group]


def test_peak_and_overlap_time_do_not_double_count_triple_overlap():
    group = parallel_groups([tool("a", 0, 10), tool("b", 1, 7), tool("c", 2, 6)])[0]
    assert group["max_concurrency"] == 3
    assert group["overlap_ms"] == 6000  # [1,7), counted once even while three calls overlap.


def test_touching_serial_open_zero_and_non_tools_do_not_form_groups():
    events = [tool("a", 0, 2), tool("b", 2, 4), tool("zero", 1, 1), tool("open", 0, None)]
    events += [tool("wait", 0, 5, kind="user_wait"), tool("llm", 0, 5, kind="llm")]
    assert parallel_groups(events) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "codex_item"),
        ("timing", "measured"),
        ("turn_id", "turn-2"),
        ("trace_id", "other-trace"),
        ("parent_span_id", "parent-span"),
        ("session_id", "other-session"),
    ],
)
def test_different_scopes_do_not_create_false_groups(field, value):
    assert parallel_groups([tool("a", 0, 4), tool("b", 1, 3, **{field: value})]) == []


def test_known_parent_child_overlap_is_excluded_but_siblings_group():
    events = [
        tool("parent", 0, 9, trace_id="trace"),
        tool("child-a", 1, 6, trace_id="trace", parent_span_id="parent"),
        tool("child-b", 2, 5, trace_id="trace", parent_span_id="parent"),
    ]
    groups = parallel_groups(events)
    assert len(groups) == 1 and groups[0]["event_ids"] == ["child-a", "child-b"]


def test_nanosecond_overlap_and_equal_start_times():
    a, b = tool("a", 0, 1), tool("b", 0, 1)
    a["end_time"] = format_timestamp(BASE + 2)
    b["start_time"] = format_timestamp(BASE + 1)
    b["end_time"] = format_timestamp(BASE + 3)
    assert parallel_groups([a, b])[0]["overlap_ms"] == 0.000001
    assert parallel_groups([tool("a", 0, 1), tool("b", 0, 1)])[0]["max_concurrency"] == 2


def test_api_groups_use_complete_source_and_preserve_events(client, imported):
    path = f"/api/v1/sessions/{imported}"
    before = client.get(path + "/export").content
    all_groups = client.get(path + "/parallel-groups").json()
    assert len(all_groups["items"]) == 1
    group = all_groups["items"][0]
    assert group["tool_count"] == group["max_concurrency"] == 2
    assert group["overlap_ms"] == 1100
    assert all_groups["scope"] == "full_session_source"
    # A one-event page must not shrink or renumber groups.
    page = client.get(path + "/events?kind=tool&limit=1").json()
    assert page["items"][0]["id"] in group["event_ids"] and page["next_cursor"]
    filtered = client.get(path + "/parallel-groups?source=codex_jsonl&q=missing&after=9999").json()
    assert filtered == all_groups
    assert client.get(path + "/parallel-groups?source=codex_item").json()["items"] == []
    assert client.get(path + "/export").content == before
    assert client.get("/api/v1/sessions/missing/parallel-groups").status_code == 404


def test_labels_are_source_local_and_recompute_after_completion(client):
    store = client.app.state.store
    events = [tool("a", 0, 4), tool("b", 1, 3), tool("c", 10, 14), tool("d", 11, None)]
    events += [tool("item-a", 0, 4, source="codex_item"), tool("item-b", 1, 3, source="codex_item")]
    store.ingest(
        [Session(id="parallel-test", started_at=format_timestamp(BASE))] + [Event(**e) for e in events]
    )
    first = store.parallel_groups("parallel-test", "codex_jsonl")["items"]
    assert len(first) == 1 and first[0]["label"] == "P1"
    store.ingest([Event(**tool("d", 11, 13))])
    after = store.parallel_groups("parallel-test", "codex_jsonl")["items"]
    assert [g["label"] for g in after] == ["P1", "P2"]
    assert after[0] == first[0]
    assert after == [
        g for g in store.parallel_groups("parallel-test")["items"] if g["source"] == "codex_jsonl"
    ]

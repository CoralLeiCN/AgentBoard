import io
import json
import shlex
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import FIXTURE

from agentboard.adapters.codex import CodexAdapter
from agentboard.domain import Event
from agentboard.timestamps import timestamp_ns


def parse(records):
    return list(CodexAdapter().parse(io.StringIO("\n".join(json.dumps(r) for r in records))))


def records():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_historical_timing_and_parallel_union(client, imported):
    stats = client.get(f"/api/v1/sessions/{imported}/stats").json()
    assert stats["counts"]["user"] == 2  # response_item + event_msg are mirrors
    assert stats["counts"]["tool"] == 6
    tools = next(t for t in stats["timing"] if t["kind"] == "tool")
    assert tools["timing"] == "estimated"
    assert tools["sum_ms"] == pytest.approx(17500)
    assert tools["active_ms"] == pytest.approx(16400)  # overlapping tool calls aren't double-counted
    llm = next(t for t in stats["timing"] if t["kind"] == "llm")
    assert llm["active_ms"] < 60000  # excludes the 32-second user think time
    inputs = client.get(f"/api/v1/sessions/{imported}/inputs").json()["items"]
    assert inputs[1]["attributes"]["previous_turn_id"] == "turn-001"
    assert inputs[1]["turn_id"] == "turn-002"


def test_idempotent_and_changed_title_preserves_classification(client, imported):
    client.post(f"/api/v1/sessions/{imported}/classify")
    result = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes()).json()
    assert result["inserted_events"] == 0
    assert client.get(f"/api/v1/sessions/{imported}").json()["classification"]["category"] == "bug-fixing"


def test_growing_rollout_completes_tool(client):
    rows = records()[:7]  # through first call
    body = "\n".join(json.dumps(r) for r in rows)
    client.post("/api/v1/import/codex", content=body)
    sid = rows[0]["payload"]["id"]
    first = client.get(f"/api/v1/sessions/{sid}/events", params={"kind": "tool"}).json()["items"]
    assert len(first) == 1 and first[0]["end_time"] is None
    client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
    updated = client.app.state.store.event(sid, first[0]["id"])
    assert updated["end_time"] is not None and updated["status"] == "ok"
    assert client.get(f"/api/v1/sessions/{sid}/stats").json()["counts"]["tool"] == 6


@pytest.mark.parametrize("tail", ["{", '{"type":"response_item"}', "null", "[]", '{"payload":null}'])
def test_malformed_import_is_atomic(client, tail):
    result = client.post("/api/v1/import/codex", content=FIXTURE.read_text() + tail)
    assert result.status_code == 422
    assert client.get("/api/v1/sessions").json()["total"] == 0


def test_repeated_real_prompts_not_deduplicated():
    rows = records()
    first = rows[3]["payload"]["content"][0]["text"]
    rows[19]["payload"]["content"][0]["text"] = first
    rows[20]["payload"]["message"] = first
    inputs = [e for e in parse(rows) if isinstance(e, Event) and e.kind == "user"]
    assert len(inputs) == 2
    assert inputs[0].text == inputs[1].text


def test_event_only_user_fallback_and_custom_tool():
    rows = [r for r in records() if not (r["type"] == "response_item" and r["payload"].get("role") == "user")]
    parsed = [e for e in parse(rows) if isinstance(e, Event)]
    assert len([e for e in parsed if e.kind == "user"]) == 2
    assert len([e for e in parsed if e.name == "apply_patch"]) == 2


def test_fragmented_user_content_matches_event_mirror():
    rows = records()
    rows[3]["payload"]["content"] = [
        {"type": "input_text", "text": "Fix the "},
        {"type": "input_text", "text": "checkout total rounding bug"},
    ]
    inputs = [e for e in parse(rows) if isinstance(e, Event) and e.kind == "user"]
    assert len(inputs) == 2
    assert inputs[0].text == "Fix the checkout total rounding bug"


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        ["/bin/zsh", "-lc", "printf '%s\\n' 'two words' \"\" '$HOME'"],
        [],
        None,
        {"command": "pytest"},
    ],
)
def test_measured_item_spans_are_separate_from_estimates(client, command):
    rows = records()
    rows.append(
        {
            "timestamp": rows[-1]["timestamp"],
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn-002",
                "started_at_ms": 1788604920000,
                "completed_at_ms": 1788604920000,
                "item": {
                    "type": "CommandExecution",
                    "id": "command-1",
                    "command": command,
                    "exit_code": 1,
                    "duration": {"secs": 2, "nanos": 100000000},
                },
            },
        }
    )
    measured = [e for e in parse(rows) if isinstance(e, Event) and e.source == "codex_item"]
    assert timestamp_ns(measured[0].end_time) - timestamp_ns(measured[0].start_time) == 2100000000
    assert measured[0].timing == "measured" and measured[0].status == "error"
    assert measured[0].attributes["item"]["command"] == command
    if isinstance(command, list):
        assert shlex.split(measured[0].text) == command
    elif command is None:
        assert measured[0].text == ""
    elif isinstance(command, str):
        assert measured[0].text == command
    else:
        assert json.loads(measured[0].text) == command
    response = client.post("/api/v1/import/codex", content="\n".join(json.dumps(row) for row in rows))
    assert response.status_code == 200, response.text
    sid = rows[0]["payload"]["id"]
    saved = client.get(f"/api/v1/sessions/{sid}/events?source=codex_item").json()["items"]
    assert len(saved) == 1 and saved[0]["text"] == measured[0].text
    assert saved[0]["attributes"]["item"]["command"] == command


def test_concurrent_idempotent_ingestion(client, imported):
    store = client.app.state.store

    def ingest(_):
        return store.ingest(CodexAdapter().parse(io.StringIO(FIXTURE.read_text())))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ingest, range(8)))
    assert sum(r["inserted_events"] for r in results) == 0
    assert store.stats(imported)["counts"]["user"] == 2


def wait_record(second, kind, **payload):
    return {
        "timestamp": f"2026-09-05T10:42:{second:02d}Z",
        "type": "response_item"
        if kind in ("function_call", "function_call_output", "message")
        else "event_msg",
        "payload": {"type": kind, **payload},
    }


def wait_events(rows):
    return [e for e in parse(rows) if isinstance(e, Event) and e.kind == "user_wait"]


def test_user_wait_between_turns_is_bounded_and_estimated(client, imported):
    path = f"/api/v1/sessions/{imported}"
    waits = client.get(path + "/events?kind=user_wait&source=codex_jsonl").json()["items"]
    assert len(waits) == 1  # No initial wait or unbounded wait after the final turn.
    assert waits[0]["attributes"]["wait_type"] == "between_turns"
    assert waits[0]["timing"] == "estimated"
    stats = client.get(path + "/stats?source=codex_jsonl").json()
    timing = next(t for t in stats["timing"] if t["kind"] == "user_wait")
    assert timing["active_ms"] == 31900
    assert timing["sum_ms"] == 31900
    assert not client.get(path + "/stats?source=otlp_log").json()["timing"]
    exported = client.get(path + "/export?kind=user_wait").text.splitlines()
    assert len(exported) == 1 and json.loads(exported[0])["id"] == waits[0]["id"]


@pytest.mark.parametrize("mirror_first", [False, True])
def test_user_wait_prompt_mirrors_and_final_answer_fallback(mirror_first):
    rows = [r for r in records() if r["payload"].get("type") != "task_complete"]
    if mirror_first:
        rows[18], rows[19] = rows[19], rows[18]
    waits = wait_events(rows)
    assert len(waits) == 1
    assert waits[0].end_time > waits[0].start_time
    assert timestamp_ns(waits[0].end_time) - timestamp_ns(waits[0].start_time) == (
        32010000000 if mirror_first else 32000000000
    )


@pytest.mark.parametrize("boundary", ["turn_aborted", "thread_rolled_back"])
def test_interruption_is_not_inferred_as_user_wait(boundary):
    rows = [
        records()[0],
        wait_record(1, "task_started"),
        wait_record(4, boundary),
        wait_record(9, "user_message", message="Continue"),
    ]
    assert wait_events(rows) == []


def test_work_without_another_prompt_clears_inferred_wait():
    rows = [
        records()[0],
        wait_record(1, "task_complete"),
        wait_record(2, "task_started"),
        wait_record(3, "function_call", call_id="a", name="read_file", arguments="{}"),
        wait_record(9, "user_message", message="New direction"),
    ]
    assert wait_events(rows) == []


def test_blocking_input_requests_union_and_async_exclusion(client):
    rows = [
        records()[0],
        wait_record(1, "task_started"),
        wait_record(2, "function_call", call_id="a", name="functions.request_user_input", arguments="{}"),
        wait_record(3, "function_call", call_id="b", name="request_user_input", arguments="{}"),
        wait_record(5, "function_call_output", call_id="a", output="Answer A"),
        wait_record(7, "function_call_output", call_id="b", output="Answer B"),
        wait_record(
            8, "function_call", call_id="c", name="functions.request_user_input_async", arguments="{}"
        ),
        wait_record(9, "function_call_output", call_id="c", output="Question posted"),
    ]
    store = client.app.state.store
    store.ingest(parse(rows))
    stats = store.stats(rows[0]["payload"]["id"])
    wait = next(t for t in stats["timing"] if t["kind"] == "user_wait")
    assert wait["count"] == 2 and wait["sum_ms"] == 7000 and wait["active_ms"] == 5000
    tool = next(t for t in stats["timing"] if t["kind"] == "tool")
    assert tool["active_ms"] == 1000 and tool["count"] == 1
    assert all(e.attributes["wait_type"] == "input_request" for e in wait_events(rows))


def test_growing_input_request_and_legacy_reimport(client):
    rows = [
        records()[0],
        wait_record(1, "task_started"),
        wait_record(2, "function_call", call_id="a", name="functions.request_user_input", arguments="{}"),
    ]
    store = client.app.state.store
    sid = rows[0]["payload"]["id"]
    store.ingest(parse(rows))
    initial = next(t for t in store.stats(sid)["timing"] if t["kind"] == "user_wait")
    assert initial["active_ms"] is None and initial["sum_ms"] is None
    rows.append(wait_record(9, "function_call_output", call_id="a", output="Chosen answer"))
    store.ingest(parse(rows))
    wait = next(e for e in store.export(sid) if e["kind"] == "user_wait")
    assert wait["status"] == "ok" and wait["attributes"]["output"] == "Chosen answer"
    assert next(t for t in store.stats(sid)["timing"] if t["kind"] == "user_wait")["active_ms"] == 7000
    # Simulate a completed input request imported before user_wait was introduced.
    with store.connect() as db:
        db.execute("UPDATE events SET kind='tool' WHERE id=?", (wait["id"],))
    assert store.ingest(parse(rows[:-1]))["inserted_events"] == 0
    assert store.event(sid, wait["id"])["attributes"]["output"] == "Chosen answer"
    assert store.ingest(parse(rows))["inserted_events"] == 0
    assert store.event(sid, wait["id"])["kind"] == "user_wait"
    assert store.stats(sid)["counts"].get("tool", 0) == 0


def test_measured_input_request_item():
    rows = [
        records()[0],
        wait_record(
            9,
            "item_completed",
            started_at_ms=1788604922000,
            completed_at_ms=1788604929000,
            item={"type": "DynamicToolCall", "id": "question-1", "tool": "functions.request_user_input"},
        ),
    ]
    wait = wait_events(rows)[0]
    assert wait.source == "codex_item" and wait.timing == "measured"
    assert timestamp_ns(wait.end_time) - timestamp_ns(wait.start_time) == 7000000000

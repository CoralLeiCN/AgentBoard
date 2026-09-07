"""Synthetic attribution cases: role is evidence, not proof of human authorship."""

import copy
import json
import subprocess
import sys

import pytest
from test_codex import parse, records, wait_record

from agentboard.domain import Event, stable_id
from agentboard.models import branch_context
from agentboard.timestamps import timestamp_ns

CONTEXT = "<environment_context>\n<cwd>/synthetic</cwd>\n</environment_context>"
AGENTS = "# AGENTS.md instructions for /synthetic\n\n<INSTRUCTIONS>Keep tests local.</INSTRUCTIONS>"


def prompt(second, text, mirror=False, **payload):
    return wait_record(second, "user_message", message=text, **payload) if mirror else wait_record(
        second, "message", role="user", content=[{"type": "input_text", "text": text}], **payload
    )


def events(rows):
    return [item for item in parse(rows) if isinstance(item, Event)]


def upload(client, rows):
    body = "".join(json.dumps(row) + "\n" for row in rows)
    response = client.post("/api/v1/import/codex", content=body)
    assert response.status_code == 200, response.text
    return response.json(), body


@pytest.mark.parametrize("text", [CONTEXT, AGENTS, AGENTS.replace("\n", "\r\n"), AGENTS + "\n" + CONTEXT,
                                       "<recommended_plugins>synthetic</recommended_plugins>\n" + CONTEXT,
                                       "<recommended_plugins>synthetic</recommended_plugins>\n" + AGENTS + CONTEXT])
@pytest.mark.parametrize("mirror_first", [False, True])
def test_context_is_inspectable_but_not_a_prompt(client, text, mirror_first):
    rows = [records()[0], prompt(1, text, mirror_first), prompt(2, text, not mirror_first),
            prompt(3, "Fix the test"), wait_record(4, "task_complete")]
    _, body = upload(client, rows)
    sid = rows[0]["payload"]["id"]
    base = f"/api/v1/sessions/{sid}"
    assert client.get(base).json()["title"] == "Fix the test"
    assert client.get(base + "/stats").json()["counts"]["user"] == 1
    assert len(client.get(base + "/inputs").json()["items"]) == 1
    all_events = client.get(base + "/events").json()["items"]
    context = [event for event in all_events if event["name"] == "Injected context"]
    assert len(context) == 1
    assert context[0]["kind"] == "event" and context[0]["text"] == text
    assert context[0]["attributes"]["transport_role"] == "user"
    fields = client.get(base + f'/events/{context[0]["id"]}/lineage').json()["fields"]
    assert fields["/text"]["origin"] == "normalized"
    assert fields["/text"]["sources"][0]["pointer"] == "/payload/content"
    assert fields["/attributes/input_attribution/origin"]["origin"] == "inferred"
    assert fields["/attributes/transport_role"]["origin"] == "normalized"
    evidence = client.get(base + f'/events/{context[0]["id"]}/raw').json()
    assert evidence["available"]
    assert json.loads(evidence["lines"][0]["text"])["payload"]["role"] == "user"
    assert client.get(base + "/raw").text == body
    assert [e["text"] for e in map(json.loads, client.get(base + "/export?kind=user").text.splitlines())] == [
        "Fix the test"
    ]
    for endpoint in ("codex-plan", "replay"):
        response = client.post(base + "/" + endpoint, json={"input_id": context[0]["id"], "replacement": "X"})
        assert response.status_code == 422
    user = next(e for e in all_events if e["kind"] == "user")
    _, _, history = branch_context(client.app.state.store, sid, user["id"], "Replacement", 10000)
    assert history[0] == {"role": "user", "content": text}


@pytest.mark.parametrize("text", [
    "Explain " + CONTEXT,
    CONTEXT + "\nPlease explain the context above.",
    "```xml\n" + CONTEXT + "\n```",
    "> " + CONTEXT,
    '"' + CONTEXT + '"',
    "<INSTRUCTIONS>Write a poem.</INSTRUCTIONS>",
    "<environment_context>Unclosed",
    "<environment_contextual>Example</environment_contextual>",
    AGENTS + "\nNow implement the feature.",
    "<subagent_notification>Done</subagent_notification>\nExplain these results: "
    "<subagent_notification>Passed</subagent_notification>",
    "Review this code for me",
])
def test_real_prompts_containing_markup_are_retained(text):
    parsed = events([records()[0], prompt(1, text)])
    assert len(parsed) == 1 and parsed[0].kind == "user"
    assert parsed[0].attributes["input_attribution"]["origin"] == "human"
    assert "inferred" in parsed[0].attributes["input_attribution"]["basis"]


def test_multimodal_prompt_and_fragmented_context():
    image_prompt = prompt(1, CONTEXT)
    image_prompt["payload"]["content"].append({"type": "input_image", "image_url": "synthetic"})
    assert events([records()[0], image_prompt])[0].kind == "user"
    fragmented = prompt(1, CONTEXT)
    fragmented["payload"]["content"] = [
        {"type": "input_text", "text": CONTEXT[:25]}, {"type": "input_text", "text": CONTEXT[25:]}
    ]
    assert events([records()[0], fragmented])[0].name == "Injected context"


@pytest.mark.parametrize("mirror_first", [False, True])
def test_context_between_prompt_mirrors_does_not_double_count(mirror_first):
    rows = [records()[0], prompt(1, "Build it", mirror_first), prompt(2, CONTEXT),
            prompt(3, "Build it", not mirror_first), wait_record(4, "task_complete"),
            prompt(5, "Build it", mirror_first), prompt(6, CONTEXT), prompt(7, "Build it", not mirror_first)]
    parsed = events(rows)
    assert len([e for e in parsed if e.kind == "user"]) == 2
    assert len([e for e in parsed if e.name == "Injected context"]) == 2
    waits = [e for e in parsed if e.kind == "user_wait"]
    assert len(waits) == 1
    assert timestamp_ns(waits[0].end_time) - timestamp_ns(waits[0].start_time) == 1000000000


def test_context_does_not_close_wait_or_create_llm_activity():
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"), prompt(3, CONTEXT),
            prompt(4, CONTEXT, True), prompt(9, "Next"), prompt(10, "Next", True)]
    parsed = events(rows)
    waits = [e for e in parsed if e.kind == "user_wait"]
    assert len(waits) == 1
    assert timestamp_ns(waits[0].end_time) - timestamp_ns(waits[0].start_time) == 7000000000
    assert not [e for e in events(rows[:5]) if e.kind in ("user_wait", "llm")]
    only_context = [records()[0], prompt(1, CONTEXT), wait_record(2, "message", role="assistant", content="Hi")]
    assert not [e for e in events(only_context) if e.kind == "llm"]


@pytest.mark.parametrize("metadata", [
    {"source": {"subagent": {"other": "guardian"}}},
    {"thread_source": "guardian_review"},
    {"source": "subagent"},
    {"source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}}},
])
def test_internal_sessions_exclude_prompt_and_wait_metrics(client, metadata):
    meta = copy.deepcopy(records()[0])
    meta["payload"].update(metadata)
    rows = [meta, prompt(1, CONTEXT), prompt(2, "Review this patch"), wait_record(3, "task_complete"),
            prompt(9, "Review again", True),
            wait_record(10, "function_call", call_id="q", name="request_user_input", arguments="{}"),
            wait_record(12, "function_call_output", call_id="q", output="Synthetic response"),
            wait_record(13, "item_completed", started_at_ms=1788604930000, completed_at_ms=1788604932000,
                        item={"type": "DynamicToolCall", "id": "q", "tool": "request_user_input"})]
    upload(client, rows)
    sid = meta["payload"]["id"]
    store = client.app.state.store
    counts = store.stats(sid)["counts"]
    assert counts.get("user", 0) == counts.get("user_wait", 0) == counts.get("tool", 0) == 0
    assert store.get_session(sid)["title"] == "Untitled session"
    origin = store.get_session(sid)["input_origin"]
    assert origin["origin"] == "internal" and origin["evidence"]
    fields = store.field_lineage(sid)["fields"]
    assert fields["/input_origin/origin"]["available"]
    assert fields["/input_origin/origin"]["origin"] == "inferred"
    assert any(source["value"] == next(iter(metadata.values()))
               for source in fields["/input_origin/origin"]["sources"])
    if "thread_spawn" in str(metadata):
        assert origin["parent_session_id"] == "parent"
    assert len([e for e in store.export(sid) if e["name"] == "Internal input"]) == 2
    requests = [e for e in store.export(sid) if e["name"] == "request_user_input"]
    assert len(requests) == 2 and all(e["attributes"]["input_scope"] == "internal" for e in requests)
    for request in requests:
        scope = store.field_lineage(sid, request["id"])["fields"]["/attributes/input_scope"]
        assert scope["available"] and scope["origin"] == "inferred"
        assert any(source["line_number"] == 1 for source in scope["sources"])


def test_internal_notification_cancels_wait_until_human_work_resumes():
    notification = '<subagent_notification agent_id="synthetic">Done</subagent_notification>'
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"),
            prompt(3, notification), wait_record(4, "message", role="assistant", content="Done", phase="final_answer"),
            wait_record(5, "task_complete"), prompt(9, "Next"), wait_record(10, "task_complete"), prompt(12, "Again")]
    waits = [e for e in events(rows) if e.kind == "user_wait"]
    assert len(waits) == 1
    assert timestamp_ns(waits[0].end_time) - timestamp_ns(waits[0].start_time) == 2000000000


def test_context_only_completion_does_not_seed_a_human_wait():
    rows = [records()[0], prompt(1, CONTEXT),
            wait_record(2, "message", role="assistant", content="Ready", phase="final_answer"),
            wait_record(3, "task_complete"), prompt(9, "First human prompt")]
    assert not [e for e in events(rows) if e.kind == "user_wait"]


def test_context_does_not_move_lineage_boundaries(client):
    rows = [records()[0], prompt(1, "First"), prompt(2, CONTEXT),
            wait_record(3, "message", role="assistant", content="Done", phase="final_answer"),
            wait_record(4, "task_complete"), prompt(5, CONTEXT), prompt(9, "Next")]
    upload(client, rows)
    store, sid = client.app.state.store, rows[0]["payload"]["id"]
    for kind, start, end in (("llm", 2, 4), ("user_wait", 5, 7)):
        event = next(e for e in store.export(sid) if e["kind"] == kind)
        fields = store.field_lineage(sid, event["id"])["fields"]
        assert fields["/start_time"]["sources"][0]["line_number"] == start
        assert fields["/end_time"]["sources"][0]["line_number"] == end


def test_reimport_removes_fallback_once_its_response_mirror_arrives(client):
    rows = [records()[0], prompt(1, "Build it", True), prompt(2, CONTEXT), prompt(3, "Build it")]
    upload(client, rows[:2])
    sid = rows[0]["payload"]["id"]
    assert client.app.state.store.stats(sid)["counts"]["user"] == 1
    upload(client, rows)
    assert client.app.state.store.stats(sid)["counts"]["user"] == 1
    inputs = list(client.app.state.store.export(sid, "user"))
    assert inputs[0]["sequence"] == 4
    assert client.app.state.store.event_raw(sid, inputs[0]["id"])["available"]


def test_reimport_corrects_legacy_inputs_titles_and_waits(client):
    rows = [records()[0], prompt(1, CONTEXT), prompt(2, "First"), wait_record(3, "task_complete"),
            prompt(4, CONTEXT), prompt(9, "Next"), wait_record(10, "task_complete")]
    uploaded, body = upload(client, rows)
    store, sid = client.app.state.store, rows[0]["payload"]["id"]
    before = {e["id"]: e["row_id"] for e in store.export(sid)}
    # Simulate v3 interpretation and its wait that incorrectly ended at context.
    with store.connect() as db:
        db.execute("UPDATE raw_imports SET mapping_version='codex-jsonl-v3'")
        db.execute("UPDATE sessions SET title=? WHERE id=?", (CONTEXT.splitlines()[0], sid))
        db.execute("""UPDATE events SET kind='user',name='User input',
                   attributes=json_object('previous_turn_id',json_extract(attributes,'$.previous_turn_id'))
                   WHERE json_extract(attributes,'$.input_attribution') IS NOT NULL""")
        db.execute("DELETE FROM events WHERE kind='user_wait'")
    store.ingest([Event(id=stable_id(sid, 5, "user-wait"), session_id=sid, sequence=5, kind="user_wait",
                        name="Between turns (inferred user wait)", start_time=rows[3]["timestamp"],
                        end_time=rows[4]["timestamp"], timing="estimated", attributes={"wait_type": "between_turns"})])
    result, _ = upload(client, rows)
    assert result["raw_import_ids"] != uploaded["raw_import_ids"]
    assert len(store.raw_imports(sid)) == 2
    assert store.get_session(sid)["title"] == "First"
    assert store.stats(sid)["counts"]["user"] == 2
    waits = list(store.export(sid, "user_wait"))
    assert len(waits) == 1
    assert timestamp_ns(waits[0]["end_time"]) - timestamp_ns(waits[0]["start_time"]) == 6000000000
    for event in store.export(sid):
        if event["kind"] != "user_wait":
            assert event["row_id"] == before[event["id"]]
        if event["attributes"].get("input_attribution"):
            assert store.event_raw(sid, event["id"])["available"]
    assert client.get(f"/api/v1/sessions/{sid}/raw").text == body
    assert upload(client, rows)[0]["inserted_events"] == 0
    # A shortened import must preserve the later corrected wait and inputs.
    upload(client, rows[:4])
    assert list(store.export(sid, "user_wait")) == waits
    assert store.stats(sid)["counts"]["user"] == 2
    cli = subprocess.run([sys.executable, "-c", "from agentboard.cli import main; main()", "--database", store.path,
                          "export", sid, "--inputs-only"], capture_output=True, text=True, check=True)
    assert [json.loads(line)["text"] for line in cli.stdout.splitlines()] == ["First", "Next"]


def test_conflicting_reimport_preserves_retained_input_and_gap(client):
    rows = [records()[0], prompt(1, "First"), wait_record(2, "task_complete"), prompt(9, "Next")]
    upload(client, rows)
    store, sid = client.app.state.store, rows[0]["payload"]["id"]
    original = list(store.export(sid))
    rows[-1] = prompt(9, CONTEXT)
    upload(client, rows)
    assert list(store.export(sid)) == original


def test_replay_preserves_context_attribution_across_repeated_branches(client):
    rows = [records()[0], prompt(1, CONTEXT), prompt(2, "Build it")]
    upload(client, rows)
    sid = rows[0]["payload"]["id"]
    for _ in range(2):
        base = f"/api/v1/sessions/{sid}"
        user = client.get(base + "/inputs").json()["items"][0]
        replay = client.post(base + "/replay", json={"input_id": user["id"], "replacement": "Another approach"})
        assert replay.status_code == 200, replay.text
        sid = replay.json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        assert client.get(base + "/stats").json()["counts"]["user"] == 1
        context = next(e for e in client.get(base + "/events").json()["items"] if e["name"] == "Injected context")
        assert context["kind"] == "event" and context["attributes"]["input_attribution"]["origin"] == "context"
        assert context["text"] == CONTEXT and context["attributes"]["retained_context"]
        assert client.post(base + "/replay", json={"input_id": context["id"], "replacement": "X"}).status_code == 422


def test_replay_retains_internal_input_request_answers_across_branches(client):
    notification = '<subagent_notification>Review completed</subagent_notification>'
    rows = [records()[0], prompt(1, "Build it"), prompt(2, notification),
            wait_record(3, "function_call", call_id="q", name="request_user_input", arguments="Which target?"),
            wait_record(4, "function_call_output", call_id="q", output="Use staging"),
            prompt(5, "Continue with that target")]
    upload(client, rows)
    store, sid = client.app.state.store, rows[0]["payload"]["id"]
    request = next(e for e in store.export(sid) if e["name"] == "request_user_input")
    assert request["kind"] == "event" and request["attributes"]["input_scope"] == "internal"
    recorded_call = {
        "role": "assistant",
        "content": "[Recorded tool call: request_user_input]\nWhich target?\nUse staging",
    }
    for _ in range(2):
        selected = list(store.export(sid, "user"))[-1]
        _, _, history = branch_context(store, sid, selected["id"], "Proceed", 10000)
        assert history == [
            {"role": "user", "content": "Build it"},
            {"role": "user", "content": notification},
            recorded_call,
            {"role": "user", "content": "Proceed"},
        ]
        replay = client.post(f"/api/v1/sessions/{sid}/replay", json={
            "input_id": selected["id"], "replacement": "Proceed",
        })
        assert replay.status_code == 200, replay.text
        sid = replay.json()["session_id"]
        retained_call = next(e for e in store.export(sid) if e["text"] == recorded_call["content"])
        assert retained_call["kind"] == "assistant" and retained_call["attributes"]["retained_context"]
        assert store.stats(sid)["counts"].get("user_wait", 0) == 0


def test_legacy_internal_request_correction_preserves_completed_result_on_short_retry(client):
    meta = copy.deepcopy(records()[0])
    meta["payload"]["source"] = {"subagent": "review"}
    rows = [meta, prompt(1, "Review"),
            wait_record(2, "function_call", call_id="q", name="request_user_input", arguments="{}"),
            wait_record(3, "function_call_output", call_id="q", output="Retained answer")]
    upload(client, rows)
    store, sid = client.app.state.store, meta["payload"]["id"]
    request = next(e for e in store.export(sid) if e["name"] == "request_user_input")
    with store.connect() as db:
        db.execute("UPDATE events SET kind='user_wait',attributes=json_remove(attributes,'$.input_scope') WHERE id=?",
                   (request["id"],))
    upload(client, rows[:-1])
    corrected = store.event(sid, request["id"])
    assert corrected["kind"] == "event" and corrected["end_time"] == request["end_time"]
    assert corrected["attributes"]["output"] == "Retained answer"
    assert store.stats(sid)["counts"].get("user_wait", 0) == 0
    upload(client, rows)
    assert store.event_raw(sid, request["id"])["available"]

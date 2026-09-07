"""Field evidence must explain stored values without redirecting them on reimport."""

import json

import pytest
from conftest import FIXTURE

from agentboard.domain import Event, Session
from agentboard.lineage import leaves, resolve
from agentboard.store import Store


def record(kind, second, **payload):
    return {"timestamp": f"2026-09-07T00:00:{second:02}Z", "type": kind, "payload": payload}


def upload(client, rows):
    text = "\r\n".join(json.dumps(r, ensure_ascii=False) if r else "" for r in rows) + "\r\n"
    response = client.post("/api/v1/import/codex", content=text.encode())
    assert response.status_code == 200, response.text
    return response.json()["raw_import_ids"][0]


def events(client):
    return client.get("/api/v1/sessions/s/events").json()["items"]


def lineage(client, event=None, sid="s"):
    path = f"/api/v1/sessions/{sid}" + (f'/events/{event["id"]}' if event else "") + "/lineage"
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def field(client, event, path):
    return lineage(client, event)["fields"][path]


def refs(description):
    return [(s["line_number"], s["pointer"]) for s in description["sources"]]


def test_every_codex_leaf_resolves_to_archive_or_explicit_application_rule(client, imported):
    store = client.app.state.store
    for event in store.export(imported):
        result = lineage(client, event, imported)
        assert leaves(event).keys() <= result["fields"].keys()
        records = {(r["import_id"], r["line_number"]): json.loads(r["text"]) for r in result["records"]}
        for path, description in result["fields"].items():
            assert description["available"], (event, path, description)
            assert description["origin"] != "unknown"
            assert description["method"]
            if path in leaves(event):
                assert description["value"] == leaves(event)[path]
            for source in description["sources"]:
                present, value = resolve(records[(source["import_id"], source["line_number"])], source["pointer"])
                assert (source["present"], source["value"]) == (present, value)
        assert all(a["mapping_version"] == "codex-jsonl-v4" for a in result["archives"])
    session = lineage(client, sid=imported)
    assert all(d["available"] for p, d in session["fields"].items() if p != "/classification")
    assert session["fields"]["/classification"]["origin"] == "unknown"


def test_context_mirrors_delayed_prompts_and_both_inferred_boundaries(client):
    rows = [record("session_meta", 0, id="s"), None,
            record("turn_context", 1, turn_id="t1", model="m1"),
            record("event_msg", 2, type="user_message", message=" First prompt \nsecond line"),
            record("response_item", 3, type="message", role="user", content=[{"text": " First prompt \nsecond line"}]),
            record("event_msg", 4, type="user_message", message=" First prompt \nsecond line"),
            record("response_item", 5, type="message", role="assistant", phase="final_answer", content="Done"),
            record("event_msg", 6, type="task_complete", turn_id="t1"),
            record("turn_context", 7, turn_id="t2", model="m2"),
            record("event_msg", 8, type="user_message", message="Next")]
    upload(client, rows)
    all_events = events(client)
    gap = next(e for e in all_events if e["kind"] == "llm")
    assert refs(field(client, gap, "/start_time")) == [(6, "/timestamp")]
    assert refs(field(client, gap, "/end_time")) == [(7, "/timestamp")]
    assert refs(field(client, gap, "/turn_id")) == [(3, "/payload/turn_id")]
    wait = next(e for e in all_events if e["kind"] == "user_wait")
    assert refs(field(client, wait, "/start_time")) == [(8, "/timestamp")]
    assert refs(field(client, wait, "/end_time")) == [(10, "/timestamp")]
    # The raw-event view continues to exclude calculation-only boundaries.
    assert not client.get(f'/api/v1/sessions/s/events/{wait["id"]}/raw').json()["available"]
    user = next(e for e in all_events if e["text"] == "Next")
    assert refs(field(client, user, "/text")) == [(10, "/payload/message")]
    assert refs(field(client, user, "/attributes/previous_turn_id")) == [(8, "/payload/turn_id")]
    assert refs(field(client, user, "/turn_id")) == [(9, "/payload/turn_id")]
    assert refs(field(client, user, "/session_id")) == [(1, "/payload/id")]
    session = lineage(client)["fields"]
    assert refs(session["/metadata/model"]) == [(9, "/payload/model")]
    assert refs(session["/title"]) == [(4, "/payload/message")]
    assert session["/title"]["value"] == "First prompt "


@pytest.mark.parametrize("payload,pointer,origin", [
    ({"call_id": "c"}, "/payload/call_id", "normalized"),
    ({"call_id": "", "id": "fallback"}, "/payload/id", "normalized"),
    ({}, "", "calculated"),
])
def test_call_keys_and_name_defaults_are_mapping_aware(client, payload, pointer, origin):
    upload(client, [record("session_meta", 0, session_id="s"),
                    record("response_item", 1, type="function_call", **payload)])
    tool = events(client)[0]
    call = field(client, tool, "/attributes/call_id")
    assert (call["origin"], refs(call)) == (origin, [(2, pointer)])
    name = field(client, tool, "/name")
    assert name["origin"] == "inferred" and not name["sources"][0]["present"]
    assert field(client, tool, "/status")["value"] == "incomplete"
    assert field(client, tool, "/end_time")["value"] is None
    assert field(client, tool, "/end_time")["available"]


def test_tool_fields_bind_independently_across_conflicting_completion(client):
    rows = [record("session_meta", 0, id="s"),
            record("response_item", 3, type="function_call", call_id="c", name="tool", arguments="original")]
    first = upload(client, rows)
    rows[1]["payload"]["arguments"] = "changed"
    rows.append(record("response_item", 2, type="function_call_output", call_id="c",
                       output="Wall time: 1.25 seconds\nProcess exited with code 1"))
    second = upload(client, rows)
    tool = events(client)[0]
    result = lineage(client, tool)
    assert result["fields"]["/text"]["import_id"] == first
    assert result["fields"]["/text"]["value"] == "original"
    for path in ("/end_time", "/status", "/attributes/output", "/attributes/reported_wall_time_ms"):
        assert result["fields"][path]["import_id"] == second
    assert result["fields"]["/attributes/reported_wall_time_ms"]["value"] == 1250
    assert refs(result["fields"]["/end_time"]) == [(2, "/timestamp"), (3, "/timestamp")]
    assert result["fields"]["/attributes/end_time_clamped"]["value"] is True
    # Retry and shortened snapshots cannot retarget fields with valid prior evidence.
    assert upload(client, rows) == second
    upload(client, rows[:1])
    assert lineage(client, tool) == result


def test_session_merge_retains_per_field_versions_and_escaped_nested_paths(client):
    first = upload(client, [record("session_meta", 0, id="s", nested={"a/b~c": [1, None], "keep": "old"})])
    second = upload(client, [record("session_meta", 1, id="s", nested={"a/b~c": None, "new": False})])
    fields = lineage(client)["fields"]
    assert "/metadata/nested/a~1b~0c/0" not in fields
    assert fields["/metadata/nested/keep"]["import_id"] == first
    assert fields["/metadata/nested/new"]["import_id"] == second
    assert fields["/started_at"]["import_id"] == first


def test_boolean_and_number_values_do_not_share_evidence(client):
    first = upload(client, [record("session_meta", 0, id="s", flag=1)])
    second = upload(client, [record("session_meta", 0, id="s", flag=True)])
    value = lineage(client)["fields"]["/metadata/flag"]
    assert value["value"] is True and value["import_id"] == second != first


def test_missing_content_explicit_tool_name_and_item_llm_origins(client):
    upload(client, [record("session_meta", 0, id="s"),
                    record("response_item", 1, type="message", role="user", summary="ignored"),
                    record("event_msg", 2, type="token_count"),
                    record("response_item", 3, type="function_call", name="tool", id="c"),
                    record("event_msg", 4, type="item_completed", started_at_ms=1788739203000,
                           completed_at_ms=1788739204000, item={"type": "Reasoning", "id": "i"})])
    all_events = events(client)
    user = next(e for e in all_events if e["kind"] == "user")
    assert field(client, user, "/text")["value"] == ""
    assert refs(field(client, user, "/text")) == [(2, "/payload/content")]
    token = next(e for e in all_events if e["name"] == "Token usage")
    assert field(client, token, "/attributes/info")["origin"] == "inferred"
    tool = next(e for e in all_events if e["kind"] == "tool")
    assert field(client, tool, "/name")["origin"] == "normalized"
    item = next(e for e in all_events if e["source"] == "codex_item")
    assert field(client, item, "/start_time")["origin"] == "normalized"
    assert refs(field(client, item, "/start_time")) == [(5, "/payload/started_at_ms")]


def test_items_reported_duration_and_nested_payloads(client):
    upload(client, [record("session_meta", 0, id="s"), record("turn_context", 1, turn_id="inherited"),
                    record("event_msg", 3, type="item_completed", turn_id="explicit",
                           started_at_ms=1788739201000, completed_at_ms=1788739203000,
                           item={"id": "i", "type": "CommandExecution", "command": ["echo", "a b"],
                                 "duration": {"secs": 1, "nanos": 3}, "exit_code": 0,
                                 "extra/key": {"~": None}})])
    event = events(client)[0]
    fields = lineage(client, event)["fields"]
    assert fields["/start_time"]["origin"] == "calculated"
    assert refs(fields["/start_time"]) == [(3, "/payload/completed_at_ms"),
                                          (3, "/payload/item/duration/secs"), (3, "/payload/item/duration/nanos")]
    assert refs(fields["/turn_id"]) == [(3, "/payload/turn_id")]
    assert refs(fields["/attributes/item/extra~1key/~0"]) == [(3, "/payload/item/extra~1key/~0")]
    assert fields["/text"]["value"] == "echo 'a b'"


def test_legacy_migration_backfill_unknowns_and_rollback(client, imported):
    store = client.app.state.store
    event = next(store.export(imported))
    with store.connect() as db:
        db.execute("DROP TABLE event_field_sources")
        db.execute("DROP TABLE session_field_sources")
        db.execute("PRAGMA user_version=6")
    Store(store.path)
    assert not lineage(client, event, imported)["fields"]["/text"]["available"]
    response = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
    assert response.status_code == 200
    before = lineage(client, event, imported)
    assert before["fields"]["/text"]["available"]
    response = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes() + b"{broken")
    assert response.status_code == 422
    assert lineage(client, event, imported) == before
    store.ingest([Session(id="s", started_at=event["start_time"]), Event(
        id="plugin", session_id="s", sequence=0, kind="event", name="External", source="plugin",
        start_time=event["start_time"])])
    assert not field(client, {"id": "plugin"}, "/text")["available"]
    assert client.get(f'/api/v1/sessions/wrong/events/{event["id"]}/lineage').status_code == 404
    assert client.get('/api/v1/sessions/missing/lineage').status_code == 404
    with store.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()

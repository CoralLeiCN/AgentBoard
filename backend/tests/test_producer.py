"""Synthetic producer evidence must prevent classification without losing session data."""

import json
import sys

import pytest

from agentboard.cli import main
from agentboard.domain import Session
from agentboard.models import ModelGateway
from agentboard.store import Store


def rollout(sid="worker", originator="agentboard_classifier"):
    rows = [
        {"type": "session_meta", "payload": {"id": sid, "originator": originator, "source": "vscode"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Classify the purpose of this synthetic conversation."}]}},
        {"type": "future_record", "payload": {"unrecognized": [1, 2, 3]}},
    ]
    return "\r\n".join(json.dumps({"timestamp": "2026-09-11T00:00:00Z", **r}) for r in rows).encode() + b"\r\n"


def import_rollout(client, sid="worker", originator="agentboard_classifier"):
    response = client.post("/api/v1/import/codex", content=rollout(sid, originator))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("originator,producer", [
    ("agentboard", "agentboard"), ("agentboard_classifier", "agentboard"),
    ("Codex Desktop", None), ("codex-tui", None), ("codex_python_sdk", None),
    ("agentboard_customer_service", None), (None, None), (["agentboard"], None),
])
def test_originator_mapping_retains_raw_metadata(client, originator, producer):
    import_rollout(client, originator=originator)
    session = client.get("/api/v1/sessions/worker").json()
    assert session["producer"] == producer
    assert session["agent"] == "codex"
    assert session["metadata"].get("originator") == originator
    assert session["metadata"]["source"] == "vscode"
    assert client.get("/api/v1/sessions/worker/raw").content == rollout(originator=originator)
    fields = client.get("/api/v1/sessions/worker/lineage").json()["fields"]
    assert fields["/producer"]["available"]
    assert fields["/producer"]["sources"][0]["pointer"] == "/payload/originator"


def test_generated_sessions_are_browsable_but_all_classifier_entries_reject(client, monkeypatch):
    import_rollout(client)
    import_rollout(client, "customer", "codex_python_sdk")
    store = client.app.state.store
    assert store.classification_candidates(force=True, limit=1) == ["customer"]
    assert client.get("/api/v1/sessions").json()["total"] == 2
    assert client.get("/api/v1/sessions?producer=agentboard").json()["items"][0]["id"] == "worker"
    assert client.get("/api/v1/sessions?producer=unmarked").json()["total"] == 1
    assert client.get("/api/v1/sessions?producer=invalid").status_code == 422
    assert client.get("/api/v1/sessions?category=unclassified").json()["items"][0]["id"] == "customer"
    assert client.get("/api/v1/sessions?producer=agentboard&category=unclassified").json()["total"] == 0
    assert client.get("/api/v1/sessions/worker/events").json()["items"]

    def no_model(*args, **kwargs):
        pytest.fail("An excluded session must never invoke the model")

    monkeypatch.setattr(ModelGateway, "complete", no_model)
    for response in (
        client.get("/api/v1/sessions/worker/classification-input"),
        client.post("/api/v1/sessions/worker/classify"),
        client.put("/api/v1/sessions/worker/classification", json={
            "category": "analysis", "reason": "Synthetic result", "model": "test"}),
    ):
        assert response.status_code == 422, response.text
        assert "AgentBoard-generated" in response.json()["detail"]
    assert store.get_session("worker")["classification"] is None


def test_explicit_marking_survives_reimports_and_can_be_cleared(client, imported):
    path = f"/api/v1/sessions/{imported}"
    label = client.post(path + "/classify").json()
    before = client.get(path + "/raw").content
    marked = client.put(path + "/producer", json={"producer": "agentboard"})
    assert marked.status_code == 200
    assert marked.json()["classification"] == label
    assert client.get(path + "/lineage").json()["fields"]["/producer"]["origin"] == "unknown"
    client.post("/api/v1/import/codex", content=before)
    client.app.state.store.ingest([Session(id=imported, started_at="2026-09-11T00:00:00Z")])
    assert client.get(path).json()["producer"] == "agentboard"
    assert client.get(path + "/raw").content == before
    assert client.post(path + "/classify").status_code == 422
    assert client.put(path + "/producer", json={"producer": None}).json()["producer"] is None
    assert client.post(path + "/classify").status_code == 200
    for body in ({}, {"producer": "sdk"}, {"producer": True}, {"producer": "agentboard", "extra": 1}):
        assert client.put(path + "/producer", json=body).status_code == 422
    assert client.put("/api/v1/sessions/missing/producer", json={"producer": "agentboard"}).status_code == 404


def test_cli_force_skips_generated_sessions(client, monkeypatch, capsys):
    import_rollout(client)
    import_rollout(client, "customer", "codex_python_sdk")
    monkeypatch.setenv("AGENTBOARD_FEATURES", "classification")
    monkeypatch.setenv("AGENTBOARD_MODEL_MODE", "dummy")
    base = ["agentboard", "--database", client.app.state.store.path, "classify"]
    monkeypatch.setattr(sys, "argv", [*base, "worker", "--force"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "skipped" and "AgentBoard-generated" in result["reason"]
    monkeypatch.setattr(sys, "argv", [*base, "--all", "--force", "--limit", "1"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["session_id"] == "customer" and result["status"] == "classified"


def test_mark_during_model_request_prevents_saving_result(client, imported):
    gateway = client.app.state.gateway
    complete = gateway.complete

    def mark_then_complete(*args, **kwargs):
        client.app.state.store.set_producer(imported, "agentboard")
        return complete(*args, **kwargs)

    gateway.complete = mark_then_complete
    response = client.post(f"/api/v1/sessions/{imported}/classify")
    assert response.status_code == 422
    assert client.app.state.store.get_session(imported)["classification"] is None


@pytest.mark.parametrize("key", ["agentboard.producer", "originator", "service.name"])
@pytest.mark.parametrize("signal", ["logs", "traces"])
def test_telemetry_marks_session_and_unmarked_retries_preserve_it(client, key, signal):
    def attr(k, v):
        return {"key": k, "value": {"stringValue": v}}

    record = {"traceId": "ab" * 16, "spanId": "cd" * 8, "name": "chat", "eventName": "chat",
              "startTimeUnixNano": "1789084800000000000", "timeUnixNano": "1789084800000000000",
              "endTimeUnixNano": "1789084800000000001"}
    records, scopes, resources = ("logRecords", "scopeLogs", "resourceLogs") if signal == "logs" else (
        "spans", "scopeSpans", "resourceSpans")
    resource = {"attributes": [attr("conversation.id", "worker"), attr(key, "agentboard")]}
    body = {resources: [{"resource": resource, scopes: [{records: [record]}]}]}
    response = client.post(f"/v1/{signal}", json=body)
    assert response.status_code == 200, response.text
    resource["attributes"].pop()
    assert client.post(f"/v1/{signal}", json=body).status_code == 200
    assert client.get("/api/v1/sessions/worker").json()["producer"] == "agentboard"
    assert client.app.state.store.classification_candidates(force=True) == []


def test_late_telemetry_identity_carries_recorded_producer(client):
    from test_otlp_sessions import SID, attr, post, span

    first = span(1)
    first["attributes"].append(attr("agentboard.producer", "agentboard"))
    post(client, first)
    post(client, span(2, SID))
    assert client.get(f"/api/v1/sessions/{SID}").json()["producer"] == "agentboard"
    assert len(client.get(f"/api/v1/sessions/{SID}/events").json()["items"]) == 2


def test_v9_migration_preserves_data_and_backfills_only_known_producers(tmp_path):
    path = str(tmp_path / "old.db")
    store = Store(path)
    sessions = [
        Session(id="worker", started_at="2026-09-11T00:00:00Z", metadata={"originator": "agentboard"}),
        Session(id="sdk", started_at="2026-09-11T00:00:00Z", metadata={"originator": "codex_python_sdk"}),
        Session(id="replay", agent="replay", started_at="2026-09-11T00:00:00Z"),
    ]
    store.ingest(sessions)
    label = {"category": "analysis", "reason": "Existing result"}
    store.classify("worker", label)
    with store.connect() as db:
        db.execute("ALTER TABLE sessions DROP COLUMN producer")
        db.execute("PRAGMA user_version=9")
    upgraded = Store(path)
    assert upgraded.get_session("worker")["producer"] == "agentboard"
    assert upgraded.get_session("worker")["classification"] == label
    assert upgraded.get_session("worker")["metadata"] == sessions[0].metadata
    assert upgraded.get_session("sdk")["producer"] is None
    assert upgraded.get_session("replay")["producer"] == "agentboard"
    assert upgraded.classification_candidates(force=True) == ["sdk"]
    with upgraded.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert not db.execute("PRAGMA foreign_key_check").fetchall()

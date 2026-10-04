"""Synthetic paginated subagent rollouts preserve child identity and full history."""

import json

import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings


def subagent_records():
    return [
        {
            "timestamp": "2026-10-03T10:00:00Z",
            "ordinal": 0,
            "type": "session_meta",
            "payload": {
                "id": "child",
                "parent_thread_id": "parent",
                "forked_from_id": "parent",
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}},
                "thread_source": "subagent",
                "history_mode": "paginated",
                "subagent_history_start_ordinal": 4,
                "cwd": "/synthetic/child",
                "originator": "codex",
                "child_only": True,
            },
        },
        {
            "timestamp": "2026-10-03T09:00:00Z",
            "ordinal": 1,
            "type": "session_meta",
            "payload": {
                "id": "parent",
                "cwd": "/synthetic/parent",
                "source": "cli",
                "originator": "agentboard",
                "parent_only": True,
            },
        },
        {
            "timestamp": "2026-10-03T09:00:01Z",
            "ordinal": 2,
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Synthetic inherited input",
            },
        },
        {
            "timestamp": "2026-10-03T09:00:02Z",
            "ordinal": 3,
            "type": "future_record",
            "payload": {
                "unrecognized": [None, {"text": "é"}],
            },
        },
        {
            "timestamp": "2026-10-03T10:00:01Z",
            "ordinal": 4,
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Synthetic child input",
            },
        },
    ]


def body(rows):
    # Blank lines, CRLF, Unicode and no final newline must survive unchanged.
    return "\r\n\r\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()


@pytest.mark.parametrize("field_lineage", [False, True])
def test_inherited_parent_header_preserves_child_identity_and_raw_history(tmp_path, field_lineage):
    features = {"import", "raw_archive", "export", "inputs"}
    if field_lineage:
        features.add("field_lineage")
    settings = Settings(database=str(tmp_path / "child.db"), features=features, model_mode="dummy")
    with TestClient(create_app(settings)) as client:
        original = body(subagent_records())
        response = client.post("/api/v1/import/codex", content=original)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["session_ids"] == ["child"]
        assert client.get("/api/v1/sessions").json()["total"] == 1
        assert client.get("/api/v1/sessions/parent").status_code == 404
        session = client.get("/api/v1/sessions/child").json()
        assert session["started_at"] == "2026-10-03T10:00:00.000000000Z"
        assert session["metadata"] == subagent_records()[0]["payload"]
        assert session["producer"] is None
        events = client.get("/api/v1/sessions/child/events").json()["items"]
        inputs = [event for event in events if event["name"] == "Internal input"]
        assert [event["text"] for event in inputs] == ["Synthetic inherited input", "Synthetic child input"]
        assert all(event["session_id"] == "child" and event["kind"] == "event" for event in inputs)
        assert client.get("/api/v1/sessions/child/inputs").json()["items"] == []
        assert client.get("/api/v1/sessions/child/raw").content == original
        assert client.get(f"/api/v1/captures/{result['capture_id']}/raw").content == original
        if field_lineage:
            fields = client.get("/api/v1/sessions/child/lineage").json()["fields"]
            for path in ("/id", "/started_at", "/metadata/cwd"):
                assert {s["line_number"] for s in fields[path]["sources"]} == {1}
            for event in inputs:
                fields = client.get(f"/api/v1/sessions/child/events/{event['id']}/lineage").json()["fields"]
                assert {s["line_number"] for s in fields["/session_id"]["sources"]} == {1}
        repeat = client.post("/api/v1/import/codex", content=original).json()
        assert repeat["inserted_events"] == 0
        assert repeat["raw_import_ids"] == result["raw_import_ids"]
        grown = (
            original
            + b"\r\n"
            + json.dumps(
                {
                    "timestamp": "2026-10-03T10:00:02Z",
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "Synthetic follow-up"},
                }
            ).encode()
        )
        response = client.post("/api/v1/import/codex", content=grown)
        assert response.status_code == 200, response.text
        assert client.get("/api/v1/sessions/child/raw").content == grown
        assert (
            client.get(
                "/api/v1/sessions/child/raw", params={"import_id": result["raw_import_ids"][0]}
            ).content
            == original
        )


@pytest.mark.parametrize(
    "variant",
    [
        "unrelated",
        "missing_fork",
        "conflicting_parent",
        "conflicting_spawn",
        "not_subagent",
        "not_paginated",
        "late_parent",
        "second_parent",
        "third_session",
        "missing_late_id",
        "empty_late_id",
    ],
)
def test_unproven_or_late_identity_changes_still_reject_atomically(client, variant):
    rows = subagent_records()
    primary = rows[0]["payload"]
    if variant == "unrelated":
        rows[1]["payload"]["id"] = "unrelated"
    elif variant == "missing_fork":
        primary.pop("forked_from_id")
    elif variant == "conflicting_parent":
        primary["parent_thread_id"] = "unrelated"
    elif variant == "conflicting_spawn":
        primary["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = "unrelated"
    elif variant == "not_subagent":
        primary["source"] = "cli"
    elif variant == "not_paginated":
        primary.pop("history_mode")
    elif variant == "late_parent":
        rows[1], rows[2] = rows[2], rows[1]
    elif variant == "second_parent":
        rows.append(rows[1])
    elif variant == "third_session":
        rows.append({"timestamp": "2026-10-03T10:00:02Z", "type": "session_meta", "payload": {"id": "other"}})
    elif variant in ("missing_late_id", "empty_late_id"):
        rows.append(
            {
                "timestamp": "2026-10-03T10:00:02Z",
                "type": "session_meta",
                "payload": {
                    "id": None if variant == "missing_late_id" else "",
                },
            }
        )
    original = body(rows)
    response = client.post("/api/v1/import/codex", content=original)
    assert response.status_code == 422
    assert client.get("/api/v1/sessions").json()["total"] == 0
    assert client.get(f"/api/v1/captures/{response.json()['capture_id']}/raw").content == original

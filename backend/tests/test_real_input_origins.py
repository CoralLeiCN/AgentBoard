"""Reviewed redacted real inputs; source locators and original text stay outside Git."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from agentboard.adapters.codex import content_text
from agentboard.timestamps import timestamp_ns

ROOT = Path(__file__).parents[2] / "examples/fixtures/input-origin-real"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
CASES = MANIFEST["cases"]
IDS = {"id", "session_id", "thread_id", "turn_id", "root_turn_id", "parent_thread_id", "client_id", "trace_id"}
ALLOWED_KEYS = IDS | {
    "timestamp", "ordinal", "type", "payload", "cli_version", "source", "thread_source", "subagent", "other",
    "agentboard_fixture", "kind", "case", "partial_session", "role", "content", "text", "summary", "phase",
    "message", "last_agent_message", "internal_chat_message_metadata_passthrough", "item", "started_at_ms",
    "completed_at_ms", "images", "local_images", "audio", "local_audio", "text_elements",
}
ENUM_VALUES = {
    "message", "reasoning", "task_started", "task_complete", "token_count", "thread_settings_applied",
    "user_message", "agent_message", "item_completed", "input_text", "output_text", "summary_text",
    "text", "Text", "UserMessage", "Reasoning", "AgentMessage", "user", "assistant", "developer", "system",
    "commentary", "final_answer", "session_meta", "event_msg", "response_item", "world_state", "turn_context",
    "token_usage_record", "cli", "vscode", "guardian", "guardian_review", "desktop", "reviewer",
    "redacted_real_input_origin_excerpt",
}
REDACTED_TEXT = re.compile(
    r"(?:\s|\[redacted text \d+\]|</?(?:environment_context|INSTRUCTIONS|recommended_plugins)>"
    r"|# AGENTS\.md instructions for /example/workspace)*"
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case"])
def test_real_input_excerpt_redaction_and_provenance(case):
    body = (ROOT / case["file"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == case["sha256"]
    rows = [json.loads(line) for line in body.splitlines()]
    assert len(rows) == case["record_count"] == len(case["lines"])
    expected_lines = [n for start, end in case["contiguous_original_line_windows"] for n in range(start, end + 1)]
    assert [line["original_line"] for line in case["lines"]] == expected_lines
    assert [line["excerpt_line"] for line in case["lines"]] == list(range(1, len(rows) + 1))
    for row, source in zip(rows, case["lines"]):
        assert row.get("ordinal") == source["original_ordinal"]
        if "ordinal" in row:
            assert row["ordinal"] + 1 == source["original_line"]
    assert rows[0]["payload"]["cli_version"] == case["source_cli_version"]
    assert rows[0]["timestamp"] == "2000-01-01T00:00:00.000000000Z"

    def check(value, key=""):
        if isinstance(value, dict):
            assert set(value) <= ALLOWED_KEYS
            for child_key, child in value.items():
                check(child, child_key)
        elif isinstance(value, list):
            if key in ("images", "local_images", "audio", "local_audio", "text_elements"):
                assert value == []
            for child in value:
                check(child, key)
        elif isinstance(value, str):
            if key in IDS:
                assert value == case["session_id"] or re.fullmatch(case["case"] + r"-id-\d{3}", value)
            elif key == "timestamp":
                assert value.startswith("2000-01-01T")
            elif key == "cli_version":
                assert value in ("0.153.0", "0.153.4")
            elif key in ("text", "message", "last_agent_message"):
                assert REDACTED_TEXT.fullmatch(value)
            else:
                assert value in ENUM_VALUES
            assert not re.search(r"/Users/|/home/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
            assert not re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value, re.I)
            assert not re.search(r"-----BEGIN .*PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_|AKIA)", value)
        elif key in ("started_at_ms", "completed_at_ms"):
            assert 946684800000 <= value < 946771200000

    check(rows)
    public_manifest = (ROOT / "manifest.json").read_text()
    assert not re.search(r"/Users/|/home/|rollout-\d{4}|source_path|identifier_map|timestamp_shift_ns", public_manifest)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case"])
def test_real_input_excerpt_api_counts_waits_and_raw_evidence(client, case):
    body = (ROOT / case["file"]).read_bytes()
    response = client.post("/api/v1/import/codex", content=body)
    assert response.status_code == 200, response.text
    base = f"/api/v1/sessions/{case['session_id']}"
    assert client.get(base + "/raw").content == body
    events = client.get(base + "/events").json()["items"]
    inputs = [event for event in events if "input_attribution" in event["attributes"]]
    origins = Counter(event["attributes"]["input_attribution"]["origin"] for event in inputs)
    assert dict(origins) == case["current"]["attributed_inputs"]
    assert len(client.get(base + "/inputs").json()["items"]) == case["current"]["user_count"]
    assert client.get(base + "/stats").json()["counts"].get("user", 0) == case["current"]["user_count"]
    waits = [e for e in events if e["kind"] == "user_wait"]
    assert [(timestamp_ns(e["end_time"]) - timestamp_ns(e["start_time"])) // 1000000 for e in waits] == (
        case["current"]["between_turn_wait_ms"]
    )
    for event in inputs:
        raw = client.get(base + f"/events/{event['id']}/raw").json()
        assert raw["available"]
        assert len(raw["lines"]) == 1
        record = json.loads(raw["lines"][0]["text"])
        assert record["payload"]["role"] == "user"
        assert content_text(record["payload"]["content"]) == event["text"]
        assert raw["lines"][0]["line_number"] == event["sequence"]
        if event["kind"] != "user":
            for endpoint in ("replay", "codex-plan"):
                assert client.post(base + "/" + endpoint, json={
                    "input_id": event["id"], "replacement": "Synthetic replacement"
                }).status_code == 422
    if case["case"] == "desktop":
        rows = [json.loads(line) for line in body.splitlines()]
        assert waits[0]["start_time"] == rows[10]["timestamp"]
        assert waits[0]["end_time"] == rows[16]["timestamp"]
        assert timestamp_ns(rows[16]["timestamp"]) - timestamp_ns(rows[13]["timestamp"]) == 18000000
    if case["case"] == "reviewer":
        session = client.get(base).json()
        assert session["input_origin"]["origin"] == "internal"
        assert session["input_origin"]["parent_session_id"] == "reviewer-id-001"
        rows = [json.loads(line) for line in body.splitlines()]
        # Preserve the original fragmentation and equality with each event mirror.
        for message, mirror, count in ((rows[6], rows[7], 55), (rows[19], rows[20], 46)):
            assert len(message["payload"]["content"]) == count
            assert content_text(message["payload"]["content"]) == mirror["payload"]["message"]
    assert client.post("/api/v1/import/codex", content=body).json()["inserted_events"] == 0

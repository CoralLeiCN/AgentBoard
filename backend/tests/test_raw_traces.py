"""Synthetic source evidence; never use private rollouts as fixtures."""

import gzip
import hashlib
import json
import subprocess
import sys

import pytest

from agentboard.domain import Session
from agentboard.store import Store


def raw_trace():
    records = [
        {
            "timestamp": "2026-09-06T08:51:22.805Z",
            "type": "session_meta",
            "extra": None,
            "payload": {
                "id": "raw-test",
                "thread_source": "guardian_review",
                "source": {"subagent": {"other": "guardian"}},
                "base_instructions": {"text": "Synthetic instructions"},
                "git": {"branch": "example"},
                "future_field": [1, None, {"x": "é"}],
            },
        },
        {
            "timestamp": "2026-09-06T08:51:24Z",
            "type": "world_state",
            "payload": {"unknown_nested": [True, None, {"value": 1.23456789}]},
        },
        {
            "timestamp": "2026-09-06T08:51:25Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "id": "message-1",
                "content": [
                    {"type": "input_text", "text": "Example"},
                    {"type": "input_image", "image_url": "data:example"},
                ],
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
            },
        },
        {
            "timestamp": "2026-09-06T08:51:26Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [], "encrypted_content": "synthetic"},
        },
    ]
    # Preserve leading/interior blanks, CRLF, Unicode, whitespace, and no final newline.
    return ("\r\n" + "\r\n \r\n".join(json.dumps(r, ensure_ascii=False) for r in records)).encode()


@pytest.mark.parametrize("compressed", [False, True])
def test_exact_raw_roundtrip_and_metadata(client, compressed):
    body = raw_trace()
    response = client.post(
        "/api/v1/import/codex",
        content=gzip.compress(body) if compressed else body,
        headers={"Content-Encoding": "gzip"} if compressed else {},
    )
    assert response.status_code == 200, response.text
    path = "/api/v1/sessions/raw-test"
    archive = client.get(path + "/raw-imports").json()["items"][0]
    assert response.json()["raw_import_ids"] == [archive["id"]]
    assert archive["sha256"] == hashlib.sha256(body).hexdigest()
    assert archive["byte_count"] == len(body)
    assert archive["line_count"] == len(body.splitlines())
    assert archive["mapping_version"] == "codex-jsonl-v1"
    assert client.get(path + "/raw").content == body
    meta = client.get(path).json()["metadata"]
    assert meta["thread_source"] == "guardian_review"
    assert meta["future_field"] == [1, None, {"x": "é"}]
    assert meta["base_instructions"] == {"text": "Synthetic instructions"}
    assert meta["git"] == {"branch": "example"}
    events = client.get(path + "/events").json()["items"]
    # Physical source line still identifies the triggering record in the archive.
    user = next(e for e in events if e["kind"] == "user")
    assert json.loads(body.splitlines()[user["sequence"] - 1])["payload"]["id"] == "message-1"


def test_reimports_keep_versions_without_duplicate_archives(client):
    body = raw_trace()
    path = "/api/v1/sessions/raw-test"
    first = client.post("/api/v1/import/codex", content=body).json()
    repeat = client.post("/api/v1/import/codex", content=body).json()
    assert repeat["inserted_events"] == 0
    assert repeat["raw_import_ids"] == first["raw_import_ids"]
    # Changed same-line source and shortened snapshots must not overwrite prior evidence.
    changed = body.replace(b'"Example"', b'"Revised"')
    second = client.post("/api/v1/import/codex", content=changed).json()
    shortened = b"\n".join(body.splitlines()[:2])
    client.post("/api/v1/import/codex", content=shortened)
    assert len(client.get(path + "/raw-imports").json()["items"]) == 3
    assert client.get(path + "/raw").content == shortened
    assert client.get(path + "/raw", params={"import_id": first["raw_import_ids"][0]}).content == body
    assert client.get(path + "/raw", params={"import_id": second["raw_import_ids"][0]}).content == changed
    assert (
        client.get("/api/v1/sessions/other/raw", params={"import_id": first["raw_import_ids"][0]}).status_code
        == 404
    )


def test_failed_import_keeps_no_partial_archive(client):
    client.post("/api/v1/import/codex", content=raw_trace())
    result = client.post("/api/v1/import/codex", content=raw_trace() + b"\n{")
    assert result.status_code == 422
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM raw_imports").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert client.get("/api/v1/sessions/raw-test/raw").content == raw_trace()


def test_legacy_session_requires_reimport_and_v2_upgrade(tmp_path):
    path = tmp_path / "legacy.db"
    store = Store(str(path))
    store.ingest([Session(id="raw-test", started_at="2026-09-06T00:00:00Z")])
    with store.connect() as db:
        db.execute("DROP TABLE raw_lines")
        db.execute("DROP TABLE raw_imports")
        db.execute("PRAGMA user_version=2")
    upgraded = Store(str(path))
    assert upgraded.raw_imports("raw-test") == []
    with pytest.raises(KeyError, match="reimport"):
        upgraded.raw_import("raw-test")
    assert upgraded.get_session("raw-test")["started_at"] == "2026-09-06T00:00:00.000000000Z"


def test_cli_preserves_original_bytes(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_bytes(raw_trace())
    command = [
        sys.executable,
        "-c",
        "from agentboard.cli import main; main()",
        "--database",
        str(tmp_path / "cli.db"),
    ]
    result = subprocess.run([*command, "import", str(source)], capture_output=True, check=True)
    archive_id = json.loads(result.stdout)["raw_import_ids"][0]
    exported = subprocess.run(
        [*command, "export", "raw-test", "--raw", "--import-id", str(archive_id)],
        capture_output=True,
        check=True,
    )
    assert exported.stdout == source.read_bytes()

"""Complete source preservation through real HTTP/CLI boundaries on synthetic data."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_raw_traces import raw_trace

from agentboard.api import create_app
from agentboard.config import Settings
from agentboard.ingestion import CaptureError
from agentboard.runtime import Runtime
from agentboard.store import Store


@pytest.mark.parametrize("features", [{"import"}, {"import", "field_lineage"}, {"import", "token_usage"}])
def test_complete_capture_independent_of_inspection_and_analysis(tmp_path, features):
    settings = Settings(database=str(tmp_path / "raw.db"), features=features)
    body = raw_trace()
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/import/codex", content=body)
        assert response.status_code == 200
        capture_id = response.json()["capture_id"]
        assert client.get("/api/v1/captures").status_code == 404
        assert client.get("/api/v1/sessions/raw-test/raw-imports").status_code == 404
        assert b"".join(client.app.state.runtime.captures.export(capture_id)) == body
        if "field_lineage" in features:
            assert client.get("/api/v1/sessions/raw-test/lineage").json()["fields"]
        if "token_usage" in features:
            assert client.get("/api/v1/sessions/raw-test/usage").status_code == 200
    with TestClient(create_app(Settings(database=settings.database, features={"raw_archive", "export"}))) as client:
        assert client.get(f"/api/v1/captures/{capture_id}/raw").content == body
        assert client.get("/api/v1/sessions/raw-test/raw").content == body


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("signal", ["logs", "traces"])
def test_otlp_preserves_unknown_data_and_original_transport(tmp_path, compressed, binary, signal):
    # Protobuf field 100, varint 123; unknown to the current OTLP request schema.
    payload = b"\xa0\x06\x7b" if binary else b'{ "futureField": [null, {"value": 1.234567890123456789}] }\n'
    body = gzip.compress(payload) if compressed else payload
    headers = {"Content-Type": "application/x-protobuf" if binary else "application/json",
               "Content-Encoding": "gzip" if compressed else "identity"}
    settings = Settings(database=str(tmp_path / "otlp.db"), features={f"otlp_{signal}", "raw_archive", "export"})
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/v1/{signal}", content=body, headers=headers)
        assert response.status_code == 200, response.text
        capture_id = response.headers["X-AgentBoard-Capture-ID"]
        metadata = client.get(f"/api/v1/captures/{capture_id}").json()
        assert metadata["source"] == f"otlp_{signal}"
        assert metadata["content_encoding"] == headers["Content-Encoding"]
        assert metadata["sha256"] == hashlib.sha256(body).hexdigest()
        assert client.get(f"/api/v1/captures/{capture_id}/raw").content == body
        assert response.json() == {} if not binary else response.content == b""


@pytest.mark.parametrize("message", ["Unsupported mapping", ""])
def test_failed_interpretation_keeps_complete_capture_and_can_be_reprocessed(tmp_path, monkeypatch, message):
    settings = Settings(database=str(tmp_path / "failure.db"), features={"import"})
    with Runtime(settings) as runtime:
        parser = runtime.adapters["codex"]
        with monkeypatch.context() as patch:
            patch.setattr(parser, "parse", lambda _: (_ for _ in ()).throw(ValueError(message)))
            with pytest.raises(CaptureError) as failure:
                runtime.ingestion.import_body("codex", raw_trace())
        capture_id = failure.value.capture_id
        assert runtime.captures.get(capture_id)["status"] == "failed"
        assert runtime.store.list_sessions()["total"] == 0
        result = runtime.ingestion.reprocess(capture_id)
        assert result["session_ids"] == ["raw-test"]
        assert runtime.captures.get(capture_id)["status"] == "failed"  # Prior attempt remains truthful.
        assert runtime.captures.get(result["capture_id"])["status"] == "normalized"
        with runtime.store.connect() as db:
            assert db.execute("SELECT count(*) FROM captured_payloads").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM captures").fetchone()[0] == 2
    with Runtime(Settings(database=settings.database, features=set())) as disabled:
        with pytest.raises(ValueError, match="disabled"):
            disabled.ingestion.reprocess(capture_id)


def test_failure_response_identifies_retained_bytes_and_canonical_rollback(client):
    body = raw_trace() + b"\n{invalid"
    response = client.post("/api/v1/import/codex", content=body)
    assert response.status_code == 422
    capture_id = response.json()["capture_id"]
    assert response.json()["capture_status"] == "retained"
    assert client.get(f"/api/v1/captures/{capture_id}").json()["status"] == "failed"
    assert client.get(f"/api/v1/captures/{capture_id}/raw").content == body
    assert client.get("/api/v1/sessions").json()["total"] == 0


def test_export_disabled_hides_capture_download(client, tmp_path):
    settings = Settings(database=str(tmp_path / "inspect.db"), features={"import", "raw_archive"})
    with TestClient(create_app(settings)) as c:
        capture_id = c.post("/api/v1/import/codex", content=raw_trace()).json()["capture_id"]
        assert c.get(f"/api/v1/captures/{capture_id}").status_code == 200
        assert c.get(f"/api/v1/captures/{capture_id}/raw").status_code == 404
        assert "/api/v1/captures/{capture_id}/raw" not in c.get("/openapi.json").json()["paths"]


def test_cli_preserves_bytes_without_inspection_and_after_parse_failure(tmp_path):
    body = raw_trace() + b"\n{invalid"
    source = tmp_path / "invalid.jsonl"
    source.write_bytes(body)
    config = tmp_path / "settings.toml"
    config.write_text('database = "raw.db"\nfeatures = ["import"]\n')
    command = [sys.executable, "-c", "from agentboard.cli import main; main()", "--config", str(config)]
    response = subprocess.run([*command, "import", str(source)], capture_output=True)
    assert response.returncode == 1
    capture_id = json.loads(response.stdout)["capture_id"]
    source.unlink()
    config.write_text('database = "raw.db"\nfeatures = ["raw_archive", "export"]\n')
    exported = subprocess.run([*command, "capture-export", str(capture_id)], capture_output=True, check=True)
    assert exported.stdout == body


def test_body_limits_never_acknowledge_partial_capture(tmp_path):
    settings = Settings(database=str(tmp_path / "limits.db"), features={"import", "raw_archive", "export"}, max_body_bytes=128)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/import/codex", content=b"x" * 129).status_code == 413
        assert client.get("/api/v1/captures").json()["items"] == []
        body = gzip.compress(b"x" * 10000)
        response = client.post("/api/v1/import/codex", content=body, headers={"Content-Encoding": "gzip"})
        assert response.status_code == 413
        capture_id = response.json()["capture_id"]
        assert client.get(f"/api/v1/captures/{capture_id}/raw").content == body


def test_storage_contention_remains_retryable_after_capture(client, monkeypatch):
    def busy(_):
        raise sqlite3.OperationalError("synthetic database busy")

    monkeypatch.setattr(client.app.state.store, "ingest", busy)
    response = client.post("/api/v1/import/codex", content=raw_trace())
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    assert client.get(f'/api/v1/captures/{response.json()["capture_id"]}/raw').content == raw_trace()


def test_v8_upgrade_preserves_existing_archives_without_fabricating_captures(tmp_path):
    path = str(tmp_path / "legacy.db")
    with Runtime(Settings(database=path, features={"import"})) as runtime:
        runtime.ingestion.import_body("codex", raw_trace())
        with runtime.store.connect() as db:
            db.execute("DROP TABLE captures")
            db.execute("DROP TABLE captured_payloads")
            db.execute("PRAGMA user_version=8")
    store = Store(path)
    assert b"".join(store.export_raw("raw-test")) == raw_trace()
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("SELECT count(*) FROM captures").fetchone()[0] == 0
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("transport", [True, False])
def test_raw_export_can_resume_on_different_http_worker_threads(tmp_path, transport):
    # Starlette advances sync streaming iterators through its shared worker pool.
    # Each next() is sequential, but it need not run on the same worker.
    body = (raw_trace() + b" " * (1024 * 1024 + 1) + b"\n") if transport else raw_trace()
    with Runtime(Settings(database=str(tmp_path / "stream.db"), features={"import"})) as runtime:
        result = runtime.ingestion.import_body("codex", body)
        chunks = (runtime.captures.export(result["capture_id"]) if transport
                  else runtime.store.export_raw("raw-test"))
        with ThreadPoolExecutor(max_workers=1) as first, ThreadPoolExecutor(max_workers=1) as second:
            collected = [first.submit(next, chunks).result()]
            while (chunk := second.submit(next, chunks, None).result()) is not None:
                collected.append(chunk)
        assert b"".join(collected) == body

"""Synthetic cross-session reports, date boundaries, filters and tag persistence."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.config import Settings
from agentboard.domain import Session
from agentboard.store import Store


def imported(client, sid, timestamps=("2026-09-07T12:00:00Z",), model="gpt-5.4-mini", metadata=None):
    rows = [{"type": "session_meta", "timestamp": "2026-09-01T00:00:00Z",
             "payload": {"id": sid, **(metadata or {})}},
            {"type": "turn_context", "timestamp": "2026-09-01T00:00:00Z", "payload": {"model": model}}]
    for index, stamp in enumerate(timestamps):
        rows.append({"type": "token_usage_record", "timestamp": stamp, "payload": {
            "response_id": f"{sid}-{index}", "usage": {"input_tokens": 1000, "cached_input_tokens": 200,
            "cache_write_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 40}}})
    result = client.post("/api/v1/import/codex", content="\n".join(map(json.dumps, rows)))
    assert result.status_code == 200, result.text
    return result


def report(client, **params):
    result = client.get("/api/v1/usage-summary", params=params)
    assert result.status_code == 200, result.text
    return result.json()


def test_summary_counts_all_pages_latest_archives_and_no_mirrors(client):
    for index in range(23):
        imported(client, f"s{index}")
    imported(client, "s0", timestamps=("2026-09-07T12:00:00Z", "2026-09-08T12:00:00Z"))
    data = report(client, limit=2)
    assert len(data["items"]) == 2
    assert data["next_offset"] == 2
    assert data["summary"]["sessions"] == 23
    assert data["summary"]["records"] == 24
    assert data["summary"]["tokens"]["total_tokens"] == 26400
    assert Decimal(data["summary"]["estimated_cost_usd"]) == Decimal("0.02556")
    assert report(client, offset=22)["next_offset"] is None
    assert len(report(client, offset=22)["items"]) == 1
    assert sum(day["tokens"]["total_tokens"] for day in data["daily"]) == 26400


def test_auto_review_dashboard_prices_luna_and_preserves_recorded_model(client):
    imported(client, "synthetic-review", model="codex-auto-review")
    data = report(client, model="codex-auto-review")
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["unpriced_records"] == 0
    assert Decimal(data["summary"]["estimated_cost_usd"]) == Decimal("0.000284")
    assert data["models"][0]["model"] == "codex-auto-review"
    assert data["facets"]["models"] == ["codex-auto-review"]
    assert report(client, model="gpt-5.6-luna")["summary"]["sessions"] == 0


def test_date_filter_applies_to_usage_not_session_start_and_normalizes_offsets(client):
    imported(client, "spanning", timestamps=("2026-09-06T23:59:59.999999999Z", "2026-09-07T00:00:00Z",
                                            "2026-09-07T13:00:00+01:00", "2026-09-08T00:00:00Z"))
    data = report(client, start="2026-09-07T01:00:00+01:00", end="2026-09-08T00:00:00Z")
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["records"] == 2
    assert data["daily"][0]["day"] == "2026-09-07"
    assert len(data["daily"]) == 1
    assert report(client, start="2026-09-09T00:00:00Z")["summary"]["sessions"] == 0


def test_metadata_exact_types_nested_keys_all_tags_and_producer(client):
    imported(client, "matching", metadata={"git": {"branch": "main"}, "service.name": "example", "flag": True})
    imported(client, "other", metadata={"git": {"branch": "dev"}, "flag": 1, "originator": "agentboard"})
    client.put("/api/v1/sessions/matching/tags", json={"tags": ["work", "review"]})
    data = report(client, metadata=json.dumps({"/git/branch": "main", "/flag": True}), tag=["work", "review"])
    assert data["summary"]["sessions"] == 1
    assert data["items"][0]["tags"] == ["review", "work"]
    assert data["facets"]["metadata"]["/service.name"] == ['"example"']
    assert report(client, metadata='{"/flag":1}')["items"][0]["id"] == "other"
    assert report(client, tag=["work", "absent"])["summary"]["sessions"] == 0
    assert report(client, producer="agentboard")["items"][0]["id"] == "other"
    assert report(client, q="MATCHING", agent="codex")["summary"]["sessions"] == 1


def test_unknown_prices_missing_usage_and_invalid_times_are_visible(client):
    imported(client, "unpriced", model="unknown-model")
    imported(client, "undated")
    # Simulate retained legacy evidence with no usage timestamp, bypassing current import validation.
    with client.app.state.store.connect() as db:
        row = db.execute("SELECT l.import_id,l.sequence,l.content FROM raw_lines l JOIN raw_imports r "
                         "ON r.id=l.import_id WHERE r.session_id='undated' AND l.sequence=3").fetchone()
        content = json.loads(row["content"])
        content["timestamp"] = None
        db.execute("UPDATE raw_lines SET content=? WHERE import_id=? AND sequence=?",
                   (json.dumps(content).encode(), row["import_id"], row["sequence"]))
    client.app.state.store.ingest([Session(id="missing", started_at="2026-09-07T00:00:00Z")])
    data = report(client)
    summary = data["summary"]
    assert summary["sessions"] == 3
    assert summary["sessions_without_usage"] == 1
    assert summary["unpriced_records"] == 1
    assert summary["estimated_cost_usd"] is None
    assert summary["priced_subtotal_usd"] is not None
    assert data["undated"]["records"] == 1
    bounded = report(client, start="2026-09-07T00:00:00Z", end="2026-09-08T00:00:00Z")
    assert bounded["summary"]["records"] == 1
    assert bounded["summary"]["excluded_unknown_time_records"] == 1
    assert report(client, model="unknown-model")["summary"]["sessions"] == 1
    assert report(client, model="unknown-model")["summary"]["priced_subtotal_usd"] is None


def test_unattributed_telemetry_excluded_and_no_rows_are_unavailable(client):
    client.app.state.store.ingest([Session(id="bucket", identity_kind="unattributed_trace",
                                          started_at="2026-09-07T00:00:00Z")])
    data = report(client)
    assert data["summary"]["sessions"] == 0
    assert data["summary"]["tokens"]["total_tokens"] is None
    assert data["summary"]["estimated_cost_usd"] is None


def test_model_filters_only_matching_usage_and_long_context_uses_full_archive(client):
    from agentboard.dashboard import build_report
    from agentboard.usage import analyze

    rows = [{"type": "token_usage_record", "timestamp": stamp, "payload": {
        "response_id": str(i), "model": model, "usage": {"input_tokens": size, "output_tokens": 100,
                                                       "cached_input_tokens": 0}}}
        for i, (stamp, model, size) in enumerate([
            ("2026-09-06T00:00:00Z", "gpt-5.4", 300000),
            ("2026-09-07T00:00:00Z", "gpt-5.4", 1000),
            ("2026-09-07T00:00:00Z", "gpt-5.4-mini", 1000)])]
    result = build_report([dict(id="s", title="s", metadata={}, tags=[], agent="codex", producer=None,
                                started_at="2026-09-01T00:00:00Z")],
                          lambda _: analyze(map(json.dumps, rows)), lambda *_: False,
                          start="2026-09-07T00:00:00Z", model="gpt-5.4")
    assert result["summary"]["records"] == 1
    assert Decimal(result["summary"]["estimated_cost_usd"]) == Decimal("0.00725")


@pytest.mark.parametrize("params", [dict(start="yesterday"), dict(start="2026-09-07T00:00:00Z",
    end="2026-09-07T00:00:00Z"), dict(metadata="[]"), dict(metadata="{"), dict(metadata='{"branch":"main"}'),
    dict(producer="other"), dict(limit=0), dict(offset=-1)])
def test_invalid_filters_rejected(client, params):
    assert client.get("/api/v1/usage-summary", params=params).status_code == 422


def test_tags_survive_reimport_restart_and_v10_upgrade(client):
    imported(client, "tagged")
    with client.app.state.store.connect() as db:
        db.execute("DROP TABLE session_tags")
        db.execute("PRAGMA user_version=10")
    Store(client.app.state.store.path)
    result = client.put("/api/v1/sessions/tagged/tags", json={"tags": [" work ", "work", "review"]})
    assert result.json() == {"tags": ["review", "work"]}
    imported(client, "tagged", metadata={"git": {"branch": "new"}})
    assert Store(client.app.state.store.path).get_session("tagged")["tags"] == ["review", "work"]
    for tags in [[""], [1], ["bad,tag"], ["a" * 81]]:
        assert client.put("/api/v1/sessions/tagged/tags", json={"tags": tags}).status_code == 422
    assert client.get("/api/v1/sessions/tagged").json()["tags"] == ["review", "work"]
    assert client.put("/api/v1/sessions/missing/tags", json={"tags": []}).status_code == 404
    assert client.put("/api/v1/sessions/tagged/tags", json={"tags": []}).json() == {"tags": []}


def test_dashboard_requires_usage_feature_but_tags_are_core(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path / "disabled.db"), features=set()))) as client:
        assert client.get("/api/v1/usage-summary").status_code == 404
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/usage-summary" not in paths
        assert "/api/v1/sessions/{sid}/tags" in paths


def test_tagged_empty_telemetry_shell_is_retained(client):
    store = client.app.state.store
    store.ingest([Session(id="shell", started_at="2026-09-07T00:00:00Z", identity_kind="unattributed_trace")])
    store.set_tags("shell", ["investigate"])
    with store.connect() as db:
        store._remove_empty_otel_sessions(db, ["shell"])
    assert store.get_session("shell")["tags"] == ["investigate"]

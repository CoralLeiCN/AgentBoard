"""Usage regressions against preserved counters from a redacted real rollout."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agentboard.usage import analyze

FIXTURE = Path(__file__).parents[2] / "examples/fixtures/codex-real-usage-slice.jsonl"
EXPECTED = dict(input_tokens=193844, cached_input_tokens=156928, cache_write_input_tokens=0,
                output_tokens=809, reasoning_output_tokens=140, total_tokens=194653)
BASE = "/api/v1/sessions/real-codex-usage-slice-v1/usage"


def test_real_slice_provenance_and_recorded_counter_relationships():
    manifest = json.loads(FIXTURE.with_suffix(".provenance.json").read_text())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == manifest["sha256"]
    source = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    assert len(source) == manifest["record_count"] == 17
    for position, (row, evidence) in enumerate(zip(source, manifest["lines"], strict=True), 1):
        assert evidence["excerpt_line"] == position
        assert row["ordinal"] == evidence["original_ordinal"] == evidence["original_line"] - 1
        assert set(row["payload"]) == set(evidence["retained_payload_fields"])
    responses = [row["payload"] for row in source if row["type"] == "token_usage_record"]
    mirrors = [row["payload"]["info"] for row in source if row["type"] == "event_msg"]
    assert len(responses) == len(mirrors) == 6
    assert {key: sum(row["usage"][key] for row in responses) for key in EXPECTED} == EXPECTED
    assert responses[-1]["thread_token_usage"] == EXPECTED
    for response, mirror in zip(responses, mirrors, strict=True):
        assert mirror["last_token_usage"] == response["usage"]
    # This recorded mirror resets at the second turn; the thread total does not.
    assert mirrors[-1]["total_token_usage"] == responses[-1]["turn_token_usage"]
    assert mirrors[-1]["total_token_usage"]["total_tokens"] < mirrors[-2]["total_token_usage"]["total_tokens"]


def test_real_slice_import_api_pagination_export_and_weighted_breakdown(client):
    imported = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
    assert imported.status_code == 200, imported.text
    result = client.get(BASE).json()
    assert result["analysis_version"] == "codex-usage-v3"
    assert result["coverage"] == "recorded"  # Coverage of this archive, not the full session.
    assert result["source"] == "token_usage_record"
    assert result["records"] == result["priced_records"] == 6
    assert result["tokens"] == EXPECTED
    assert result["breakdown"]["uncached_input_tokens"] == 36916
    assert result["breakdown"]["reasoning_percent_of_total"] == pytest.approx(100 * 140 / 194653)
    assert result["models"][0]["breakdown"] == result["breakdown"]
    assert result["models"][0]["tokens"] == EXPECTED
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.566538")
    expected_costs = [
        ("0.26227", "0", "0.00605"), ("0.00344", "0.026112", "0.0064"),
        ("0.07532", "0.02624", "0.00635"), ("0.01955", "0.033536", "0.00215"),
        ("0.00517", "0.035328", "0.00945"), ("0.00341", "0.035712", "0.01005"),
    ]
    for row, expected in zip(result["items"], expected_costs, strict=True):
        parts = row["cost"]["components_usd"]
        assert Decimal(parts["cache_write"]) == 0
        assert tuple(Decimal(parts[key]) for key in ("input", "cached_input", "output")) == tuple(map(Decimal, expected))
        assert sum(map(Decimal, parts.values())) == Decimal(row["cost"]["usd"])
    assert [row["line_number"] for row in result["items"]] == [4, 6, 8, 10, 12, 16]
    assert [row["breakdown"]["uncached_input_tokens"] for row in result["items"]] == [26227, 344, 7532, 1955, 517, 341]
    assert [row["breakdown"]["reasoning_percent_of_total"] for row in result["items"]] == pytest.approx(
        [0, 0, 100 * 59 / 33899, 0, 0, 100 * 81 / 36254])
    assert result["items"][-1]["cumulative_tokens"] == EXPECTED["total_tokens"]
    assert "cumulative_resets" not in result["findings"]
    assert result["usage_summary_mismatches"] == []
    repeated = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes()).json()
    assert repeated["raw_import_ids"] == imported.json()["raw_import_ids"]
    first = client.get(BASE, params={"limit": 2}).json()
    assert first["breakdown"] == result["breakdown"]
    rest = client.get(BASE, params={"import_id": first["archive"]["id"], "after": first["next_cursor"]}).json()
    assert first["items"] + rest["items"] == result["items"]
    exported = client.get(BASE + "/export").json()
    assert exported["items"] == result["items"]
    assert exported["breakdown"] == result["breakdown"]


def test_real_slice_legacy_projection_marks_the_observed_turn_reset_partial():
    # Deliberate format projection; the original fixture remains byte-for-byte unchanged.
    lines = [line for line in FIXTURE.read_text().splitlines() if json.loads(line)["type"] != "token_usage_record"]
    result = analyze(lines)
    assert result["source"] == "token_count"
    assert result["coverage"] == "partial"
    assert result["records"] == 5
    assert result["tokens"]["total_tokens"] == 158399
    assert result["breakdown"]["uncached_input_tokens"] == 36575
    assert result["breakdown"]["reasoning_percent_of_total"] == pytest.approx(100 * 59 / 158399)
    assert result["findings"]["cumulative_resets"] == 1
    assert result["estimated_cost_usd"] is None


def test_missing_response_with_retained_turn_summary_warns_in_api_and_export(client):
    # Deliberately omit one response; this is a corruption experiment, not a real missing record.
    lines = FIXTURE.read_text().splitlines()
    body = "\n".join(line for number, line in enumerate(lines, 1) if number != 16)
    assert client.post("/api/v1/import/codex", content=body).status_code == 200
    for endpoint in (BASE, BASE + "/export"):
        result = client.get(endpoint).json()
        assert result["tokens"]["total_tokens"] == 158399
        assert result["coverage"] == "partial"
        assert result["estimated_cost_usd"] is None
        assert Decimal(result["priced_subtotal_usd"]) == Decimal("0.517366")
        assert result["findings"]["usage_summary_mismatches"] == 1
        mismatch, = result["usage_summary_mismatches"]
        assert mismatch["line_number"] == 16  # Former line 17 in this shortened archive.
        assert mismatch["scope"] == "thread_or_turn"
        assert mismatch["turn_id"] == "usage-example-007"
        assert mismatch["tokens"]["total_tokens"] == 36254

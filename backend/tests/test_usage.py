import json
from decimal import Decimal
from pathlib import Path

import pytest

from agentboard.pricing import catalog, price
from agentboard.usage import analyze, token_counts


def counts(input=1000, cached=200, output=100, reasoning=40, writes=100):
    return dict(input_tokens=input, cached_input_tokens=cached, output_tokens=output,
                reasoning_output_tokens=reasoning, cache_write_input_tokens=writes,
                total_tokens=input + output)


def record(kind, payload):
    return {"timestamp": "2026-09-07T12:00:00Z", "type": kind, "payload": payload}


def context(model="gpt-5.6-sol", turn="turn-1"):
    return record("turn_context", {"model": model, "turn_id": turn})


def response(usage=None, response_id="r1", **extra):
    return record("token_usage_record", {"response_id": response_id, "usage": usage or counts(), **extra})


def cumulative(total=None, last=None):
    return record("event_msg", {"type": "token_count", "info": {
        "total_token_usage": total or counts(), "last_token_usage": last or counts(),
    }})


def report(*rows, model_override=""):
    return analyze((json.dumps(row) for row in rows), model_override)


def import_rows(client, *rows):
    body = "\n".join(json.dumps(row) for row in (
        record("session_meta", {"id": "usage-test"}), *rows
    ))
    result = client.post("/api/v1/import/codex", content=body)
    assert result.status_code == 200, result.text
    return result.json()["raw_import_ids"][0]


def test_price_includes_cache_writes_and_reasoning_only_once():
    # 700 ordinary * $4 + 200 cached * $0.4 + 100 writes * $5 + 100 output * $20.
    result = report(context(), response())
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.00538")
    assert result["tokens"]["total_tokens"] == 1100
    assert result["tokens"]["reasoning_output_tokens"] == 40
    assert result["breakdown"]["uncached_input_tokens"] == 800  # Includes the 100 cache writes.
    assert result["breakdown"]["reasoning_percent_of_total"] == pytest.approx(100 * 40 / 1100)
    assert result["items"][0]["cost"]["components_usd"] == {
        "input": "0.0028", "cached_input": "0.00008", "cache_write": "0.0005", "output": "0.002",
    }


def test_response_ids_and_cumulative_mirrors_are_not_double_counted():
    result = report(context(), response(), cumulative(), response(), cumulative(),
                    response(response_id="r2"))
    assert result["records"] == 2  # Equal counts with different response IDs are real usage.
    assert result["tokens"]["total_tokens"] == 2200
    assert result["findings"]["duplicate_response_records"] == 1
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.01076")


def test_model_changes_use_chronological_context_and_override_is_explicit():
    rows = [context(), response(), context("gpt-5.6-luna"), response(response_id="r2")]
    result = report(*rows)
    assert [row["model"] for row in result["items"]] == ["gpt-5.6-sol", "gpt-5.6-luna"]
    assert [group["model"] for group in result["models"]] == ["gpt-5.6-sol", "gpt-5.6-luna"]
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.005669")
    override = report(*rows, model_override="gpt-5.6-sol")
    assert Decimal(override["estimated_cost_usd"]) == Decimal("0.01076")
    assert override["items"][1]["model"] == "gpt-5.6-luna"
    assert override["model_override"] == "gpt-5.6-sol"


def test_legacy_cumulative_totals_are_differenced_and_repeats_ignored():
    result = report(context(), cumulative(), cumulative(),
                    cumulative(counts(input=2000, cached=400, output=200, reasoning=80, writes=200)))
    assert result["source"] == "token_count"
    assert result["records"] == 2
    assert result["tokens"]["total_tokens"] == 2200
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.01076")
    assert [row["cumulative_tokens"] for row in result["items"]] == [1100, 2200]


def test_legacy_aggregate_has_unknown_model_and_context_tier():
    result = report(context(), cumulative(counts(input=2000000), counts()), model_override="gpt-5.6-sol")
    assert result["tokens"]["total_tokens"] == 2000100
    assert result["items"][0]["model"] is None
    assert result["items"][0]["request_known"] is False
    assert result["estimated_cost_usd"] is None
    assert result["priced_subtotal_usd"] is None


def test_cumulative_reset_and_conflicting_response_are_partial():
    result = report(context(), cumulative(), cumulative(counts(input=500)),
                    cumulative(counts(input=1500, cached=400, output=200, reasoning=80, writes=200)))
    assert result["coverage"] == "partial"
    assert result["findings"]["cumulative_resets"] == 1
    assert result["estimated_cost_usd"] is None
    result = report(context(), response(), response(counts(input=500)))
    assert result["records"] == 1
    assert result["tokens"]["input_tokens"] == 1000
    assert result["coverage"] == "partial"
    assert result["findings"]["conflicting_response_records"] == 1


def test_mixed_formats_report_missing_coverage_instead_of_summing():
    result = report(context(), cumulative(counts(input=2000)), response())
    assert result["tokens"]["total_tokens"] == 1100
    assert result["findings"]["tokens_outside_response_records"] == 1000
    assert result["coverage"] == "partial"
    assert result["estimated_cost_usd"] is None
    assert result["priced_subtotal_usd"] is not None


@pytest.mark.parametrize("scope", ["thread_token_usage", "turn_token_usage"])
@pytest.mark.parametrize("summary", [counts(input=500), counts(input=1500), counts(cached=300)])
def test_summary_consistency_checks_counts_in_both_directions_and_breakdowns(scope, summary):
    result = report(context(), response(**{scope: summary}))
    assert result["tokens"] == counts()
    assert result["findings"]["usage_summary_mismatches"] == 1
    assert result["usage_summary_mismatches"][0]["line_number"] == 2
    assert result["coverage"] == "partial"
    assert result["estimated_cost_usd"] is None
    assert result["priced_subtotal_usd"] == "0.00538"


@pytest.mark.parametrize("mirror_scope", ["thread", "turn"])
def test_consistent_summaries_accept_both_mirror_scopes_across_turns(mirror_scope):
    second = counts(input=1500)
    total = {key: counts()[key] + second[key] for key in counts()}
    result = report(context(), response(thread_token_usage=counts(), turn_token_usage=counts()),
                    cumulative(), context(turn="turn-2"),
                    response(second, response_id="r2", thread_token_usage=total, turn_token_usage=second),
                    cumulative(total if mirror_scope == "thread" else second, second))
    assert result["tokens"] == total
    assert result["coverage"] == "recorded"
    assert result["usage_summary_mismatches"] == []


def test_consistency_does_not_treat_missing_optional_breakdowns_as_zero():
    minimal = {"input_tokens": 1000, "output_tokens": 100}
    for usage, summary in [(minimal, counts()), (counts(), minimal)]:
        result = report(context(), response(usage, thread_token_usage=summary))
        assert result["coverage"] == "recorded"
        assert result["usage_summary_mismatches"] == []


@pytest.mark.parametrize("model", [None, "demo-model", "gpt-6-astra-secret", "gpt-5.4-2099-01-01"])
def test_unknown_models_are_unpriced_not_free(model):
    result = report(context(model), response())
    assert result["tokens"]["total_tokens"] == 1100
    assert result["unpriced_records"] == 1
    assert result["estimated_cost_usd"] is None
    assert result["priced_subtotal_usd"] is None
    assert result["items"][0]["cumulative_priced_usd"] is None


@pytest.mark.parametrize("field,value", [("input_tokens", True), ("output_tokens", -1),
    ("cached_input_tokens", 1.5), ("input_tokens", "1000"), ("output_tokens", None),
    ("cached_input_tokens", 1001), ("reasoning_output_tokens", 101), ("total_tokens", 1140)])
def test_invalid_counts_excluded_without_coercion(field, value):
    invalid = {**counts(), field: value}
    with pytest.raises(ValueError):
        token_counts(invalid)
    result = report(context(), response(invalid))
    assert result["records"] == 0
    assert result["tokens"]["total_tokens"] is None
    assert result["findings"]["invalid_usage_records"] == 1


def test_missing_breakdowns_stay_unknown_and_block_relevant_prices():
    usage = {"input_tokens": 1000, "output_tokens": 100}
    result = report(context(), response(usage))
    assert result["tokens"]["cached_input_tokens"] is None
    assert result["tokens"]["reasoning_output_tokens"] is None
    assert result["breakdown"] == {"uncached_input_tokens": None, "reasoning_percent_of_total": None}
    assert result["estimated_cost_usd"] is None
    usage["cached_input_tokens"] = 0
    assert report(context(), response(usage))["estimated_cost_usd"] is None
    # Older models have no additional cache-write charge.
    assert Decimal(report(context("gpt-5.4-mini"), response(usage))["estimated_cost_usd"]) == Decimal("0.0012")


def test_request_and_session_long_context_rates():
    short, long = counts(input=272000, cached=0, writes=0), counts(input=272001, cached=0, writes=0)
    result = report(context(), response(short), response(long, response_id="r2"))
    assert [row["cost"]["long_context"] for row in result["items"]] == [False, True]
    assert result["items"][1]["cost"]["rates_per_million"]["output"] == "30.0"
    result = report(context("gpt-5.4"), response(short), response(long, response_id="r2"))
    assert all(row["cost"]["long_context"] for row in result["items"])
    assert result["items"][0]["cost"]["rates_per_million"]["cached_input"] == "0.50"


def test_known_zero_is_distinct_from_missing():
    assert report()["tokens"]["total_tokens"] is None
    usage = counts(input=0, cached=0, output=0, reasoning=0, writes=0)
    result = report(context(), response(usage))
    assert result["tokens"]["total_tokens"] == 0
    assert result["breakdown"] == {"uncached_input_tokens": 0, "reasoning_percent_of_total": None}
    assert Decimal(result["estimated_cost_usd"]) == 0


def test_api_archive_pagination_reimport_and_export(client):
    first = import_rows(client, context(), response())
    base = "/api/v1/sessions/usage-test/usage"
    result = client.get(base).json()
    assert result["archive"]["id"] == first
    assert result["records"] == 1
    assert import_rows(client, context(), response()) == first
    second = import_rows(client, context(), response(), response(response_id="r2"))
    assert second != first
    page = client.get(base + "?limit=1").json()
    assert page["records"] == 2
    assert len(page["items"]) == 1
    assert page["next_cursor"] == page["items"][0]["line_number"]
    tail = client.get(base, params={"import_id": second, "after": page["next_cursor"]}).json()
    assert tail["items"][0]["response_id"] == "r2"
    assert tail["next_cursor"] is None
    assert client.get(base, params={"import_id": first}).json()["records"] == 1
    exported = client.get(base + "/export", params={"import_id": second, "model_override": "gpt-5.6-luna"})
    assert "agentboard-usage.json" in exported.headers["content-disposition"]
    assert len(exported.json()["items"]) == 2
    assert exported.json()["model_override"] == "gpt-5.6-luna"
    assert client.get(base + "?model_override=unpublished").status_code == 422
    assert client.get(base + "?import_id=999999").status_code == 404
    assert client.get("/api/v1/sessions/absent/usage").status_code == 404
    assert client.get("/api/v1/pricing").json() == catalog()


def test_real_excerpt_usage_works_from_existing_raw_archive(client):
    fixture = Path(__file__).parents[2] / "examples/fixtures/codex-real-excerpt.jsonl"
    assert client.post("/api/v1/import/codex", content=fixture.read_bytes()).status_code == 200
    base = "/api/v1/sessions/real-codex-excerpt/usage"
    result = client.get(base).json()
    assert result["records"] == 1
    assert result["tokens"]["total_tokens"] == 26417
    assert result["tokens"]["input_tokens"] == 26239
    assert result["items"][0]["line_number"] == 9
    assert result["estimated_cost_usd"] is None  # Redacted fixture has no model evidence.
    assumed = client.get(base + "?model_override=gpt-5.6-sol").json()
    assert Decimal(assumed["estimated_cost_usd"]) == Decimal("0.108516")
    assert assumed["items"][0]["model"] is None


def test_unarchived_session_is_unavailable(client, imported):
    store = client.app.state.store
    with store.connect() as db:
        db.execute("DELETE FROM raw_imports WHERE session_id=?", (imported,))
    result = client.get(f"/api/v1/sessions/{imported}/usage").json()
    assert result["coverage"] == "unavailable"
    assert result["archive"] is None
    assert result["estimated_cost_usd"] is None
    assert "Import the original" in result["note"]


def test_cache_write_counts_cannot_overlap_cache_reads():
    with pytest.raises(ValueError, match="Cache counts"):
        token_counts(counts(input=1000, cached=900, writes=200))
    assert price(token_counts(counts()), "gpt-5.6-sol")["usd"] == "0.00538"


def test_browser_demo_tracks_synthetic_usage_without_adding_mirrors(client):
    fixture = Path(__file__).parents[2] / "frontend/demo.jsonl"
    imported = client.post("/api/v1/import/codex", content=fixture.read_bytes()).json()
    sid = imported["session_ids"][0]
    result = client.get(f"/api/v1/sessions/{sid}/usage").json()
    assert result["records"] == 3
    assert result["tokens"]["total_tokens"] == 123600
    assert Decimal(result["estimated_cost_usd"]) == Decimal("0.456")
    assert client.get(f"/api/v1/sessions/{sid}").json()["metadata"]["agentboard_fixture"]["kind"] == "synthetic"


def test_leading_unknown_cost_does_not_display_a_zero_subtotal():
    result = report(response(), context(), response(response_id="r2"))
    assert result["items"][0]["cumulative_priced_usd"] is None
    assert result["items"][1]["cumulative_priced_usd"] == "0.00538"


def test_missing_response_identity_does_not_invent_deduplication():
    result = report(context(), response(response_id=None), response())
    assert result["records"] == 1
    assert result["coverage"] == "partial"
    assert result["findings"]["missing_response_ids"] == 1


@pytest.mark.parametrize("model,expected", [("gpt-5.1-codex", "0.002025"),
    ("gpt-5.1-codex-max", "0.002025"), ("gpt-5.1-codex-mini", "0.000405"),
    ("gpt-5.2-codex", "0.002835")])
def test_published_codex_prices(model, expected):
    assert price(token_counts(counts()), model)["usd"] == expected

"""TOML rate-card validation uses synthetic prices; usage tests verify snapshot arithmetic."""

import json
from decimal import Decimal
from textwrap import dedent

import pytest

from agentboard import pricing
from agentboard.usage import analyze


@pytest.fixture
def card():
    return dedent('''\
        # Synthetic rates; decimal strings preserve precision.
        schema_version = 2
        version = "synthetic-v1"
        verified_at = 2026-09-15
        source_url = "https://example.com/prices"
        currency = "USD"
        service_tier = "standard"
        unit = "USD per 1M tokens"

        [aliases]
        "synthetic-alias" = "synthetic-model"

        [models."synthetic-model"]
        input = "2"
        cached_input = "0.2"
        cache_write = "input_rate"
        output = "10"

        [models."synthetic-model".long_context]
        scope = "session"
        threshold_input_tokens = 1000
        input_multiplier = "3"
        output_multiplier = "2"
    ''')


def test_toml_comments_rates_dates_sources_and_aliases(card):
    loaded = pricing.parse_rate_card(card)
    assert loaded.models["synthetic-model"].cached_input == "0.2"
    assert loaded.models["synthetic-model"].cache_write is None
    assert loaded.aliases["synthetic-alias"] == "synthetic-model"
    assert loaded.verified_at == "2026-09-15"
    assert str(loaded.source_url) == "https://example.com/prices"
    assert pricing.parse_rate_card(card.replace('2026-09-15', '"2026-09-15"')).verified_at == "2026-09-15"


@pytest.mark.parametrize("value", ['2', '0.2', 'true', 'null', '"-1"', '"NaN"', '"Infinity"', '"1e2"', '""'])
def test_rates_require_nonnegative_decimal_strings(card, value):
    with pytest.raises(ValueError):
        pricing.parse_rate_card(card.replace('input = "2"', f'input = {value}'))


@pytest.mark.parametrize("old,new", [
    ('schema_version = 2', 'schema_version = 1'),
    ('schema_version = 2', 'schema_version = 3'),
    ('schema_version = 2', 'schema_version = true'),
    ('currency = "USD"', 'currency = "EUR"'),
    ('service_tier = "standard"', 'service_tier = "batch"'),
    ('unit = "USD per 1M tokens"', 'unit = "USD per token"'),
    ('verified_at = 2026-09-15', 'verified_at = "2026-02-30"'),
    ('verified_at = 2026-09-15', 'verified_at = 2026-09-15T12:00:00Z'),
    ('source_url = "https://example.com/prices"', 'source_url = "not-a-url"'),
    ('schema_version = 2', 'schema_version = 2\nunknown = 1'),
    ('input = "2"', 'input = "2"\ncached_inpt = "0.1"'),
    ('cache_write = "input_rate"', ''),
    ('cached_input = "0.2"', ''),
    ('cached_input = "0.2"', 'cached_input = "input_rate"'),
    ('cached_input = "0.2"', 'cached_input = false'),
    ('cache_write = "input_rate"', 'cache_write = "unavailable"'),
    ('scope = "session"', 'scope = "unknown"'),
    ('threshold_input_tokens = 1000', 'threshold_input_tokens = 0'),
    ('threshold_input_tokens = 1000', 'threshold_input_tokens = true'),
    ('input_multiplier = "3"', 'input_multiplier = "0"'),
    ('output_multiplier = "2"', ''),
    ('"synthetic-alias" = "synthetic-model"', '"synthetic-alias" = "missing-model"'),
    ('"synthetic-alias" = "synthetic-model"', '"synthetic-model" = "synthetic-model"'),
    ('"synthetic-alias" = "synthetic-model"', '"synthetic-alias" = "synthetic-alias"'),
    ('"synthetic-alias" = "synthetic-model"', '"" = "synthetic-model"'),
])
def test_invalid_metadata_rules_and_aliases_fail_closed(card, old, new):
    with pytest.raises(ValueError):
        pricing.parse_rate_card(card.replace(old, new))


@pytest.mark.parametrize("suffix", ['input = "200"', '[models."synthetic-model"]'])
def test_duplicate_toml_keys_and_tables_cannot_overwrite_prices(card, suffix):
    text = card.replace('input = "2"', f'input = "2"\n{suffix}')
    with pytest.raises(ValueError):
        pricing.parse_rate_card(text)


@pytest.mark.parametrize("tier", ['', 'long_context = true', 'long_context = "none"', 'long_context = {}'])
def test_context_tier_must_be_explicit_false_or_a_complete_table(card, tier):
    text = card.split('[models."synthetic-model".long_context]')[0] + tier
    with pytest.raises(ValueError):
        pricing.parse_rate_card(text)


def test_json_is_not_accepted_as_a_toml_card():
    with pytest.raises(ValueError):
        pricing.parse_rate_card('{"schema_version": 2}')


def test_model_names_with_dots_are_literal_identifiers(card):
    loaded = pricing.parse_rate_card(card.replace('synthetic-model', 'synthetic.model-v1'))
    assert list(loaded.models) == ['synthetic.model-v1']
    assert loaded.aliases['synthetic-alias'] == 'synthetic.model-v1'


def test_catalog_returns_an_independent_copy():
    expected = pricing.catalog()
    changed = pricing.catalog()
    changed["models"]["gpt-5.6"]["input"] = "9999"
    assert pricing.catalog() == expected
    assert expected["models"]["gpt-5.6"] == expected["models"]["gpt-5.6-sol"]


@pytest.mark.parametrize("input_tokens,expected,long_context", [
    (1000, "0.000289", False),
    (272000, "0.054489", False),
    (272001, "0.1089184", True),
])
def test_auto_review_uses_luna_cache_rates_and_context_tier(input_tokens, expected, long_context):
    models = pricing.catalog()["models"]
    assert models["codex-auto-review"] == models["gpt-5.6-luna"]
    usage = {"input_tokens": input_tokens, "cached_input_tokens": 200,
             "cache_write_input_tokens": 100, "output_tokens": 100}
    cost = pricing.price(usage, "codex-auto-review")
    assert cost == pricing.price(usage, "gpt-5.6-luna")
    assert Decimal(cost["usd"]) == Decimal(expected)
    assert cost["long_context"] is long_context


def test_toml_threshold_and_multipliers_control_session_pricing(card, monkeypatch):
    loaded = pricing.parse_rate_card(card)
    monkeypatch.setattr(pricing, "_RATES", dict(loaded.models))
    rows = [{"type": "token_usage_record", "payload": {
        "model": "synthetic-model", "response_id": str(index),
        "usage": {"input_tokens": count, "cached_input_tokens": 0, "output_tokens": 100},
    }} for index, count in enumerate([1000, 1001])]
    report = analyze(map(json.dumps, rows))
    assert all(row["cost"]["long_context"] for row in report["items"])
    assert report["items"][0]["cost"]["rates_per_million"] == {
        "input": "6", "cached_input": "0.6", "cache_write": "6", "output": "20",
    }
    assert report["estimated_cost_usd"] == "0.016006"
    assert analyze(map(json.dumps, rows[:1]))["items"][0]["cost"]["long_context"] is False


def test_explicit_status_values_are_distinct_from_zero_prices(card, monkeypatch):
    text = card.split('[models."synthetic-model".long_context]')[0] + 'long_context = false\n'
    text = text.replace('input = "2"', 'input = "0"').replace('output = "10"', 'output = "0"')
    text = text.replace('cached_input = "0.2"', 'cached_input = "unavailable"')
    loaded = pricing.parse_rate_card(text)
    monkeypatch.setattr(pricing, "_RATES", dict(loaded.models))
    assert loaded.models['synthetic-model'].long_context is None
    usage = {"input_tokens": 1000, "cached_input_tokens": 0, "cache_write_input_tokens": 100, "output_tokens": 100}
    assert pricing.price(usage, "synthetic-model")["usd"] == "0"
    usage["cached_input_tokens"] = 1
    assert pricing.price(usage, "synthetic-model")["usd"] is None
    loaded = pricing.parse_rate_card(text.replace('cached_input = "unavailable"', 'cached_input = "0"'))
    monkeypatch.setattr(pricing, "_RATES", dict(loaded.models))
    assert pricing.price(usage, "synthetic-model")["usd"] == "0"


def test_input_rate_status_charges_writes_as_ordinary_input(card, monkeypatch):
    text = card.split('[models."synthetic-model".long_context]')[0] + 'long_context = false\n'
    monkeypatch.setattr(pricing, "_RATES", dict(pricing.parse_rate_card(text).models))
    usage = {"input_tokens": 1000, "cached_input_tokens": 200, "cache_write_input_tokens": 100, "output_tokens": 100}
    cost = pricing.price(usage, "synthetic-model")
    assert cost["components_usd"]["cache_write"] == "0.0002"
    assert cost["usd"] == "0.00264"

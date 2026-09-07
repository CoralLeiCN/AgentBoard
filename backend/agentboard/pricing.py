"""Dated Standard API text-token prices. No network requests or billing side effects."""

from decimal import Decimal

VERSION = "openai-standard-2026-09-07"
SOURCE = "https://developers.openai.com/api/docs/pricing"
VERIFIED_AT = "2026-09-07"

# USD per million tokens: input, cached input, output, cache write, long-context scope.
# Exact identifiers only: an unknown model/snapshot must never inherit a guessed price.
_RATES = {
    "gpt-6-astra": ("10", "1", "50", "12.5", "request"),
    "gpt-5.6-sol": ("4", "0.4", "20", "5", "request"),
    "gpt-5.6-terra": ("2", "0.2", "12", "2.5", "request"),
    "gpt-5.6-luna": ("0.2", "0.02", "1.2", "0.25", "request"),
    "gpt-5.5": ("5", "0.5", "30", None, "session"),
    "gpt-5.5-pro": ("30", None, "180", None, "session"),
    "gpt-5.4": ("2.5", "0.25", "15", None, "session"),
    "gpt-5.4-pro": ("30", None, "180", None, "session"),
    "gpt-5.4-mini": ("0.75", "0.075", "4.5", None, None),
    "gpt-5.4-nano": ("0.2", "0.02", "1.25", None, None),
    "gpt-5.3-codex": ("1.75", "0.175", "14", None, None),
    "gpt-5.2-codex": ("1.75", "0.175", "14", None, None),
    "gpt-5.1-codex": ("1.25", "0.125", "10", None, None),
    "gpt-5.1-codex-max": ("1.25", "0.125", "10", None, None),
    "gpt-5.1-codex-mini": ("0.25", "0.025", "2", None, None),
    "gpt-5.2": ("1.75", "0.175", "14", None, None),
    "gpt-5.2-pro": ("21", None, "168", None, None),
    "gpt-5.1": ("1.25", "0.125", "10", None, None),
    "gpt-5": ("1.25", "0.125", "10", None, None),
    "gpt-5-mini": ("0.25", "0.025", "2", None, None),
    "gpt-5-nano": ("0.05", "0.005", "0.4", None, None),
    "gpt-4.1": ("2", "0.5", "8", None, None),
    "gpt-4.1-mini": ("0.4", "0.1", "1.6", None, None),
    "gpt-4.1-nano": ("0.1", "0.025", "0.4", None, None),
    "gpt-4o": ("2.5", "1.25", "10", None, None),
    "gpt-4o-mini": ("0.15", "0.075", "0.6", None, None),
    "o3": ("2", "0.5", "8", None, None),
    "o4-mini": ("1.1", "0.275", "4.4", None, None),
}
# Published alias; intentionally no broad prefix matching.
_RATES["gpt-5.6"] = _RATES["gpt-5.6-sol"]


def catalog():
    return {
        "version": VERSION, "verified_at": VERIFIED_AT, "source_url": SOURCE,
        "currency": "USD", "service_tier": "standard", "unit": "USD per 1M tokens",
        "models": {
            model: dict(zip(
                ("input", "cached_input", "output", "cache_write", "long_context_scope"), rates
            ), long_context_threshold=272000 if rates[4] else None,
                source_url=f"https://developers.openai.com/api/docs/models/{model}"
                if model in ("gpt-5.2-codex", "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini")
                else SOURCE)
            for model, rates in _RATES.items()
        },
    }


def price(usage, model, *, request_known=True, session_long=False):
    """Return decimal USD strings, or an explicit reason why a row is unpriced."""
    if model not in _RATES:
        return {"usd": None, "reason": "Unknown model or unpublished price"}
    input_rate, cached_rate, output_rate, write_rate, long_scope = _RATES[model]
    if long_scope and not request_known:
        return {"usd": None, "reason": "Per-request context size unavailable"}
    cached, writes = usage["cached_input_tokens"], usage["cache_write_input_tokens"]
    if cached is None and cached_rate is not None:
        return {"usd": None, "reason": "Cached-input token count unavailable"}
    if writes is None and write_rate is not None:
        return {"usd": None, "reason": "Cache-write token count unavailable"}
    cached, writes = cached or 0, writes or 0
    if cached and cached_rate is None:
        return {"usd": None, "reason": "Cached-input rate unavailable for this model"}
    long_context = bool(long_scope and (usage["input_tokens"] > 272000 or session_long))
    input_multiplier = Decimal(2 if long_context else 1)
    output_multiplier = Decimal("1.5" if long_context else "1")
    rates = {
        "input": Decimal(input_rate) * input_multiplier,
        "cached_input": Decimal(cached_rate or input_rate) * input_multiplier,
        "cache_write": Decimal(write_rate or input_rate) * input_multiplier,
        "output": Decimal(output_rate) * output_multiplier,
    }
    quantities = {
        "input": usage["input_tokens"] - cached - writes,
        "cached_input": cached, "cache_write": writes, "output": usage["output_tokens"],
    }
    parts = {key: rates[key] * count / Decimal(1000000) for key, count in quantities.items()}
    return {
        "usd": str(sum(parts.values())), "reason": None, "long_context": long_context,
        "rates_per_million": {key: str(value) for key, value in rates.items()},
        "components_usd": {key: str(value) for key, value in parts.items()},
    }

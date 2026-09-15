"""Validated TOML rate card and decimal Standard API token-value calculations."""

import tomllib
from datetime import date
from decimal import Decimal
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

# Strings preserve exact decimal prices; TOML status values normalize to None for calculations.
Rate = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
ModelID = Annotated[str, Field(strict=True, pattern=r"^\S+$")]


class CardObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LongContext(CardObject):
    scope: Literal["request", "session"]
    threshold_input_tokens: Annotated[int, Field(strict=True, gt=0)]
    input_multiplier: Rate
    output_multiplier: Rate

    @field_validator("input_multiplier", "output_multiplier")
    @classmethod
    def positive_multiplier(cls, value):
        if Decimal(value) <= 0:
            raise ValueError("Long-context multipliers must be positive")
        return value


class ModelRates(CardObject):
    input: Rate
    cached_input: Rate | None
    cache_write: Rate | None
    output: Rate
    long_context: LongContext | None
    source_url: HttpUrl | None = None

    @field_validator("cached_input", mode="before")
    @classmethod
    def unavailable_cached_rate(cls, value):
        return None if value == "unavailable" else value

    @field_validator("cache_write", mode="before")
    @classmethod
    def ordinary_write_rate(cls, value):
        return None if value == "input_rate" else value

    @field_validator("long_context", mode="before")
    @classmethod
    def disabled_context_tier(cls, value):
        return None if value is False else value


class RateCard(CardObject):
    schema_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    version: Annotated[str, Field(strict=True, min_length=1)]
    verified_at: Annotated[str, Field(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]
    source_url: HttpUrl
    currency: Literal["USD"]
    service_tier: Literal["standard"]
    unit: Literal["USD per 1M tokens"]
    models: Annotated[dict[ModelID, ModelRates], Field(min_length=1)]
    aliases: dict[ModelID, ModelID]

    @field_validator("verified_at", mode="before")
    @classmethod
    def native_toml_date(cls, value):
        return value.isoformat() if type(value) is date else value

    @field_validator("verified_at")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def valid_aliases(self):
        for alias, target in self.aliases.items():
            if alias in self.models or target not in self.models:
                raise ValueError("Aliases must name a canonical model and cannot shadow models or chain")
        return self


def parse_rate_card(text):
    # tomllib rejects duplicate keys/tables and unsupported values before validation.
    return RateCard.model_validate(tomllib.loads(text))


# Load one validated snapshot per process. Restart after editing the bundled card.
_CARD = parse_rate_card(files("agentboard").joinpath("data/model-pricing.toml").read_text(encoding="utf-8"))
VERSION, SOURCE, VERIFIED_AT = _CARD.version, str(_CARD.source_url), _CARD.verified_at
_RATES = {**_CARD.models, **{alias: _CARD.models[target] for alias, target in _CARD.aliases.items()}}


def catalog():
    # Preserve the existing public API, expanding only explicitly declared aliases.
    return {
        "version": VERSION, "verified_at": VERIFIED_AT, "source_url": SOURCE,
        "currency": _CARD.currency, "service_tier": _CARD.service_tier, "unit": _CARD.unit,
        "models": {
            model: {
                "input": rates.input, "cached_input": rates.cached_input, "output": rates.output,
                "cache_write": rates.cache_write,
                "long_context_scope": rates.long_context.scope if rates.long_context else None,
                "long_context_threshold": rates.long_context.threshold_input_tokens if rates.long_context else None,
                "source_url": str(rates.source_url) if rates.source_url else SOURCE,
            }
            for model, rates in _RATES.items()
        },
    }


def price(usage, model, *, request_known=True, session_long=False):
    """Return decimal USD strings, or an explicit reason why a row is unpriced."""
    if model not in _RATES:
        return {"usd": None, "reason": "Unknown model or unpublished price"}
    model_rates = _RATES[model]
    input_rate, cached_rate = model_rates.input, model_rates.cached_input
    output_rate, write_rate = model_rates.output, model_rates.cache_write
    context = model_rates.long_context
    if context and not request_known:
        return {"usd": None, "reason": "Per-request context size unavailable"}
    cached, writes = usage["cached_input_tokens"], usage["cache_write_input_tokens"]
    if cached is None and cached_rate is not None:
        return {"usd": None, "reason": "Cached-input token count unavailable"}
    if writes is None and write_rate is not None:
        return {"usd": None, "reason": "Cache-write token count unavailable"}
    cached, writes = cached or 0, writes or 0
    if cached and cached_rate is None:
        return {"usd": None, "reason": "Cached-input rate unavailable for this model"}
    long_context = bool(context and (usage["input_tokens"] > context.threshold_input_tokens or session_long))
    input_multiplier = Decimal(context.input_multiplier) if long_context else Decimal(1)
    output_multiplier = Decimal(context.output_multiplier) if long_context else Decimal(1)
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

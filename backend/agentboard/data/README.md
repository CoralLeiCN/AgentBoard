# Model pricing rate card

**Implemented 2026-09-16.** [model-pricing.toml](model-pricing.toml) is the editable pricing data. [pricing.py](../pricing.py) parses it with Python's standard-library `tomllib`, validates it, and calculates decimal USD values. Rates, explicit aliases, long-context rules and explanatory comments live in TOML. This conversion preserves the previous catalog's prices and verification date.

## Format

| Field | Meaning |
| --- | --- |
| `schema_version` | File format version, currently `2` for TOML and explicit status values. |
| `version`, `verified_at`, `source_url` | Pricing snapshot identifier, verification date (TOML date or quoted `YYYY-MM-DD`), and source. Verification is recorded manually; loading the card makes no network request. |
| `currency`, `service_tier`, `unit` | Currently `USD`, `standard`, and `USD per 1M tokens`; other values are rejected. |
| `models` | Exact model IDs mapped to named `input`, `cached_input`, `cache_write`, and `output` rates. Quote model IDs in table headers so dots stay part of the name. |
| Numeric rates | Nonnegative decimal **strings** preserve precision. `"0"` is a known zero price. Bare numbers are rejected. |
| `cached_input = "unavailable"` | Explicitly no published cached-input rate; positive cached usage is unpriced. Normalizes to `null` in the public API. |
| `cache_write = "input_rate"` | Cache writes use the ordinary input rate, including its long-context multiplier. Normalizes to the existing API's `null` (no separate write premium). |
| `long_context = false` | Explicitly no separate long-context tier. Normalizes to `null` internally and in the API's context fields. |
| `long_context` table | Requires `scope` (`request` or `session`), positive integer `threshold_input_tokens`, and positive decimal-string `input_multiplier` / `output_multiplier`. Input must exceed the threshold. The input multiplier also applies to cache reads and writes. |
| Model `source_url` | Optional override of the card's source link. |
| `aliases` | Alias ID → canonical model ID. Aliases cannot chain, shadow models, or refer to missing models. |

For example, this **synthetic** model entry describes prices per million tokens:

```toml
[models."synthetic.model-v1"]
input = "2"
cached_input = "unavailable" # Positive cache reads cannot be priced.
cache_write = "input_rate"  # Writes are charged at the input rate.
output = "10"
long_context = false
```

All four rates and the context declaration are required. Omitting a field is an error, not an unavailable rate. TOML has no `null`: the status values above preserve the existing API meanings while distinguishing unavailable data from known zero. Unknown fields, duplicate keys/tables, malformed rates, invalid status values and incomplete rules fail validation. [Calculation and missing-data semantics](../../../docs/data-lineage.md#71-token-usage-and-api-value).

**Local valuation policy, 2026-09-16:** `codex-auto-review` uses `gpt-5.6-luna` rates through an explicit alias, including cache reads, cache writes and long-context rules. This is a user-configured pricing equivalence. Recorded model names and model filters remain `codex-auto-review`. Card version `openai-standard-2026-09-07-review-luna-v1` identifies the policy change; the underlying rates' verification date remains 2026-09-07.

## Updating

1. Verify the intended Standard API rates and rules against their sources.
2. Edit the TOML entries, update `version`, and retain the source links. Update `verified_at` when published rates are reverified; document local valuation aliases separately. A file-format change uses `schema_version`; a price or alias change uses `version`.
3. Run `uv run --extra dev pytest -m "not e2e" backend/tests/test_pricing.py backend/tests/test_usage.py -q` and the [routine checks](../../../docs/testing.md#routine-checks-before-handoff). Review changes to expected arithmetic when rates intentionally change.
4. Restart AgentBoard. Each process loads one validated snapshot; development auto-reload does not watch TOML-only edits.

The file ships inside the Python package and requires no new dependency (Python 3.11+ is already required). The previous JSON card is removed. `/api/v1/pricing` retains its JSON response shape with aliases expanded. Historical sessions are also recalculated using the loaded card; multiple effective-date schedules and automatic price refresh remain unimplemented. Git retains reviewed revisions of the data file.

## Verification

TOML conversion validation on 2026-09-16: the [routine checks](../../../docs/testing.md#routine-checks-before-handoff) passed with 521 offline backend tests (5 live tests deselected), 44 frontend tests, Ruff and `git diff --check`. The built wheel loads the TOML resource directly and excludes the former JSON card. Catalog output and sample cost calculations matched the preceding card for all 29 model IDs; pricing regressions cover context rules, explicit status values, missing fields, aliases, comments and dates. The synthetic example above also parses and validates.

Luna review-alias validation on 2026-09-16: 525 offline backend tests (5 live tests deselected), 44 frontend tests, Ruff and `git diff --check` passed. Synthetic regressions verify alias rates at the long-context boundary, cache reads/writes, dashboard cost and preserved recorded-model filtering. A separate read-only calculation from local raw archives and TOML rates matched the session and dashboard APIs; private evidence remains in ignored `.agentboard/` storage.

Browser, live-model and Codex schema compatibility checks were not needed because no UI behavior, model execution or app-server integration changed.

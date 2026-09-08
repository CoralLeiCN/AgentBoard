# Configurable tracing features

Implemented 2026-09-08. AgentBoard always serves its dashboard and core session API. One process-wide allowlist selects shipped first-party capabilities for HTTP, CLI and UI. Restart after changing it. [Principles](principles.md) require complete raw capture from every configured source independently of analysis and inspection.

## Feature list

Mandatory core owns configuration, storage/migrations, complete capture, normalization correctness, session/event browsing and search, source selection, normalized inspection, basic timing statistics, health, lifecycle and the bundled UI. `features = []` browses an existing dataset without starting unconfigured collection.

| Feature ID | Capability | Default | Requires |
| --- | --- | --- | --- |
| `import` | Explicit rollout import through HTTP/CLI, including complete capture; demo loading | On | — |
| `otlp_logs` | Complete OTLP/HTTP log capture and normalization at `/v1/logs` | On | — |
| `otlp_traces` | Complete OTLP/HTTP trace capture and normalization at `/v1/traces` | On | — |
| `raw_archive` | Inspect archives, capture outcomes and event evidence | On | — |
| `field_lineage` | Calculate, persist and inspect Codex field mappings | On | — |
| `unified_timeline` | Correlate rollout events with measured item timings | On | — |
| `parallel_groups` | Derive and display overlapping tool groups | On | — |
| `token_usage` | Calculate recorded usage and estimated Standard API value from core archives | On | — |
| `inputs` | Dedicated human-attributed input view and query API | On | — |
| `export` | Download normalized events and supported combined exports | On | — |
| `classification` | Model classification, external label submission and related controls | Off | — |
| `replay` | Edited text continuation through the configured model | Off | `inputs` |
| `native_resume` | Native Codex plans and explicit CLI execution | Off | `inputs` |

Only Codex ships as a rollout adapter. Raw downloads require `raw_archive` plus `export`; usage downloads require `token_usage` plus `export`; dedicated input export requires `inputs` plus `export`. Core queries/normalized exports can still filter `kind=user` without the dedicated input workflow. Disabling input presentation does not change canonical attribution or timing semantics.

## Configuration

`features` is the complete allowlist, replacing defaults. Explicit TOML overrides `AGENTBOARD_FEATURES`, which otherwise overrides built-in defaults. Unknown IDs, duplicate declarations, cycles, missing dependencies and invalid adapter ownership fail before storage opens. Dependencies are not enabled implicitly.

```sh
# Read metadata without opening/migrating storage.
uv run agentboard features
uv run agentboard --config config/dev.toml features
# Existing-data browsing only.
AGENTBOARD_FEATURES= uv run agentboard serve
# Complete imports, without optional evidence inspection or derived analysis.
AGENTBOARD_FEATURES=import uv run agentboard serve
# Compute lineage and usage without raw-inspection APIs.
AGENTBOARD_FEATURES=import,field_lineage,token_usage uv run agentboard serve
```

Equivalent TOML:

```toml
database = "traces.db"
features = ["import"]
```

Start with `uv run agentboard --config /path/to/settings.toml serve`. Database paths in TOML resolve relative to that file. The default tracing set is:

```toml
features = [
  "import", "otlp_logs", "otlp_traces", "raw_archive", "field_lineage",
  "unified_timeline", "parallel_groups", "token_usage", "inputs", "export",
]
```

Add model/continuation features explicitly with their dependencies. Classification/replay use the configured model client; enabling them makes no model request. Native continuation uses Codex's own configuration.

The [dev profile](../config/dev.toml) pins port 4319, a separate database, dummy model mode and tracing without live receivers. It uses fixed fixtures/snapshots; existing exporters stay on 4318. [Development workflow](development.md).

## What disabling changes

Disabled routes are absent from HTTP and OpenAPI and return 404. The UI reads `/api/v1/config`, lists enabled capabilities, hides their unavailable containers/actions, and omits disabled requests. CLI rejects dependent commands, before opening storage when possible.

Complete raw capture never depends on `raw_archive`, `field_lineage`, `token_usage`, or model settings. Unknown content and failed interpretations remain captured. Disabling `raw_archive` hides access without deleting bytes; enabling it reveals retained history immediately. [Capture formats, failure semantics and reprocessing](data-lineage.md#35-raw-archive-and-export).

Disabling `field_lineage` skips detailed mapping computation and new mapping writes. Existing mappings survive, except stale links are removed when canonical values change. Re-enabling does not backfill automatically; explicitly reprocess a retained capture or reimport original files. `token_usage` scans retained data only on demand. Disabled parallel groups skip overlap work inside the unified timeline. Disabled model features do not import or construct the model client. Normalization, input attribution, timestamps, fingerprints and event identity remain core correctness rules.

Features are availability controls, not deletion/redaction policy. Old uncaptured content still needs its original source. Migration never claims to reconstruct missing history.

## First-party module contract

The [static catalog](../backend/agentboard/features/catalog.py) defines frozen IDs, descriptions, dependencies, defaults and lazy router factories. The [adapter catalog](../backend/agentboard/adapters/catalog.py) defines owned parser factories. Metadata listing does not import parsers, open SQLite or initialize optional clients.

Core validates configuration, opens storage and builds enabled services. Feature factories return `APIRouter` values using only their [named service operations](../backend/agentboard/services.py). Core validates method/path ownership and owns middleware, mounts, lifecycle and UI placement. No feature receives a raw Store, database connection, app or runtime. These internal interfaces support trusted first-party code; they are not an isolation boundary or external compatibility promise.

The experimental `plugins` setting and Python `register(registry)` hook have been removed. Delete `plugins` from TOML and unset `AGENTBOARD_PLUGINS`; nonempty legacy environment settings fail explicitly. No package discovery, hot loading or third-party module execution is supported. [Architecture and deferred design](modular-features-plan.md).

## Verification

[Feature tests](../backend/tests/test_features.py) cover independent capabilities, HTTP/OpenAPI boundaries and skipped work; [catalog/CLI tests](../backend/tests/test_feature_registry.py) cover validation, lazy imports, lifecycle, UI IDs and disabled commands. [Capture tests](../backend/tests/test_capture.py) cover byte completeness, failures, replay of retained data and upgrades; [reimport tests](../backend/tests/test_feature_reimports.py) cover evidence across lineage settings. [Frontend tests](../frontend/tests/features.test.cjs) verify selective controls and requests. [Review evidence](architecture-review.md) records browser profiles and measurement limits.

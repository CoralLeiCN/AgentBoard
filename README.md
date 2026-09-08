# AgentBoard

Import Codex sessions and inspect LLM/tool timing in a mandatory browser dashboard. Tracing capabilities are independently configurable; classification and edited continuations are opt-in. One lightweight Python service serves the UI and external clients.

| Reference | Covers |
| --- | --- |
| [Principles](docs/principles.md) | Product intent, complete raw preservation, modularity, performance, and evidence |
| [Configurable features](docs/features.md) | Required core, optional capabilities, defaults, configuration, internal module contract |
| [First-party modular feature plan](docs/modular-features-plan.md) | VS Code/Obsidian lessons, approved architecture target, staged refactor, deferred plugins |
| [Specification](docs/specification.md) | Requirements, status, acceptance criteria |
| [Data lineage](docs/data-lineage.md) | Raw mappings, timing, reimports, evidence limits |
| [Data-quality gaps](docs/data-quality-gaps.md) | Correctness concerns and proposed checks |
| [Backlog](docs/backlog.md) | Future capacity, languages, and integrations |
| [Documentation rules](docs/documentation-guide.md) | Writing and maintenance standards |

## Run locally

Python 3.11+ is required. No Node build, Redis, collector, or model service is required.

```sh
uv sync --extra dev
uv run agentboard --config config/dev.toml serve
```

When `uv` resolves dependencies, it excludes distributions uploaded within the previous seven days.
The checked-in `uv.lock` then pins the selected versions for repeatable setup.

Open [the dev dashboard on port 4319](http://127.0.0.1:4319) and choose **Load demo**. Dev uses a separate database and rejects live OTel uploads. The existing collector remains `uv run agentboard serve` on port 4318. See [isolated development and snapshots](docs/development.md). Codex sessions open in a [unified timeline](docs/unified-timeline.md), with source views available for debugging. For a smaller installation use `uv sync`, or `pip install -e .`; install `.[models]` to enable the OpenAI client. The `dev` extra includes the SDK and HTTP client used by examples.

## Repository layout

The repository follows the backend/frontend boundary of the [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) while retaining AgentBoard's smaller local-first stack.

| Directory | Current contents |
| --- | --- |
| [`backend/`](backend/) | Installable `agentboard` Python package and pytest suite |
| [`frontend/`](frontend/) | Dependency-free HTML, CSS, JavaScript, demo data, and Node test |
| [`config/`](config/) | Runtime configuration, including the isolated dev profile |
| [`docs/`](docs/) | Architecture, behavior, evidence limits, and development workflow |

The root `pyproject.toml` keeps existing `uv run agentboard ...` commands stable. Source checkouts serve `frontend/` directly; wheel builds bundle the same files under `agentboard/static`.

Import real history without changing Codex files:

```sh
uv run agentboard import ~/.codex/sessions
uv run agentboard import ~/.codex/archived_sessions
# Import only the ten most recently modified files across both directories:
uv run agentboard import ~/.codex/sessions ~/.codex/archived_sessions --limit 10
# A single rollout also works:
uv run agentboard import /path/to/rollout.jsonl
```

The CLI streams and commits each file separately, continues after handled failures, and exits nonzero if any fail. Per-file JSONL goes to stdout; the final stderr summary counts processed/succeeded/failed files, unique successful sessions, new rows, and successful files with no new rows.

`--limit N` caps file attempts, including failures and repeated paths, across all arguments. It requires a positive integer and selects newest modification times first; omitted, all files are imported. It does not cap unique sessions or remove existing data. See [batch selection rules](docs/specification.md#33-selecting-a-smaller-batch).

Identical reimports deduplicate; growing files can complete unfinished calls. Zero new rows can still mean updates. Reimport does not generally correct completed records—see [merge rules](docs/data-lineage.md#8-identity-transactions-and-upgrades). Malformed or partially written JSON rejects the whole file; retry after writing completes. Browser uploads default to a 32 MiB limit. HTTP imports accept contents, never server filesystem paths.

The database defaults to `./agentboard.db`. Select another file with `agentboard --database /path/traces.db serve` or `AGENTBOARD_DATABASE`. **Load demo** imports the synthetic session “Fix the checkout total rounding bug” (`demo-codex-checkout-usage-v1`). For actual recorded evidence, explicitly import the [redacted item-timing excerpt](examples/fixtures/codex-real-excerpt.md) or [three redacted input-origin excerpts](examples/fixtures/input-origin-real/README.md). Nothing automatically scans or uploads your history.

## What is implemented

The following capabilities are available when their [feature IDs](docs/features.md#feature-list) are enabled. The default enables tracing features; classification, replay, and native continuation are off.

- Codex rollout import: session metadata, prompts, assistant text and reasoning summaries, function/custom tool calls, token-usage events, turn boundaries, and newer item timing records.
- Human/context/internal input attribution for rollout prompt counts, branch selection, and inferred waiting time; [limitations and evidence](docs/data-lineage.md#341-input-attribution) remain explicit.
- OTLP/HTTP **traces and logs**, accepting JSON, protobuf, and gzip at `/v1/traces` and `/v1/logs`. Decoded span attributes, resource/scope metadata, IDs, parents, links, and span events remain inspectable/exportable; original wire bytes are not retained.
- Browser session search and category filters, timing waterfall, source selection, event inspection, prompt extraction, classification, export, and branching.
- Versioned REST API, cursor-paginated event/input queries, and streaming JSONL exports. [Interactive API documentation](http://127.0.0.1:4318/docs) and `/openapi.json` describe routes and request parameters.
- Optional local-model classification and conversation replay, plus native Codex branch plans and a CLI executor.
- Validated feature allowlists and a static first-party catalog for the HTTP service and CLI. Only the Codex agent adapter ships.

**Token usage & API value:** each session shows recorded input, cached input, cache writes, output and reasoning tokens, plus estimated Standard API token cost in USD. Expand the model breakdown or usage history, select **Price as** for an explicit model assumption, and export the complete usage report as JSON. Published prices are a dated snapshot verified on **2026-09-07**; this is an API-equivalent estimate, not a subscription charge or actual bill. Missing counts/models remain unavailable, with a priced subtotal when possible. Usage summaries that disagree with recorded responses produce a partial-coverage warning. [Calculation rules and coverage](docs/data-lineage.md#71-token-usage-and-api-value).

Existing raw archives work immediately; older sessions without archives need an explicit reimport. Usage reads one archived rollout and refreshes with **↻**; live OTel usage and automatic rollout watching are not implemented. **Load demo** includes synthetic token usage under a new demo session identity.

## Human-attributed inputs

**User inputs** and its count exclude recognized injected context and internal requests after `codex-jsonl-v5` import. Attribution uses complete environment/plugin/AGENTS envelopes and recorded subagent/reviewer metadata; ordinary user messages default to human-attributed. A `user` role alone does not verify human authorship, and unknown wrappers or unmarked automation can still be counted.

Context and internal requests remain under **All events**, with their text, attribution evidence, and raw records. They are excluded from input-only exports and selectable branch inputs, while preceding context remains in conversation replay. Internal sessions display a reviewer/subagent label and a parent link when recorded. Titles use the first nonempty human-attributed prompt.

Existing databases require explicit reimport of the complete original rollout; restarting does not update their counts or waits. For the isolated dev database, use `uv run agentboard --config config/dev.toml import /path/to/rollout.jsonl`. See [exact rules, conflict handling, and coverage](docs/data-lineage.md#341-input-attribution).

## Understand the timing

Historical gaps estimate activity; they do not establish exact full LLM latency.

Click an event to inspect **Normalized**, **Calculated**, **Inferred**, or **Model-generated** values and their derivation. Source timestamps remain Normalized even when their use as LLM boundaries is Inferred. Codex fields expand to exact archive lines and JSON paths, including propagated context and calculated boundaries. Null/empty values retain their known origin; missing evidence is Unknown. Reimport original rollouts to backfill older fields. See the [field-lineage API and limits](docs/data-lineage.md#36-per-field-codex-lineage). [Origin definitions](docs/data-lineage.md#62-origins-and-timing-quality-are-separate-axes) separate provenance from timing quality.

The **Table / Normalized JSON** toggle remembers your choice across events/reloads in this browser. Preserved item/decoded OTel evidence appears separately. **Export raw trace** downloads the latest archived Codex rollout; normalized JSON remains a derived view. Older imports need reimporting to populate the archive. Explanations also work on existing imports.

| Source | Meaning |
| --- | --- |
| `codex_jsonl` | Estimated LLM intervals between known active-turn items and estimated tool call/output intervals. Can include orchestration, scheduling, approvals, and transport. Between-turn gaps are excluded from LLM estimates. Missing ends stay unknown. |
| `codex_item` | Measured lifetimes from newer `item_completed` records. Reported tool durations are used when present. LLM reasoning/message items measure **output streaming**, not request-to-completion latency. |
| `otlp_trace` | Explicit span start/end measurements. GenAI chat/generation and tool operations are recognized; enclosing orchestration spans remain ordinary events. |
| `otlp_log` | Codex API/websocket request and tool-result durations. Request timing is **not automatically full response latency**; SSE event processing times are not counted as LLM response time. |
| `replay` | Wall time of the optional model replay request, including model discovery when needed. Dummy spans are labeled. |

`sum_ms` adds closed durations; `active_ms` unions overlaps within each source/kind/quality group, including parallel tools and nested same-kind spans. Groups are not additive: rollout and telemetry may describe the same operation. The UI prefers measured timing, otherwise estimates; `/stats` exposes all groups. Session elapsed includes gaps and is not the sum of activity times. Distinct request retries remain; identical telemetry retries deduplicate.

**Parallel P1**, **Parallel P2**, etc. label tool calls with overlapping recorded intervals. Group cards show tool count, peak concurrency, and overlapping time; the inspector lists members. Labels use the full selected source and remain consistent across filters/pages, with hidden members indicated. Groups are inferred and do not establish exact simultaneous process execution. Existing imports work without reimport. See [grouping rules and limitations](docs/data-lineage.md#44-parallel-tool-groups), or query `/api/v1/sessions/SESSION_ID/parallel-groups?source=codex_jsonl`.

The **Waiting for user** card and amber spans use `kind=user_wait`. `attributes.wait_type` distinguishes blocking `input_request` calls from inferred `between_turns` gaps. Blocking lifetimes include delivery overhead and are excluded from tool time; rollout pairs are estimated, item/OTLP timing measured. AgentBoard derives between-turn waits from completion (otherwise final answer) to the next human-attributed prompt. These are inferred intervals without a corresponding raw wait record, and may include idle time. Context does not end a pending wait; recognized internal sessions are excluded from rollout human-wait estimates.

Waits union within each source/quality group. Async answers and approvals inside other tools cannot be isolated; unfinished waits have no duration, and the final gap is not extrapolated. **No recorded waits** means no qualifying wait events in the selected source, not proof that nobody waited. The [real desktop sample](examples/fixtures/input-origin-real/README.md#what-the-sample-demonstrates) has one estimated wait of **45m 16.391s**; the one-prompt CLI and internal reviewer samples have none. Query `/events` or `/export` with `kind=user_wait`, or `/stats`. See [exact wait rules and missing-data behavior](docs/data-lineage.md#43-between-turn-waits).

Core captures complete supplied Codex and OTLP payloads before interpretation, even when analysis/inspection is disabled or normalization fails. Every successful Codex JSONL import also archives all source lines, including unknown types, message metadata, multimodal fields, and encrypted content. Session metadata retains every `session_meta.payload` field, including `thread_source`. Distinct source versions are kept; identical content under the same mapping version is deduplicated. Enable `raw_archive` and `export` to use `agentboard export SESSION_ID --raw` or `GET /api/v1/sessions/SESSION_ID/raw`; list versions at `/raw-imports` and select one with `--import-id` or `?import_id=`. See [raw retention semantics](docs/data-lineage.md#35-raw-archive-and-export) for hashes, backfills, and limitations.

Rollout history is treated as an append-only audit trail. Compaction/rollback markers are retained; this is not an exact reconstruction of the current model context. Tool outputs from long-running processes may represent a yielded call, not the entire process lifetime. Unrecognized records are preserved in the raw archive but omitted from normalized events; do not assume complete coverage of every Codex version or multimodal content.

## OpenTelemetry with Codex

Merge [examples/codex-otel.toml](examples/codex-otel.toml) into your Codex configuration. Do not duplicate an existing `[otel]` section. Codex's exporters batch asynchronously, keeping network/storage work away from the coding path. Prompt telemetry stays redacted unless you explicitly enable `otel.log_user_prompt`.

AgentBoard correlates `session.id`, `conversation.id`, `gen_ai.conversation.id`, or `thread.id` with the imported Codex ID. Without one of these attributes it uses the trace ID; uncorrelated logs go to an explicit `unattributed-*` session. AgentBoard does not guess associations from timestamps. Attribute conventions vary by client version; a collector can remap them when necessary.

OTLP metrics and gRPC are outside this first version. Use an OpenTelemetry Collector to translate gRPC, buffer/retry exports, redact fields, or fan out to other backends. A success response means the batch is committed. Invalid batches fail atomically; saturated ingestion/storage returns `503` with `Retry-After`. There is no unacknowledged in-memory ingestion queue.

Implementation references: [Codex telemetry configuration](https://developers.openai.com/codex/config-advanced#observability-and-telemetry), [Codex configuration reference](https://developers.openai.com/codex/config-reference), and [OTLP specification](https://opentelemetry.io/docs/specs/otlp/).

## Classification and edited continuations

The optional model gateway uses the OpenAI client against `http://localhost:30000/v1`, discovers a model at `/models`, and calls `/chat/completions` by default. Set `AGENTBOARD_MODEL_API=responses` to use `/responses` instead. It makes no model requests during tracing/import. When the service cannot be reached, `auto` mode uses a labeled dummy response/keyword classifier. Dummy classification is a test placeholder, not an AI judgment. A reachable server that rejects a request or returns invalid classification JSON is reported as an error, not silently replaced.

```sh
# Select capabilities explicitly; add others from the feature list as needed.
export AGENTBOARD_FEATURES=import,inputs,export,classification,replay,native_resume
AGENTBOARD_MODEL_MODE=local AGENTBOARD_MODEL=my-model uv run agentboard serve
# Or force deterministic model examples:
AGENTBOARD_MODEL_MODE=dummy uv run agentboard serve
```

Classify one session with **Classify purpose**, or use **Classify this page** for unclassified sessions in the current view. Purpose filters cover writing, coding, debugging, research, analysis, creative media, guidance, and other, adapted from the enterprise AI report. Existing `bug-fixing` IDs stay compatible. A bounded conversation transcript is sent only when requested; partial input and dummy results are labeled.

```sh
uv run agentboard classify SESSION_ID
uv run agentboard classify --all --limit 100
# Replace saved labels explicitly:
uv run agentboard classify --all --force
```

An independent agent can fetch `/api/v1/sessions/{id}/classification-input` and submit `{category, reason, model}` to `PUT /api/v1/sessions/{id}/classification`. Classification uses a shared enum-backed Pydantic model and strict Structured Outputs on both supported model APIs. Inspect its JSON Schema at `GET /api/v1/classification-schema`. Edit model instructions in [prompts/](backend/agentboard/prompts/README.md). See [session purpose classification](docs/session-purpose.md) for the contract, model configuration, category definitions, batch behavior, and the external-agent workflow.

**Conversation replay:** in **User inputs**, choose **Branch & edit**. Replay retains the preceding text transcript and tool observations, replaces the selected prompt, discards later context, calls the configured model, and saves a separate linked session. It does not run tools, execute Codex, restore files, reproduce hidden model state, or replay images. Oversized contexts and histories containing compaction/rollback are rejected rather than silently truncated. The original session is unchanged.

**Native Codex:** the same dialog can download an app-server plan. To actually continue the coding agent, use an imported session that also exists in your local Codex installation:

```sh
uv run agentboard export SESSION_ID --inputs-only
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Explain the fix instead' --cwd /path/to/repo
# Execute the reviewed plan in a NEW read-only Codex branch:
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Explain the fix instead' --cwd /path/to/repo --execute
```

The CLI uses `thread/fork` with the preceding completed `lastTurnId`, then `turn/start` with the replacement. Replacing the first input uses `thread/start` with no retained history. Inputs that steer an existing turn or lack known prior turn boundaries cannot be represented exactly by a turn-level fork; use transcript replay for those. Execution stays connected until the turn finishes, defaults to read-only with no interactive approvals, and times out after 120 seconds. Codex handles model/auth configuration itself. **Files are not rewound.** Native execution is not exposed through HTTP. Import the resulting Codex rollout to inspect its new traces. See the [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server).

## API examples

```sh
curl -X POST --data-binary @examples/fixtures/codex-session.jsonl \
  http://127.0.0.1:4318/api/v1/import/codex
curl 'http://127.0.0.1:4318/api/v1/sessions?limit=20&q=checkout'
curl 'http://127.0.0.1:4318/api/v1/sessions/demo-codex-checkout/stats?source=codex_jsonl'
curl 'http://127.0.0.1:4318/api/v1/sessions/demo-codex-checkout/inputs?q=test'
curl http://127.0.0.1:4318/api/v1/sessions/demo-codex-checkout/export > events.jsonl
```

Event/input pages return `items` and `next_cursor`; pass the cursor as `after` until null. Sessions use `limit`, `offset`, and `next_offset`. Ingest order, rollout `sequence`, and temporal `start_time` order differ. Refresh older pages to retrieve completed-call updates. Exports contain normalized events; fetch session metadata/classification separately. Concurrent exports are not frozen snapshots.

Domain, SQLite, and API timestamps are UTC RFC 3339 strings with nine fractional digits: `start_time`, nullable `end_time`, and session `started_at`, e.g. `2026-09-06T08:51:24.032000000Z`. Retained raw objects keep their source format, including OTel epoch nanoseconds.

**Database upgrades:** stop all running AgentBoard processes before starting the updated version. Startup migrates supported databases to schema v9, preserving event IDs, pagination row IDs, metadata, and classifications. It converts old integer timestamps to RFC 3339 text and adds the current archive, identity, lineage, and reimport-fingerprint tables. Schema v9 adds independent capture tables without inventing old capture history. The earlier v8 migration hashes Codex lines for conflict detection; neither migration renormalizes events. Timestamp conversion needs no reimport; raw archives for existing sessions require the original rollouts to be reimported. The normalized API/export fields `start_ns`, `end_ns`, and `started_ns` are replaced by `start_time`, `end_time`, and `started_at`; update external consumers and custom adapters. Older binaries cannot use a migrated database. Refresh the browser after restarting.

The [timestamp contract](docs/data-lineage.md#61-timestamp-contract) specifies offset normalization, supported range/precision, rejected values, and exact temporary nanosecond arithmetic. Duration metrics remain milliseconds.

## Configuration

See the [feature list](docs/features.md) for the mandatory core, all optional IDs, dependencies, and internal module contract. An explicit allowlist replaces defaults. `AGENTBOARD_FEATURES=` keeps the dashboard and core browsing API while disabling imports, telemetry receivers, and optional analysis. `uv run agentboard features` lists resolved capabilities without opening the database.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `AGENTBOARD_DATABASE` | `agentboard.db` | SQLite file |
| `AGENTBOARD_FEATURES` | [Tracing feature set](docs/features.md#configuration) | Complete comma-separated allowlist; empty retains only the mandatory core |
| `AGENTBOARD_MODEL_MODE` | `auto` | `auto`, `local` (fail when unavailable), or `dummy` |
| `AGENTBOARD_MODEL_BASE_URL` | `http://localhost:30000/v1` | OpenAI-compatible endpoint |
| `AGENTBOARD_MODEL` | unset | Model ID; discovers first advertised model otherwise |
| `AGENTBOARD_MODEL_API` | `chat_completions` | `chat_completions` or `responses` wire protocol |
| `AGENTBOARD_MODEL_TIMEOUT_SECONDS` | `30` | Positive request timeout; increase for slower local models |
| `AGENTBOARD_MODEL_KEY` | `local` | Credential for that endpoint |
| `AGENTBOARD_API_TOKEN` | unset | Optional shared bearer token |
| `AGENTBOARD_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1,testserver` | Comma-separated HTTP Host allowlist; configure your hostname for remote use |

The app factory also accepts `Settings` for the body limit, ingestion concurrency (4), and model context budget. A [static first-party catalog](docs/features.md#first-party-module-contract) supplies feature routers and owned adapters. Names and dependencies are validated before storage opens. Disabled routes are absent, UI controls are hidden, and dependent CLI operations fail clearly. Remove the old `plugins` TOML key and unset `AGENTBOARD_PLUGINS`; dynamic module loading is no longer supported.

## Efficiency and deployment boundaries

The [architecture review](docs/architecture-review.md) records the implemented changes, fixture measurements, verification and remaining performance gaps.

SQLite WAL, indexed queries, batched transactions, streaming file import/export, bounded HTTP bodies, and a bounded ingest concurrency keep the local runtime small. Core captures original payloads with SQLite `FULL` commits before normalization; derived writes retain `NORMAL` semantics. A worker thread handles each import/model request so the HTTP event loop stays responsive. There are no background polling loops and no agent-side monkey patches. The static frontend adds no build/runtime dependency. Persistent IDs deduplicate retried OTLP spans and imports.

This version is a **single shared workspace**, suitable for local use and a modest private service. SQLite still has one writer; no claim is made that it is a high-volume multi-tenant SaaS backend. Large file transactions can occupy that writer, leading to retryable backpressure. JSON search and per-session timing aggregation scan the relevant data. Future service deployments can retain HTTP/OTLP while replacing `Store` (e.g. PostgreSQL/columnar storage) or adding a collector/durable queue. These are optional candidates, subject to [workload review](docs/backlog.md#wl-001--efficient-high-volume-tracing-service). [Architecture notes](docs/architecture.md) explain the boundaries.

The CLI binds to loopback. Binding elsewhere requires `AGENTBOARD_API_TOKEN`; configure the allowed hostname and a TLS reverse proxy. Authentication protects the shared dataset, not per-user tenants. Stored prompts, code, and tool outputs are plaintext in the database; choose the database location/access policy accordingly. There is no automatic retention/deletion or secret redaction. A configured model receives transcript text only when classification/replay is explicitly invoked. The browser loads no third-party assets; FastAPI's optional Swagger/ReDoc pages use their default CDN assets.

## Examples and validation

[examples/README.md](examples/README.md) maps each requirement to a runnable demo. Start the service with the capabilities listed in that catalog, then run `uv run python examples/01_import.py` through `09_external_classification.py`. Model examples require explicit feature enablement and fall back to a dummy model when your local service is unavailable.

The repository keeps generated Codex app-server schemas with a producing-version/hash manifest. After upgrading Codex or changing the integration, run `python scripts/codex_schemas.py check`; use `update` to regenerate a reviewable baseline. Offline `verify` runs in pytest. See the [schema upgrade workflow](schemas/README.md) for executable selection, drift review, and the distinction from raw rollout files.

```sh
uv run --extra dev pytest -q
node --test frontend/tests/provenance.test.cjs
uv run --extra dev ruff check backend examples scripts
uv run python examples/benchmark.py --events 10000
```

Tests cover imports, parallel timing, missing/invalid events, reimports, OTLP encodings and hierarchy, pagination, exports, classification/model failures, context branching, configuration, and plugin loading. The native Codex branch protocol is tested with a fake app-server; a real paid Codex generation is not required to run the test suite.

Live Codex and classification tests use only the private model endpoint **`http://192.168.1.220:30000/v1`**, with no hosted-provider fallback. They are opt-in; normal tests need no model calls. The Codex test uses an isolated home, receiver and database. See [private model tests](docs/development.md#private-model-tests) for model discovery, optional credentials, and isolation details.

```sh
uv run --extra dev pytest --run-private-e2e -m e2e -q
```

The service must implement streaming Responses for Codex and structured Responses for classification. An unavailable endpoint fails the live test. [`example.env`](example.env) documents optional model pins; otherwise a single advertised model is discovered automatically.

For real local development data, [seed a shared baseline and checkpoint](docs/development.md#automatic-worktree-data-setup) from complete selected Codex rollouts. Each worktree snapshots its own writable database. All source lines remain archived in the local database; real data and backups stay ignored and must never be committed or uploaded. [AGENTS.md](AGENTS.md) gives agents the required setup and test policy.

For existing OTel data affected by numeric worker IDs appearing as sessions, back up the database and run `uv run agentboard repair-otlp-sessions`. The repair preserves recorded spans and event IDs, associates spans using unambiguous conversation evidence, and leaves unresolved activity in trace buckets. See [OTLP correlation rules](docs/data-lineage.md#9-live-telemetry-mappings). Timeline now displays internal timed spans as well as LLM/tool/wait operations.

The **Sessions** view counts observed/imported conversation identities. **Unattributed telemetry** holds background traces with no unambiguous conversation association. The API defaults to sessions; use `/api/v1/sessions?identity_kind=unattributed` or `identity_kind=all` to include these records. See the [grouping review](docs/session-grouping-review.md).

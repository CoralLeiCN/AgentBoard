# AgentBoard product specification

Updated: 2026-09-06

Agreed requirements, implementation status, and acceptance criteria for AgentBoard. Codex compatibility is limited to supported source variants.

[Data lineage](data-lineage.md) defines current mappings; [data-quality gaps](data-quality-gaps.md) track correctness concerns separately from deferred capabilities. Implemented does not imply complete coverage or reproducible evaluation.

Status meanings:

- **Implemented:** present in the current source, subject to the stated limitations.
- **Workaround available:** usable today through an existing workflow, without a dedicated product control.
- **Discussed follow-up:** identified desired behavior that has not been implemented.
- **Outside current scope:** no implementation or delivery commitment in this version.

## 1. Purpose and scope

AgentBoard explains prompts, tool use, timing, uncertainty, and edited continuations. Its primary workflow is importing Codex sessions/traces to analyze LLM and tool time. Keep additional features optional so tracing stays lightweight.

The dashboard and external clients share one HTTP API over local session data. Codex is the first agent; initial deployment is one shared workspace on a developer machine or modest private service.

Core requirements:

| ID | Requirement | Status |
| --- | --- | --- |
| RUN-01 | Start the API, dashboard, and telemetry receiver as one service. | Implemented |
| IMP-01 | Import Codex rollout files and directories, including archived history. | Implemented |
| IMP-02 | Reimport safely, handling duplicates and growing sessions. | Implemented |
| IMP-03 | Limit an import to a chosen recent subset. | Workaround available |
| IMP-04 | Report per-file outcomes and an end-of-run summary. | Implemented |
| IMP-05 | Preserve raw evidence and handle Codex rollout format changes explicitly. | Accepted policy; partially implemented (§3.5) |
| TEL-01 | Receive live OTLP/HTTP logs and traces from Codex. | Implemented |
| UI-01 | Browse, search, filter, and inspect sessions and their events. | Implemented |
| TIME-01 | Separate elapsed, LLM, tool, and waiting-for-user time. | Implemented, with inference limitations |
| TIME-02 | Explicitly label parallel tool groups from recorded overlap. | Implemented; inferred membership (§6.2) |
| INPUT-01 | Extract prompts and distinguish their actual origin. | Extraction implemented; origin distinction is a discussed follow-up |
| PROV-01 | Distinguish normalized, calculated, inferred, and model-generated field values. | Implemented |
| VIEW-01 | Switch between table and normalized JSON, remembering the last view. | Implemented |
| API-01 | Expose session data, timing, input extraction, and export to external tools. | Implemented |
| MODEL-01 | Classify work using an optional model or external analysis worker. | Implemented |
| BRANCH-01 | Explore an edited input through transcript replay or a native Codex branch. | Implemented, with distinct semantics |
| EXT-01 | Keep optional features and future agent integrations modular. | Implemented extension boundary; only Codex ships |
| EXAMPLE-01 | Maintain an `examples/` folder with a demo for each initial use case, using the local model or a labeled dummy fallback where a model is needed. | Implemented examples; native execution requires a real local session |

## 2. Local operation and configuration

**RUN-01 — Implemented**

- Require Python 3.11 or newer.
- Serve the dashboard and API from the same process and origin.
- Default to `127.0.0.1:4318` and a SQLite database at `./agentboard.db`.
- Do not require a frontend build, Redis, collector, or model server for tracing.
- Do not make model calls during import or telemetry ingestion.
- Provide synthetic demo data and interactive API documentation.

Start from the repository root:

```sh
uv sync
uv run agentboard serve
```

Open [the dashboard](http://127.0.0.1:4318), select a session, and choose a data source. API documentation is available at `/docs` and `/openapi.json`; `/health` reports service health.

Use the same database for the server and CLI imports. Select it with the global CLI option or environment variable:

```sh
uv run agentboard --database /path/to/agentboard.db serve
uv run agentboard --database /path/to/agentboard.db import /path/to/rollout.jsonl
```

Keep the server running for UI access/live telemetry. Refresh manually for new data or static UI changes; restart after backend changes.

## 3. Historical imports

### 3.1 Supported input and normalization

**IMP-01 — Implemented**

Accept one or more file or directory arguments. Recursively discover `.jsonl` files in directory arguments. The browser supports selecting multiple files; each uploaded file is a separate import request.

```sh
uv run agentboard import ~/.codex/sessions
uv run agentboard import ~/.codex/archived_sessions
uv run agentboard import /path/to/rollout.jsonl
```

Normalize session metadata, user-role messages, assistant messages, reasoning summaries, function/custom tool calls and outputs, token-usage records, lifecycle boundaries, and supported item-completion timing records. Preserve compaction and rollback markers. Unrecognized records may be skipped.

Handle recorded commands that are strings, argument arrays, empty arrays, or null. Argument arrays must become readable, shell-quoted text without losing argument boundaries. Preserve the original item object in event attributes. Other structured command values are serialized as JSON. Importing a command must never execute it.

Acceptance: a measured item containing `['/bin/zsh', '-lc', 'echo "two words"']` imports successfully, retains the original array, and exposes a string in the normalized `text` field.

### 3.2 Reimports and failures

**IMP-02 — Implemented**

- Stream CLI input and commit once per file.
- Deduplicate stable event identities on repeated imports.
- Complete previously unfinished events when their results become available.
- Preserve session classification on ordinary reimport.
- Reject a malformed file atomically; do not leave partially imported events from that file.
- Continue processing subsequent files after handled file/parse failures and exit nonzero when any fail.
- Allow retry after Codex finishes a partially written final line.
- Do not treat reimport as arbitrary synchronization of edited or rewritten history.

`inserted_events: 0` means no new event rows were added. It does not mean the file was ignored or that existing unfinished rows could not have been updated.

HTTP/browser imports are bounded to 32 MiB by default. CLI streaming supports larger files. The HTTP API accepts file contents, not server filesystem paths.

### 3.3 Selecting a smaller batch

**IMP-03 — Workaround available**

The user can import the ten most recently modified rollout files with zsh:

```sh
uv run agentboard import ~/.codex/sessions/**/*.jsonl(.om[1,10])
```

- The number limits selected files, not the database’s total session count.
- Multiple files can refer to the same session, so ten files need not mean ten unique sessions.
- Ordering is by file modification time, not session creation time.
- Existing database contents remain in place.
- This expression is zsh-specific; do not escape the tilde or wildcards when entering it in zsh.

No native `import --limit` or `--latest` option exists.

### 3.4 Import summary

**IMP-04 — Implemented**

Keep per-file JSONL outcomes on stdout. After those records, emit a readable summary on stderr so stdout remains machine-readable.

```text
Import summary:
  Files: 10 processed, 10 succeeded, 0 failed
  Unique sessions: 9
  New events: 11
  Files with no new events: 9
```

Count files attempted, files successfully committed, failures, distinct successfully imported session IDs, inserted event rows, and successful files with zero inserted rows. Count repeated file arguments as attempts, while deduplicating session IDs in the session total. Failed transactions contribute no committed sessions or new events.

Acceptance: provide correct totals for empty directories, duplicate files, multiple files sharing a session, unchanged reimports, and mixed success/failure. Print the summary before the nonzero exit for handled failures.

### 3.5 Codex rollout compatibility policy

**IMP-05 — Accepted 2026-09-06; partially implemented**

Treat persisted Codex rollouts as a version-dependent format. Do not assume a stable upstream file contract. The [official app-server schemas](https://learn.chatgpt.com/docs/app-server#message-schema) describe version-specific API messages; they do not establish the schema or compatibility of imported rollout files.

| Principle | Current behavior and remaining work |
| --- | --- |
| Preserve original data completely. | Successful Codex JSONL imports archive all supplied lines and fields independently of normalized events. Exact retention, export, failure, and backfill rules are in [lineage §3.5](data-lineage.md#35-raw-archive-and-export). |
| Record the producing Codex version and our mapping version. | Source `session_meta.payload.cli_version` is retained when supplied; absence remains unknown. Archives record `mapping_version`. Verified Codex event and field links identify their archive/version; see [field lineage](data-lineage.md#36-per-field-codex-lineage) for backfill and source limits. Changing normalization semantics must advance the mapping version and document existing-data treatment. |
| Normalize only through documented, evidence-backed mappings. | [Lineage §3.3](data-lineage.md#33-record-to-event-mapping) describes current mappings and their assumptions. Preserve unknown data without inventing meaning; existing heuristic correctness gaps remain listed in the [gap register](data-quality-gaps.md). |
| Preserve unfamiliar fields and report unsupported records. | Preservation is implemented. Reports distinguishing mapped, intentionally ignored, unsupported, and rejected records are still required (DQ-03); absence from normalized events must not be interpreted as absent activity. |
| Separate structural validation from interpretation. | JSON/payload/timestamp checks are partial (DQ-14). Valid structure or `role: "user"` does not establish human authorship. Human/context/reviewer classification remains unimplemented (INPUT-01/DQ-01). |

For a new supported variant, add a synthetic or redacted fixture, document source fields and transformations, and verify raw round-trip preservation and intended normalized behavior. Record the producing version when known, the mapping version, and effects on existing imports. These are change-review requirements, not claims of compatibility with every Codex release.

**App-server baseline — Implemented:** the repository retains generated API schemas and a CLI-version/hash manifest. The [schema workflow](../schemas/README.md) provides offline integrity verification, installed-CLI drift checks, and explicit regeneration for Git review. Regular tests validate the stored baseline without Codex. This does not validate raw rollout files or establish semantic compatibility of every API method.

## 4. Live Codex telemetry

**TEL-01 — Implemented**

Receive OTLP/HTTP at `/v1/logs` and `/v1/traces`, supporting JSON, binary protobuf, and gzip. Preserve decoded record/resource/scope data, identifiers, and hierarchy where provided. Normalize recognized operations into the shared event contract.

Merge the following into the user-level Codex configuration, normally `~/.codex/config.toml`, without duplicating existing tables:

```toml
[otel]
environment = "local"
log_user_prompt = false

[otel.exporter.otlp-http]
endpoint = "http://127.0.0.1:4318/v1/logs"
protocol = "binary"

[otel.trace_exporter.otlp-http]
endpoint = "http://127.0.0.1:4318/v1/traces"
protocol = "binary"
```

The maintained project example is [codex-otel.toml](../examples/codex-otel.toml). The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) documents the user-level telemetry settings; the [official telemetry guide](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry) explains asynchronous export and prompt redaction.

Use a fresh/restarted Codex process after changing exporter configuration. Loopback endpoints refer to the machine running Codex; remote sessions need a receiver reachable from that machine. When AgentBoard requires a token, configure the matching bearer header on both exporters.

Correlate sessions using supported session/conversation/thread attributes. Fall back to a trace ID or explicit unattributed identity when needed; do not guess associations from similar timestamps. Keep rollout and telemetry sources separate even when they refer to the same operation.

Live export does not replace historical import for inferred gaps between turns. Prompt redaction in telemetry does not redact prompts contained in explicitly imported rollout files.

## 5. Dashboard and event browsing

**UI-01 — Implemented**

Provide a session list with search, category filtering, pagination, refresh, import, and demo controls. Session details include source selection, event filtering, pagination, and these views:

| View | Required behavior |
| --- | --- |
| Timeline | Show loaded LLM, tool, user-wait, and other recorded intervals on a relative timeline with duration and timing quality. Internal spans use neutral styling and do not add to operation totals. |
| User inputs | Show events currently classified as user inputs, with branching controls when enabled. Subject to INPUT-01 below. |
| All events | Include events outside the timeline categories and allow detail inspection. |

The timeline covers loaded events; `events?timeline=true` selects LLM, tool, user-wait, and other events with recorded ends before pagination so unrelated events cannot fill an empty timeline page. Stats cover the full selected source, including unloaded/search-excluded events. Empty timelines distinguish sources with no recorded intervals or open operations from search filters with no matches and point to All events. Disable unavailable sources. Clicking a row opens event/span details, not a whole-turn aggregation.

## 6. Timing semantics

**TIME-01 — Implemented with stated limitations**

Show five metrics: **Session elapsed**, **LLM time**, **Tool time**, **Waiting for user**, and **User inputs**.

| Source | Meaning and limitation |
| --- | --- |
| `codex_jsonl` | LLM activity inferred between eligible active-turn records; tool durations estimated by pairing calls and outputs. Neither is guaranteed to equal model inference or process execution time. |
| `codex_item` | Measured item lifetimes or reported tool durations. Reasoning/message item time measures output streaming, not full request latency. |
| `otlp_trace` | Explicit span timestamps, with recognized operations mapped to event categories. |
| `otlp_log` | Reported API/websocket/tool durations. Transport duration is not necessarily full response latency; SSE processing is not counted as LLM response time. |
| `replay` | Locally measured replay request duration, including model discovery when needed; dummy responses remain labeled. |

For every source/kind/timing-quality group, expose:

- `sum_ms`: sum of closed interval durations.
- `active_ms`: duration of the union of those intervals, merging overlaps.
- Span count and the quality label: `measured`, `estimated`, or `unknown`.

Do not add duplicate sources or quality groups together. Within the selected source, the UI prefers measured timing when available and otherwise shows estimates. All groups remain available through `/stats`.

Session elapsed is the distance between the earliest and latest recorded event bounds for the selected source, including gaps. It is not a sum of LLM, tool, and waiting time, and it is not a live clock extending to the present.

### 6.1 Waiting for user input

Use `kind: "user_wait"` and amber timeline styling.

| `attributes.wait_type` | Definition | Quality |
| --- | --- | --- |
| `input_request` | Lifetime of a recognized blocking `request_user_input` call until its result. Exclude this operation from tool time. | Estimated for rollout call/output pairs; measured when suitable item or telemetry timing exists. |
| `between_turns` | Gap from turn completion, or a final answer when completion is absent, to the next user prompt. | Inferred/estimated; may include idle time. |

Requirements and limits:

- Merge overlapping waits within the same source/quality group.
- Keep missing ends unknown; show an unfinished wait without inventing a duration.
- Do not extrapolate waiting time after the last completed turn.
- Do not classify asynchronous question tool lifetimes as blocking waits.
- Do not infer waiting solely from an interruption/rollback marker.
- Do not claim to isolate approval waits embedded in ordinary tools or asynchronous questions.
- Recognized blocking-call durations may include delivery and scheduling overhead.
- Reimport older rollouts to populate new wait spans and reclassify previously imported blocking requests.
- Preserve blocking input-request text/results for replay context.

Acceptance: the synthetic demo has one inferred between-turn wait of **31,900 ms**. Overlapping blocking requests lasting 3 s and 4 s over a shared 5 s interval yield `sum_ms = 7000` and `active_ms = 5000`.

Internal reviewer sessions can still produce misleading user-wait estimates because input origin is not yet distinguished; see INPUT-01.

### 6.2 Parallel tool groups

**TIME-02 — Implemented**

Label grouped tool rows “Parallel P1”, “Parallel P2”, etc. in Timeline and All events. Show tool count, peak concurrent intervals, overlapping time, and partial membership when a filter/page hides tools. The inspector identifies membership as Inferred and exposes the member IDs. Source/quality/turn/trace/parent boundaries and exact arithmetic are defined in [lineage §4.4](data-lineage.md#44-parallel-tool-groups).

Compute groups from the full selected source through `/parallel-groups`, independently of the loaded event page/search. Existing imports work without backfill; source events and timing totals remain unchanged. Labels describe recorded overlap, with no claim of exact process concurrency or an explicit model batch. Missing parent relationships remain a limitation.

Acceptance: the synthetic demo labels its two initial shell calls as one group, peak 2, with 1,100 ms overlapping time. Filtering to one member keeps its label and shows “1/2 tools shown”. Three calls connected by successive overlaps can form one group with peak 2. Touching, open, zero-length, cross-source, cross-turn, or known parent/child intervals do not create false groups. [Automated coverage](../backend/tests/test_parallel.py).

## 7. User inputs and internal context

**INPUT-01 — Partially implemented; origin distinction is a discussed follow-up**

Implemented behavior:

- Extract user-role messages and event-message fallbacks.
- Deduplicate mirrored representations while retaining repeated real prompts.
- Join adjacent prompt fragments without inserting new characters.
- Provide input counts, filtering, export, and previous-turn information for branching.

Known defect: the current importer treats injected `role: "user"` environment context and internal reviewer requests as user inputs. This can affect session titles, counts, branching controls, and inferred waiting time. The session metadata may already contain subagent source and parent identifiers, but the UI does not use them to distinguish these input origins.

Discussed desired behavior, **not yet implemented**:

1. Distinguish human-authored prompts, injected environment/context messages, and internal agent/reviewer requests.
2. Keep environment/context records inspectable under All events while excluding them from human input counts and human prompt branching controls.
3. Identify internal reviewer/subagent sessions and their relationship to the parent session rather than presenting their messages as ordinary human prompts.
4. Avoid attributing internal scheduling or review activity to waiting for a human.
5. Retain the original evidence and avoid discarding a real prompt merely because it contains quoted markup.

Acceptance scenario: a guardian session whose first user-role record is `<environment_context>…</environment_context>` must not label that record as the first human prompt or offer **Branch & edit** on it. The classification mechanism and migration/backfill approach remain to be designed.

## 8. Event provenance and inspector views

### 8.1 Field origins

**PROV-01 — Implemented**

Explain each built-in event field and attribute with a value, an origin label, and a description of how it was obtained.

| Origin | Meaning |
| --- | --- |
| Normalized | A value extracted, selected, propagated, decoded, or reformatted from source data. |
| Calculated | A value computed from other values, such as elapsed duration, a duration-derived start, a stable hash, or a sequence counter. |
| Inferred | Internal application rules or interpretation: event categories, display labels, timing-quality labels, default statuses, and explanations. Does not necessarily mean uncertain. |
| Model-generated | Content or classifications produced by an ML model, LLM, or agent during an AgentBoard model operation. Dummy placeholders and keyword rules are Inferred. |
| Unavailable | No usable stored value for the field. |
| Unknown | No supported mapping description; do not assume it is raw data. |

Keep origin independent of timing quality (`measured`, `estimated`, `unknown`). Source timestamps remain **Normalized** when selected as inferred boundaries; end-minus-duration starts are **Calculated**. Arithmetic does not establish measured latency; `status: "ok"` is not confidence.

Acceptance: the [2,838 ms worked example](data-lineage.md#64-worked-historical-example) has Normalized endpoints, Calculated duration/hash, and Inferred LLM interpretation, display name, quality, default status, basis, and empty placeholder.

These labels describe how AgentBoard obtains a field. An assistant response imported from a Codex transcript is **Normalized** because it is extracted from source text; that is separate from who originally authored it. New replay responses and classification results produced by an actual model are **Model-generated**; retained/replacement replay text is **Normalized**. Older replay output without model/dummy origin evidence remains **Unknown**.

Display preserved Codex item or decoded telemetry data separately when available. Label the canonical event representation **Normalized JSON**. Never imply it is the complete original rollout record or original protobuf bytes.

Codex Table fields use the persisted backend [field-lineage contract](data-lineage.md#36-per-field-codex-lineage), including exact archived records and context/calculation sources. Reimport original files to backfill matching legacy fields; missing evidence stays Unknown. Null/empty stored values retain their known origin. Other-source descriptions remain display-time mappings without verified archive links. Expand large values and render all content as text without executing markup.

### 8.2 View selection and persistence

**VIEW-01 — Implemented**

- Place a **Table / Normalized JSON / Raw JSONL** toggle in the event inspector.
- Show one primary representation at a time.
- Table view presents values, origin labels, and explanations.
- JSON view presents the complete normalized event as formatted JSON.
- Raw JSONL shows actual event records with exact archived text, line numbers, and archive identity. Records used only to calculate duration are excluded; inferred intervals explicitly have no raw event record. Multiple records and unavailable mappings are explicit. See [source-link semantics](data-lineage.md#35-raw-archive-and-export).
- Keep the timing explanation and available source evidence accessible in every view.
- Indicate the selected control visually and accessibly; support keyboard activation.
- Default to Table when no valid saved choice exists.
- Remember the last selection across events and page reloads using browser local storage, key `agentboard-event-view`, with values `table`, `json`, or `raw`.
- Scope persistence to the browser/origin; do not imply account synchronization or sharing across browsers, hosts, or ports.
- If storage is unavailable, preserve the choice in memory for the current page without breaking inspection.

Acceptance: select JSON, close the inspector, reload the dashboard, and open a different event. JSON remains selected. Selecting Table must update the preference in the same way.

## 9. Shared API and data contract

**API-01 — Implemented**

Session records include identity, agent, title, start time, metadata, and optional classification. Events include identity/order, session/turn/span relationships, kind/name, timestamps, timing quality, source, status, text, and extensible attributes.

Supported event kinds are `user`, `assistant`, `llm`, `tool`, `user_wait`, and `event`. Canonical timestamps use [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.html#section-5.6) strings in the domain model, SQLite TEXT columns, API, and normalized exports. Use `start_time`, nullable `end_time`, and session `started_at`, normalized to UTC `Z` with exactly nine fractional digits. Fixed timezone and precision preserve chronological string ordering and nanosecond precision. Integers may be used transiently for source conversion and duration arithmetic. Duration metrics remain milliseconds. Raw retained source objects keep their original timestamp fields and formats.

Accept known numeric timezone offsets and normalize them to UTC. Support instants on/after the Unix epoch with up to nine fractional digits; reject leap seconds, unknown `-00:00` offsets, and finer precision without silently rounding.

Schema v1 timestamps migrate to text, schema v3 adds raw archive tables, schema v4 adds OTLP identity indexes, and schema v5 distinguishes session identities from unattributed telemetry, and schema v6 adds event source links, and schema v7 adds per-field Codex lineage on startup in one transaction under a write lock, preserving IDs, pagination cursors, metadata, and classifications. Stop all older AgentBoard processes before upgrading. Failures roll back the migration. Existing normalized API/export timestamp keys `start_ns`, `end_ns`, and `started_ns` are replaced, so external consumers and custom adapters must adopt the new names. Timestamp conversion needs no reimport; backfilling raw evidence requires reimporting original rollouts. Refresh the UI after restart. Older binaries cannot use schema v7. Existing OTLP session associations require the explicit repair described in [lineage §9](data-lineage.md#9-live-telemetry-mappings). The database row ID, original sequence, and temporal order have different meanings.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/config` | Enabled features, adapters, model mode, and import body limit. |
| `GET /api/v1/sessions` | Search/filter observed sessions with limit/offset pagination. `identity_kind=unattributed` exposes telemetry groups; `all` includes both. |
| `GET /api/v1/sessions/{sid}` | Session metadata and classification. |
| `GET /api/v1/sessions/{sid}/events` | Filter by source, kind, and text; paginate with a cursor. |
| `GET /api/v1/sessions/{sid}/parallel-groups` | Derived tool-overlap groups for the full session or selected source. |
| `GET /api/v1/sessions/{sid}/raw-imports` | List complete Codex source archives with hashes and mapping versions. |
| `GET /api/v1/sessions/{sid}/events/{event_id}/raw` | Get verified source lines and archive identity, or an explicit unavailable reason. |
| `GET /api/v1/sessions/{sid}/raw` | Export exact archived JSONL; optional `import_id` selects a version. |
| `GET /api/v1/sessions/{sid}/inputs` | Query events currently classified as user inputs. |
| `GET /api/v1/sessions/{sid}/stats` | Counts, bounds, and timing groups, optionally by source. |
| `GET /api/v1/sessions/{sid}/export` | Stream normalized JSONL, optionally filtered by kind. |
| `POST /api/v1/import/codex` | Import rollout contents. |
| `POST /v1/logs`, `POST /v1/traces` | Receive telemetry. |
| `POST /api/v1/sessions/{sid}/classify` | Run optional model classification. |
| `PUT /api/v1/sessions/{sid}/classification` | Save external classification. |
| `POST /api/v1/sessions/{sid}/replay` | Create a transcript replay from an edited input. |
| `POST /api/v1/sessions/{sid}/codex-plan` | Create a native continuation plan without executing it. |

Event/input pages expose `items` and `next_cursor`; pass the cursor as `after`. Session pages use `next_offset`. Updating an older event does not automatically resend it through an already advanced cursor. Refresh to retrieve updated records.

Exports contain normalized events, not a complete database/session backup. Session metadata and classification must be fetched separately. Concurrent export is not a transactionally frozen snapshot.

## 10. Classification and edited continuations

### 10.1 Classification

**MODEL-01 — Implemented**

Support `writing`, `coding`, `bug-fixing`, `research`, and `other`, with a reason, model identity, and explicit dummy/fallback labeling. Allow classification in the UI/API or by an independent worker that reads an export and writes a validated result.

An independent model or coding-agent session must fetch trace/input data and submit labels through the frontend’s backend API, without direct database access.

The optional gateway uses an OpenAI-compatible model endpoint, defaulting to `http://localhost:30000/v1`. A configured model ID is optional; otherwise discover a model. Support `auto`, `local`, and `dummy` modes. In auto mode an unavailable service can use a labeled dummy fallback; a reachable service returning invalid output or rejecting a request must surface an error.

Use Python’s OpenAI client: `client.models.list()` discovers at `http://localhost:30000/v1/models`; `client.chat.completions.create()` generates responses. Examples/model tests should exercise an available service and allow a labeled dummy when unavailable, so unrelated development/testing can continue.

Bound classification context to 60,000 characters by default and report truncation. Treat imported transcript content as data rather than instructions to the classifier.

### 10.2 Transcript replay

**BRANCH-01 — Implemented**

From **Branch & edit**, retain prior text and tool observations, replace the selected prompt, omit later context, call the configured model, and create a linked replay session without altering the original. Label dummy responses. Reject replay histories that cannot be represented safely within the text budget or contain unsupported compaction/rollback.

Transcript replay does not execute tools, reconstruct hidden model state, restore historical files, or replay images. It is a text continuation.

### 10.3 Native Codex continuation

Provide a downloadable plan and explicit CLI execution:

```sh
uv run agentboard export SESSION_ID --inputs-only
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Replacement input' --cwd /path/to/repo
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Replacement input' --cwd /path/to/repo --execute
```

Use the preceding completed turn as the fork boundary; replacing the first input starts a new session. Reject input positions that cannot be represented by a turn-level fork. Native execution uses Codex’s own authentication, defaults to read-only/no interactive approvals, waits for turn completion, and has a 120-second timeout.

Do not execute native continuation through HTTP. Do not rewind repository files. Import the resulting rollout to inspect the continuation.

## 11. Optional features and operational limits

**EXT-01 — Implemented boundary**

Support the operator-controlled [configuration variables and defaults](../README.md#configuration-and-plugins), covering database, optional features, model mode/endpoint/ID/key, API token, allowed hosts, and trusted plugins. Settings also control request size, ingestion concurrency, and model context budget.

Disabled optional features must hide their UI controls and reject their API use. Plugins may register routes or parsing adapters through the documented registration boundary. Only the Codex adapter is included; future agents are extension opportunities, not current integrations.

Keep storage behind the Store interface. Use SQLite WAL, indexed reads, per-file/batch transactions, and bounded HTTP ingestion. Default to four concurrent ingestion requests per process; return retryable `503` responses on capacity/storage contention. A successful ingestion response follows the transaction commit, subject to SQLite `synchronous=NORMAL` durability semantics.

Require a token before the CLI binds outside loopback. For remote operation configure the host allowlist and TLS termination. Authentication protects a shared dataset; it is not tenant isolation. Stored prompts/code/tool output are plaintext, and no automatic retention or secret-redaction policy is provided.

Keep tracing overhead away from agent execution by using explicit file imports or the existing asynchronous telemetry exporter. Do not claim measured performance improvements or service-scale throughput from the synthetic benchmark.

### 11.1 Backend portability and future agent integrations

Use Python now. Keep HTTP/OTLP, canonical event semantics, storage, and adapters separable so measured needs can justify migrating some/all components later. Any replacement must validate API compatibility and data migration while preserving one integration path for frontend/external clients. No rewrite, language, or alternate backend is selected.

Future candidates are **OpenCode**, **Pi Agent**, and **Claude Code**. Only Codex ships; add future integrations through configured adapters/plugins without mandatory base dependencies. Track both options in the [backlog](backlog.md).

## 12. Verification and maintenance

Maintain the specification alongside feature changes. Validate behavior at the relevant boundary:

| Area | Acceptance coverage |
| --- | --- |
| Imports | String/array commands, mirrored/repeated prompts, growing files, atomic failure, duplicate identities. |
| CLI summary | Unique sessions versus file counts, zero new events, mixed failures, stdout JSONL, stderr summary, exit status. |
| Timing | Parallel interval union, separated sources/qualities, unknown ends, bounded waits, async exclusion. |
| Telemetry | JSON/protobuf/gzip, exact IDs/timestamps, hierarchy, correlation, malformed batches, backpressure. |
| Inspector | Example 2,838 ms span: normalized endpoints, calculated duration/hash, inferred labels; model vs dummy output, retained source objects, unknown mappings, safe text rendering. |
| View preference | Toggle both ways, reopen another event, reload, default behavior, unavailable storage. |
| Model features | Validated categories, labeled dummy results, request failures, bounded transcripts, preserved source session. |
| Native continuation | Plan validation and fake app-server protocol tests; no paid model execution required for unit tests. |
| Real Codex endpoint | Opt-in environment-configured Responses API provider; isolated AgentBoard receiver/database; successful response plus log, trace, and normalized LLM-event assertions. Skips before execution when unconfigured. |
| App-server schema workflow | Stored manifest/file/reference integrity; version and file drift detection; no-op refreshes; removed-file cleanup; preservation on failed generation/replacement. [Tests](../backend/tests/test_schema_workflow.py) use a fake CLI. |
| INPUT-01 follow-up | Environment context/internal requests excluded from human input/branch/wait semantics once implemented. |

For development dependencies and existing checks:

```sh
uv sync --extra dev
uv run --extra dev pytest -q
node --test frontend/tests/provenance.test.cjs
uv run --extra dev ruff check backend examples scripts
```

Use browser checks for visuals/persistence and Node for JS tests/syntax; Node is not an app runtime dependency. Acceptance criteria are not claims of complete automated coverage.

**E2E-01 — Implemented, opt-in:** `AGENTBOARD_E2E_CODEX_BASE_URL` and `AGENTBOARD_E2E_CODEX_MODEL` select a real Responses-compatible provider for [one end-to-end test](../backend/tests/test_codex_endpoint_e2e.py). `AGENTBOARD_E2E_CODEX_API_KEY` optionally supplies its bearer credential without exposing the value in process arguments. The harness uses a temporary Codex home, disables OpenAI authentication and retries for the custom provider, and runs one short prompt. It creates a loopback receiver and temporary database rather than reading user Codex state or using either configured development or live AgentBoard data. Default tests make no external request; missing required variables skip, while partial configuration fails.

Implementation references: [CLI](../backend/agentboard/cli.py), [Codex adapter](../backend/agentboard/adapters/codex.py), [telemetry](../backend/agentboard/otlp.py), [storage](../backend/agentboard/store.py), [API](../backend/agentboard/api.py), [field-origin descriptions](../frontend/provenance.js), [UI](../frontend/app.js), [models](../backend/agentboard/models.py), and [native resume](../backend/agentboard/resume.py). See also [architecture](architecture.md) and [runnable examples](../examples/README.md).

### 12.1 Required examples

**EXAMPLE-01 — Implemented examples, with the execution limits noted below**

Maintain the repository’s `examples/` folder and a documented demonstration of each initial use case. Network examples must consume the public backend API used by the frontend. Use synthetic data so ordinary examples do not require importing private sessions or running real repository commands.

The [runnable example catalog](../examples/README.md) maps every required use case to a script or walkthrough: import; LLM/tool/parallel timing; OTLP; external trace analysis; model classification; edited transcript replay; input extraction; native Codex plan; independent classifier; UI inspection; plugins/tracing-only mode; and a storage benchmark. Keep that catalog current. Input extraction retains the section 7 limitation; the benchmark does not certify service capacity.

Install the development extra for example/test dependencies. Model examples use section 10.1’s OpenAI client, endpoint/discovery, and `auto` fallback rules. Keep unavailable-service dummy results explicit and reachable-service errors visible. Non-model examples must work without a model service.

Acceptance: every row has a runnable script or an explicit UI/CLI walkthrough. A missing local model must not prevent import, timing analysis, telemetry, export, or UI development. Native Codex execution is separate from dummy transcript replay and requires an actual locally stored Codex session.

## 13. Open work and scope boundaries

| Item | Current position |
| --- | --- |
| Human/context/internal-request distinction | Discussed follow-up; current classifications can mislead counts and waiting time. |
| Dedicated import count option | Not implemented; zsh file selection is the documented workaround. |
| Automatic rollout watcher/import hooks | Not implemented; imports are explicit. |
| Dashboard live refresh | Not implemented; manually refresh after ingestion. |
| Exact human think time, embedded approvals, async question waits | Not reliably established from the currently supported records. |
| Full raw rollout retention and per-field source-line lineage | Raw Codex JSONL versions, hashes, and physical lines are implemented for new imports/reimports; verified event archive and actual event-record links are implemented; complete per-field lineage remains open. See [archive semantics](data-lineage.md#35-raw-archive-and-export). |
| OTLP metrics receiver or gRPC receiver | Outside current scope; use a collector for protocol translation when needed. |
| Other coding-agent adapters | Outside the shipped integration set; extension boundary exists. |
| Multi-tenant SaaS, distributed storage, durable job queue | Outside current scope. |
| Automatic file rollback, deterministic model replay, multimodal reconstruction | Outside current replay behavior. |

Listing an open item records the discussion or current boundary; it does not assign a delivery date or authorize an expansion of scope.

High-volume service support, possible backend language migration, and the named future agent integrations are tracked in the separate [backlog and wishlist](backlog.md). High-volume capacity is a future-review item, not an initial-release acceptance requirement, as clarified by the user during the initial requirements review.

## 14. Initial requirements coverage review

Traceability to initial requirements. **Documented** means represented with limitations, not necessarily implemented.

| Initial requirement | Specification coverage | Review outcome |
| --- | --- | --- |
| Lightweight tracing application with a minimal initial implementation | Sections 1, 2, and 11. | Documented. |
| Initial Codex support with an extensible agent architecture | Sections 3 and 11.1. | Documented; Codex is the only shipped agent adapter. |
| OpenTelemetry support | Section 4. | Documented as OTLP/HTTP logs and traces; current protocol/signal limits are explicit. |
| Ingest historical Codex sessions as the main workflow | Sections 1 and 3. | Documented as the primary workflow. |
| Frontend trace visualization and analysis | Sections 5, 6, and 8. | Documented. |
| Fetch trace data for external systems | Section 9. | Documented. |
| Unified API shared by the frontend and external systems | Sections 1, 9, and 12.1. | Documented. |
| Minimal impact on coding-agent performance | Sections 2 and 11. | Documented as an architectural requirement; no measured overhead guarantee is claimed. |
| Low backend resource consumption for local use | Sections 2 and 11. | Documented through the lightweight runtime and bounded ingestion/storage design. |
| Efficient handling of a potentially large trace volume as a service | Sections 1, 11, and 13; [WL-001](backlog.md#wl-001--efficient-high-volume-tracing-service). | User clarified that this belongs in a separate backlog/wishlist for future review. Existing initial-release scope is unchanged. |
| Python backend now; possible migration of some or all components later | Sections 2 and 11.1. | Python now; future migration conditional on measured needs. |
| Plugins and user configuration for optional features | Section 11. | Documented. |
| Analyze time spent on LLM responses and tool calls | Section 6. | Documented, including measured/estimated distinctions and overlapping intervals. |
| Classify writing, coding, bug-fixing, and similar sessions using another AI model or coding session | Section 10.1. | Documented, including external coding-session classification. |
| Resume from the middle and change user input | Sections 10.2 and 10.3. | Documented through transcript replay and native continuation at supported turn boundaries; exact replay and file restoration are not claimed. |
| Easily filter and extract all user inputs | Sections 7 and 9. | Documented; human/context/internal-input distinction remains an implementation gap. |
| Repository `examples/` folder with a demo for every initial use case | Section 12.1. | Explicit requirement with a runnable demo mapping. |
| Use the OpenAI client with the local model service and its models endpoint | Sections 10.1 and 12.1. | Python OpenAI client and exact models endpoint specified. |
| Continue development/testing with a dummy model if the local service is unavailable | Sections 10.1 and 12.1. | Documented explicitly for examples and model-feature development. |
| Keep OpenCode, Pi Agent, and Claude Code in mind without implementing them now | Section 11.1. | Three named candidates; future integrations only. |

### 14.1 Scope clarification resolved

On **2026-09-06**, the user moved high-volume service support to [WL-001](backlog.md#wl-001--efficient-high-volume-tracing-service) for later review. Initial scope remains local/modest private service, excluding multi-tenant SaaS, distributed storage, and durable queues. No throughput, retention-volume, concurrency, latency, or resource targets are agreed.

Scalability remains a desired outcome; high volume alone does not require multi-tenancy, distributed infrastructure, or a language rewrite. The synthetic benchmark does not establish production capacity.

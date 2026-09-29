# AgentBoard product specification

Updated: 2026-09-26 (supervised classifiers, curation maintenance and documentation consolidation)

Agreed requirements, implementation status, and acceptance criteria for AgentBoard, guided by the [product and design principles](principles.md). Codex compatibility is limited to supported source variants.

[Data lineage](data-lineage.md) defines current mappings; [data-quality gaps](data-quality-gaps.md) track correctness concerns separately from deferred capabilities. Implemented does not imply complete coverage or reproducible evaluation.

Status meanings:

- **Implemented:** present in the current source, subject to the stated limitations.
- **Workaround available:** usable today through an existing workflow, without a dedicated product control.
- **Accepted design, not implemented:** selected requirements and architecture with implementation and verification still pending.
- **Discussed follow-up:** identified desired behavior that has not been implemented.
- **Outside current scope:** no implementation or delivery commitment in this version.

## 1. Purpose and scope

AgentBoard explains prompts, tool use, timing, uncertainty, and edited continuations. Its primary workflow is importing Codex sessions/traces to analyze LLM and tool time. Keep the dashboard, core event browsing, and complete raw capture mandatory. Configure ingestion sources explicitly; analysis, inspection, export, and continuation capabilities are optional. The [feature catalog](features.md) defines current IDs, defaults, dependencies, and disable behavior; §1.1 records capture and performance acceptance.

The dashboard and external clients share one HTTP API over local session data. Codex is the first agent; initial deployment is one shared workspace on a developer machine or modest private service.

Core requirements:

| ID | Requirement | Status |
| --- | --- | --- |
| RUN-01 | Start the API, mandatory dashboard, and enabled telemetry receivers as one service. | Implemented |
| IMP-01 | Import Codex rollout files and directories, including archived history. | Implemented |
| IMP-02 | Reimport safely, handling duplicates and growing sessions. | Implemented |
| IMP-03 | Limit an import to a chosen recent subset. | Implemented |
| IMP-04 | Report per-file outcomes and an end-of-run summary. | Implemented |
| IMP-05 | Preserve raw evidence and handle Codex rollout format changes explicitly. | Accepted policy; partially implemented (§3.5) |
| RAW-01 | Always preserve complete raw data from configured ingestion sources, independently of optional features, for future inference and calculation. | Implemented for configured Codex/OTLP sources; limits in §1.1 |
| PERF-01 | Build a high-performance service while prototyping in Python, preserving complete raw data. | Structural improvements and fixture measurements implemented; representative loads/budgets remain open (§1.1) |
| TEL-01 | Receive live OTLP/HTTP logs and traces from Codex. | Implemented |
| UI-01 | Browse, search, filter, and inspect sessions and their events. | Implemented |
| TIME-01 | Separate elapsed, LLM, tool, and waiting-for-user time. | Implemented, with inference limitations |
| TIME-02 | Explicitly label parallel tool groups from recorded overlap. | Implemented; inferred membership (§6.2) |
| USAGE-02 | Summarize all sessions over a selected UTC period, with metadata/tag filters, tokens and estimated API value. | Implemented; [dashboard semantics and tests](usage-dashboard.md) |
| USAGE-01 | Track recorded token usage and estimate its Standard API value in USD. | Implemented for archived Codex rollouts; evidence and pricing limits in [lineage §7.1](data-lineage.md#71-token-usage-and-api-value) |
| INPUT-01 | Extract prompts and distinguish their actual origin. | Implemented with inferred attribution and coverage limits (§7) |
| PROV-01 | Distinguish normalized, calculated, inferred, and model-generated field values. | Implemented |
| VIEW-01 | Switch between table and normalized JSON, remembering the last view. | Implemented |
| API-01 | Expose session data, timing, input extraction, and export to external tools. | Implemented |
| MODEL-01 | Classify work using an optional model or external analysis worker. | Implemented |
| BRANCH-01 | Explore an edited input through transcript replay or a native Codex branch. | Implemented, with distinct semantics |
| EXT-01 | Keep tracing capabilities and future agent integrations independently configurable. | Implemented static first-party catalog and narrow services; only Codex ships |
| EXAMPLE-01 | Maintain an `examples/` folder with a demo for each initial use case, using the local model or a labeled dummy fallback where a model is needed. | Implemented examples; native execution requires a real local session |
| EXP-01–14 | Preserve experiment evidence outside checkouts, track partial coverage, and regenerate reports from pinned inputs; optional sharing and external dataset storage follow separately. | Filesystem recorder/coverage implemented; one-time migration complete; shared mode remains planned (§15) |
| CLS-01–08 | Prepare, train and compare BERT-like and embedding/LightGBM classifiers with saved GPT predictions in the independent `cronjob/classifier/` project. | Pipeline implemented; reference-label selection and real-data execution pending (§16) |

### 1.1 Raw capture and performance acceptance

**RAW-01 implemented; PERF-01 partially verified — 2026-09-08.** The [principles](principles.md) own the product intent. Acceptance requires:

| Requirement | Acceptance evidence |
| --- | --- |
| RAW-01: complete capture | Verify reconstruction of complete source payloads captured before lossy decoding/normalization, including unknown records/fields and source/encoding metadata needed for reprocessing. |
| RAW-01: feature independence | HTTP and CLI retain identical complete source evidence with optional analysis and inspection disabled. Keep source/receiver selection explicit, including disabled live receivers in the isolated dev profile. |
| RAW-01: failures and future processing | Verify that interpretation failures preserve captured evidence, incomplete capture is reported explicitly, and retained payloads support later reparsing. Old uncaptured data still requires the original source; automatic backfill is not implemented. |
| PERF-01: measured performance | Record ingestion throughput, query/tail latency, CPU, memory, and disk costs against repeatable workloads with complete raw capture; agree and verify numerical budgets. |
| PERF-01: bounded operation | Verify explicit backpressure/failure under capacity pressure, with no successful acknowledgement of silently incomplete capture. Measure optional analysis overhead separately. |

**Evidence and limits:** [capture tests](../backend/tests/test_capture.py) verify complete bytes, unknown fields, feature independence, interpretation failure, explicit retries and schema v9 upgrades. Original capture uses SQLite `FULL` commits; canonical/outcome writes use `NORMAL`. Admission failures do not claim capture; upstream data never supplied cannot be recovered. [Exact semantics](data-lineage.md#35-raw-archive-and-export).

[Architecture measurements](architecture.md#measurements) cover fixed synthetic import retries with concurrent session-list reads, CPU, memory and storage. They do not establish production throughput, large-dataset tail latency, export cost or model-analysis load. Numerical budgets remain unagreed. These are PERF-01 verification gaps; larger deployment scope remains in [WL-001](backlog.md#wl-001--efficient-high-volume-tracing-service).

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

- Snapshot CLI input in bounded chunks; commit original capture before a separate per-file normalization transaction.
- Deduplicate stable event identities on repeated imports.
- Complete previously unfinished events when their results become available.
- Preserve session classification on ordinary reimport.
- Reject malformed normalization atomically; retain the complete captured file and report its capture ID without leaving partial canonical events.
- Continue processing subsequent files after handled file/parse failures and exit nonzero when any fail.
- Allow retry after Codex finishes a partially written final line.
- Do not treat reimport as arbitrary synchronization of edited or rewritten history.

`inserted_events: 0` means no new event rows were added. It does not mean the file was ignored or that existing unfinished rows could not have been updated.

HTTP/browser imports are bounded to 32 MiB by default. CLI streaming supports larger files. The HTTP API accepts file contents, not server filesystem paths.

### 3.3 Selecting a smaller batch

**IMP-03 — Implemented 2026-09-07**

Select at most ten files across all supplied files and recursively scanned directories:

```sh
uv run agentboard import ~/.codex/sessions ~/.codex/archived_sessions --limit 10
```

- `--limit N` requires a positive integer. Omission retains the existing unlimited import order.
- With a limit, order all candidates by modification time (nanoseconds), newest first; break ties by ascending absolute path. Paths whose modification time cannot be read sort last and retain normal per-file import/error handling if selected.
- The cap applies globally to file attempts, including failures and repeated paths from duplicate or overlapping arguments. Failed imports do not cause replacement files to be selected.
- Multiple files can refer to the same session, so ten files need not mean ten unique sessions. Ordering does not use session creation time or skip previously imported files.
- Existing database contents remain in place. Reimports retain section 3.2 semantics; no migration or backfill is required.

The [CLI](../backend/agentboard/cli.py) scans candidate paths and modification times before a limited import, retains only the selected batch in memory, and streams each selected file. [Synthetic CLI tests](../backend/tests/test_cli_import.py) cover global selection, ordering/ties, validation, repeated files, failures, reimports, and empty/undersized batches. Browser/API imports are unchanged.

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
| Preserve original data completely. | Core captures complete bytes before interpretation, independent of inspection settings, including unknown records and failed normalization. Retention, export, failure, and backfill rules are in [lineage §3.5](data-lineage.md#35-raw-archive-and-export). |
| Record the producing Codex version and our mapping version. | Source `session_meta.payload.cli_version` is retained when supplied; absence remains unknown. Archives record `mapping_version`. Verified Codex event and field links identify their archive/version; see [field lineage](data-lineage.md#36-per-field-codex-lineage) for backfill and source limits. Changing normalization semantics must advance the mapping version and document existing-data treatment. |
| Normalize only through documented, evidence-backed mappings. | [Lineage §3.3](data-lineage.md#33-record-to-event-mapping) describes current mappings and their assumptions. Preserve unknown data without inventing meaning; existing heuristic correctness gaps remain listed in the [gap register](data-quality-gaps.md). |
| Preserve unfamiliar fields and report unsupported records. | Core capture preserves unfamiliar bytes even if normalization fails. Reports distinguishing mapped, intentionally ignored, unsupported, and rejected records are still required (DQ-03); absence from normalized events must not be interpreted as absent activity. |
| Separate structural validation from interpretation. | JSON/payload/timestamp checks are partial (DQ-14). Valid structure or `role: "user"` does not establish human authorship. Rollout input attribution uses explicit source metadata and context envelopes; it remains inferred (INPUT-01/DQ-01). |

For a new supported variant, add a synthetic or redacted fixture, document source fields and transformations, and verify raw round-trip preservation and intended normalized behavior. Record the producing version when known, the mapping version, and effects on existing imports. These are change-review requirements, not claims of compatibility with every Codex release.

**App-server baseline — Implemented:** the repository retains generated API schemas and a CLI-version/hash manifest. The [schema workflow](../schemas/README.md) provides offline integrity verification, installed-CLI drift checks, and explicit regeneration for Git review. Regular tests validate the stored baseline without Codex. This does not validate raw rollout files or establish semantic compatibility of every API method.

## 4. Live Codex telemetry

**TEL-01 — Implemented**

Receive OTLP/HTTP at `/v1/logs` and `/v1/traces`, supporting JSON, binary protobuf, and gzip. Capture complete wire payloads before decoding, and preserve decoded record/resource/scope data, identifiers, and hierarchy where provided. Normalize recognized operations into the shared event contract.

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
| `between_turns` | Gap from eligible turn completion, or a final answer when completion is absent, to the next human-attributed rollout prompt. | Inferred/estimated; may include idle time. |

Requirements and limits:

- Merge overlapping waits within the same source/quality group.
- Keep missing ends unknown; show an unfinished wait without inventing a duration.
- Interpret **No recorded waits** as absence of qualifying events in the selected source, not proof of zero human waiting. A between-turn estimate has boundary evidence but no corresponding raw wait record; see [lineage §4.3](data-lineage.md#43-between-turn-waits).
- Do not extrapolate waiting time after the last completed turn.
- Do not classify asynchronous question tool lifetimes as blocking waits.
- Do not infer waiting solely from an interruption/rollback marker.
- Do not claim to isolate approval waits embedded in ordinary tools or asynchronous questions.
- Recognized blocking-call durations may include delivery and scheduling overhead.
- Reimport older rollouts to populate new wait spans and reclassify previously imported blocking requests.
- Preserve blocking input-request text/results for replay context.

Acceptance: the synthetic demo has one inferred between-turn wait of **31,900 ms**. Overlapping blocking requests lasting 3 s and 4 s over a shared 5 s interval yield `sum_ms = 7000` and `active_ms = 5000`.

Real-source acceptance: the redacted desktop excerpt retains one **2,716,391 ms** between-turn wait; the one-prompt CLI and internal reviewer excerpts retain none. These are source-derived regression cases, not explicit measurements of human waiting. [Selection, redaction, and expected results](../examples/fixtures/input-origin-real/README.md); [automated checks](../backend/tests/test_real_input_origins.py).

Recognized internal reviewer sessions are excluded from rollout human-wait estimates after v5 import. Unmarked automation, unknown origin formats, and older imports remain uncertain; see INPUT-01.

### 6.2 Parallel tool groups

**TIME-02 — Implemented**

Label grouped tool rows “Parallel P1”, “Parallel P2”, etc. in Timeline and All events. Show tool count, peak concurrent intervals, overlapping time, and partial membership when a filter/page hides tools. The inspector identifies membership as Inferred and exposes the member IDs. Source/quality/turn/trace/parent boundaries and exact arithmetic are defined in [lineage §4.4](data-lineage.md#44-parallel-tool-groups).

Compute groups from the full selected source through `/parallel-groups`, independently of the loaded event page/search. Existing imports work without backfill; source events and timing totals remain unchanged. Labels describe recorded overlap, with no claim of exact process concurrency or an explicit model batch. Missing parent relationships remain a limitation.

Acceptance: the synthetic demo labels its two initial shell calls as one group, peak 2, with 1,100 ms overlapping time. Filtering to one member keeps its label and shows “1/2 tools shown”. Three calls connected by successive overlaps can form one group with peak 2. Touching, open, zero-length, cross-source, cross-turn, or known parent/child intervals do not create false groups. [Automated coverage](../backend/tests/test_parallel.py).

## 7. User inputs and internal context

**INPUT-01 — Implemented 2026-09-07, with inference limits**

- Attribute rollout inputs as human, injected context, or internal; retain the observed role, matching evidence, rule version, text, and raw records separately.
- Keep context/internal records under All events and full export. Exclude them from input counts, input-only export, and selectable replay/native branch inputs. Retain them in the transcript preceding a selected human input.
- Derive titles from human-attributed prompts. Label recorded reviewer/subagent sessions and link their parent when supplied.
- Context envelopes do not consume a pending wait or move an LLM anchor. Internal activity cancels pending human waits. Internal/context-only turns do not seed another human wait; recognized blocking requests in that scope remain inspectable timed events outside human-wait/tool totals.
- Deduplicate mirrors within each origin, including context interleaved between prompt representations, while retaining repeated response-item prompts.

Exact rules and reimport behavior are in [lineage §3.4.1](data-lineage.md#341-input-attribution). Human authorship is inferred, not verified. Unknown envelopes, automation without explicit internal evidence, and a person submitting an entire unquoted context envelope remain ambiguous. OTLP and older replay authorship are unchanged; new replay retains input attribution across branches.

[Regression coverage](../backend/tests/test_input_origin.py) checks the guardian acceptance case, quoted markup, fragmented/multimodal input, both mirror orders, waits, raw evidence, branch rejection, retained replay context, and legacy/growing/conflicting reimports. Existing databases require explicit reimport; startup does not backfill them.

[Real-source regression evidence](../examples/fixtures/input-origin-real/README.md) adds selected, redacted CLI/Desktop/guardian records with preserved timing gaps and old/new importer results. It confirms these patterns occur in actual rollouts; it does not measure representative classifier accuracy.

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

Session records include identity, agent, nullable producer, title, start time, metadata, and optional classification. Implemented 2026-09-11: `producer="agentboard"` sessions remain inspectable but are excluded from purpose classification, including forced batches and direct/external API calls. The UI labels and filters them. [Producer mappings and verification](data-lineage.md#321-session-producer). Events include identity/order, session/turn/span relationships, kind/name, timestamps, timing quality, source, status, text, and extensible attributes.

Supported event kinds are `user`, `assistant`, `llm`, `tool`, `user_wait`, and `event`. Canonical timestamps use [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.html#section-5.6) strings in the domain model, SQLite TEXT columns, API, and normalized exports. Use `start_time`, nullable `end_time`, and session `started_at`, normalized to UTC `Z` with exactly nine fractional digits. Fixed timezone and precision preserve chronological string ordering and nanosecond precision. Integers may be used transiently for source conversion and duration arithmetic. Duration metrics remain milliseconds. Raw retained source objects keep their original timestamp fields and formats.

Accept known numeric timezone offsets and normalize them to UTC. Support instants on/after the Unix epoch with up to nine fractional digits; reject leap seconds, unknown `-00:00` offsets, and finer precision without silently rounding.

Startup migrates supported databases to schema v11 in one transaction under a write lock, preserving IDs, pagination cursors, metadata, and classifications. Earlier migrations introduced RFC 3339 timestamps, raw archives, OTLP identities, event source links, and per-field lineage. Schema v11 adds persistent session tags separately from imported metadata. Schema v10 adds/backfills the producer field using the documented exact matches; raw archives and saved labels are preserved. Schema v9 added independent raw-capture tables without fabricating old capture history. Schema v8 added mandatory source-line fingerprints for reimport conflict detection and hashes existing Codex archives without renormalizing events. Hashes contain no source text; see [migration and fingerprint semantics](data-lineage.md#8-identity-transactions-and-upgrades). Stop older processes before upgrading; failures roll back, and older binaries cannot use schema v11.

Old normalized timestamp keys `start_ns`, `end_ns`, and `started_ns` are replaced by the current names. Timestamp conversion needs no reimport; backfilling raw evidence requires the original rollouts or an existing complete capture. Refresh the UI after restart. Existing OTLP associations require the explicit [repair procedure](data-lineage.md#9-live-telemetry-mappings). Database row ID, source sequence, and temporal order remain distinct.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/config` | Enabled features and catalog, adapters, model mode, import body limit, and enabled-feature metadata. |
| `GET /api/v1/sessions` | Search/filter observed sessions with limit/offset pagination. `identity_kind=unattributed` exposes telemetry groups; `all` includes both. `producer=agentboard` or `unmarked` filters producer identity. |
| `GET /api/v1/sessions/{sid}` | Session metadata, producer and classification. |
| `PUT /api/v1/sessions/{sid}/producer` | Mark or clear the AgentBoard producer on an existing session; core endpoint. |
| `GET /api/v1/sessions/{sid}/events` | Filter by source, kind, and text; paginate with a cursor. |
| `GET /api/v1/sessions/{sid}/parallel-groups` | Derived tool-overlap groups for the full session or selected source. |
| `GET /api/v1/sessions/{sid}/raw-imports` | List complete Codex source archives with hashes and mapping versions. |
| `GET /api/v1/sessions/{sid}/events/{event_id}/raw` | Get verified source lines and archive identity, or an explicit unavailable reason. |
| `GET /api/v1/sessions/{sid}/raw` | Export exact archived JSONL; optional `import_id` selects a version. |
| `GET /api/v1/sessions/{sid}/inputs` | Query events currently classified as user inputs. |
| `GET /api/v1/sessions/{sid}/stats` | Counts, bounds, and timing groups, optionally by source. |
| `GET /api/v1/sessions/{sid}/export` | Stream normalized JSONL, optionally filtered by kind. |
| `POST /api/v1/import/codex` | Capture and normalize rollout contents; report capture ID and interpretation outcome. |
| `GET /api/v1/captures`, `GET /api/v1/captures/{capture_id}` | Inspect retained payload metadata and interpretation attempts, including failures. |
| `GET /api/v1/captures/{capture_id}/raw` | Export exact original transport bytes, including compressed/binary content. |
| `POST /v1/logs`, `POST /v1/traces` | Receive telemetry. |
| `GET /api/v1/classification-schema` | Generated strict JSON Schema for classification output; canonical category enum and reason constraints. |
| `GET /api/v1/sessions/{sid}/classification-input` | Bounded classifier messages and input provenance; no model call. |
| `POST /api/v1/sessions/{sid}/classify` | Run optional model classification. |
| `PUT /api/v1/sessions/{sid}/classification` | Save external classification. |
| `POST /api/v1/sessions/{sid}/replay` | Create a transcript replay from an edited input. |
| `POST /api/v1/sessions/{sid}/codex-plan` | Create a native continuation plan without executing it. |

Optional endpoints in this table are mounted only when their owning [feature](features.md#feature-list) is enabled; `/openapi.json` reflects that selection. Core session, event, and stats routes remain available.

Event/input pages expose `items` and `next_cursor`; pass the cursor as `after`. Session pages use `next_offset`. Updating an older event does not automatically resend it through an already advanced cursor. Refresh to retrieve updated records.

Exports contain normalized events, not a complete database/session backup. Session metadata and classification must be fetched separately. Concurrent export is not a transactionally frozen snapshot.

## 10. Classification and edited continuations

### 10.1 Classification

**MODEL-01 — Implemented**

Support the [report-derived session purpose taxonomy](session-purpose.md#categories), with a reason, model identity, and explicit dummy/fallback labeling. Keep existing category IDs compatible. Allow individual and page classification in the UI, CLI batch classification (`classify --all`, skipping saved labels unless `--force`), or an independent worker that reads bounded classification input or an export and writes a validated result. Batch failures do not suppress later sessions; unattributed telemetry is excluded. See [usage and batch semantics](session-purpose.md).

An independent model or coding-agent session must fetch trace/input data and submit labels through the frontend’s backend API, without direct database access.

The optional gateway uses an OpenAI-compatible model endpoint, defaulting to `http://localhost:30000/v1`. A configured model ID is optional; otherwise discover a model. Support `auto`, `local`, and `dummy` modes. In auto mode an unavailable service can use a labeled dummy fallback; a reachable service returning invalid output or rejecting a request must surface an error.

Use Python’s OpenAI client: `client.models.list()` discovers at the configured base URL plus `/models`; `client.chat.completions.create()` is the default generation protocol. Setting `model_api=responses` (or `AGENTBOARD_MODEL_API=responses`) uses `client.responses.create()` instead. Completed, nonempty text is required; incomplete/failed Responses results are errors. See [wire parameters](data-lineage.md#10-model-derived-data-and-continuation) and the [opt-in real classification test](session-purpose.md#live-responses-api-test). Examples/model tests should exercise an available service and allow a labeled dummy when unavailable, so unrelated development/testing can continue.

Generate strict structured-output schemas from the enum-backed Pydantic label model for both model APIs; validate typed request/result objects and publish the schema. Reject unexpected fields, invalid categories, refusals, incomplete responses, and Markdown-wrapped output. Keep the legacy `debugging` alias only at the external-submission boundary. See the [contract](session-purpose.md#structured-output-contract).

Bound classification context to 60,000 characters by default, preferring one conversation source and excluding tool bodies. Report truncation, input/prompt hashes, source event IDs, taxonomy version, and classification time. Missing usable text is an error. Treat imported transcript content as data rather than instructions to the classifier. See [selection and limitations](data-lineage.md#10-model-derived-data-and-continuation).

### 10.2 Transcript replay

**BRANCH-01 — Implemented**

From **Branch & edit**, retain prior text and tool observations, replace the selected prompt, omit later context, call the configured model, and create a linked replay session without altering the original. A call before the input must not carry a result recorded afterward; use verified source record order and reject missing result-order evidence. Label dummy responses. Reject replay histories that cannot be represented safely within the text budget or contain unsupported compaction/rollback. See [boundary rules and existing-data treatment](data-lineage.md#10-model-derived-data-and-continuation).

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

**EXT-01 — Implemented**

Support the operator-controlled [configuration variables and defaults](../README.md#configuration), covering database, optional features, model mode/endpoint/ID/key, API token and allowed hosts. Settings also control request size, ingestion concurrency, and model context budget.

The [feature list and contract](features.md) are authoritative for capability IDs, default tracing features, dependencies, persistence effects, and the required core. Disabling a feature removes its HTTP routes and OpenAPI entries, hides its UI controls, avoids its UI requests, and rejects dependent CLI operations. The dashboard remains available with an empty allowlist. Classification, replay, and native resume require explicit enablement.

Use one static first-party catalog/runtime for CLI and HTTP. Validate unknown IDs, duplicate declarations, cycles, missing dependencies and adapter ownership before opening storage. `agentboard features` inspects metadata without a database or optional parser/client imports. Only enabled feature factories run, returning routers backed by named service operations. Core owns route mounting, middleware, storage and lifecycle. Dynamic plugin configuration/module execution is removed; only the Codex adapter ships.

Disabling inspection never disables complete capture. Optional field mapping, analysis and model work are skipped while canonical input/timing/identity correctness remains active. Re-enabling exposes retained data but does not automatically backfill derived evidence; `agentboard reprocess CAPTURE_ID` supports explicit reprocessing under existing merge semantics. Feature flags are not redaction policy.

Use SQLite WAL, indexed reads, atomic canonical normalization, bounded HTTP admission and chunked CLI capture. Four concurrent ingestion requests per process is the default; capacity/storage contention returns retryable 503. Commit complete original bytes with `synchronous=FULL` before interpretation. Canonical writes and outcome updates use `NORMAL`; interrupted attempts may remain pending and require explicit retry. No successful response may silently omit supplied raw data.

Require a token before the CLI binds outside loopback. For remote operation configure the host allowlist and TLS termination. Authentication protects a shared dataset; it is not tenant isolation. Stored prompts/code/tool output are plaintext, and no automatic retention or secret-redaction policy is provided.

Keep tracing overhead away from agent execution by using explicit file imports or the existing asynchronous telemetry exporter. Do not claim measured performance improvements or service-scale throughput from the synthetic benchmark.

### 11.1 Backend portability and future agent integrations

Prototype in Python while treating service performance as a current requirement (PERF-01). Keep HTTP/OTLP, complete raw capture, canonical event semantics, storage, and adapters separable so measured needs can justify migrating some/all components later. Any replacement must validate API compatibility, raw completeness, and data migration while preserving one integration path for frontend/external clients. No rewrite, replacement language, or alternate backend is selected.

Future candidates are **OpenCode**, **Pi Agent**, and **Claude Code**. Only Codex ships; add future integrations through first-party adapters without mandatory base dependencies. Track both options in the [backlog](backlog.md).

## 12. Verification and maintenance

Maintain the specification alongside feature changes. Give each behavior one maintained design/workflow document; consolidate completed plans and reviews, remove obsolete or pointer-only pages, and repair inbound links. Keep experiment artifacts in the archive and date any aggregate observations in documentation. Validate behavior at the relevant boundary:

| Area | Acceptance coverage |
| --- | --- |
| Imports | String/array commands, mirrored/repeated prompts, growing files, atomic failure, duplicate identities. |
| CLI summary | Unique sessions versus file counts, zero new events, mixed failures, stdout JSONL, stderr summary, exit status. |
| Timing | Parallel interval union, separated sources/qualities, unknown ends, bounded waits, async exclusion. |
| Telemetry | JSON/protobuf/gzip, exact IDs/timestamps, hierarchy, correlation, malformed batches, backpressure. |
| Inspector | Example 2,838 ms span: normalized endpoints, calculated duration/hash, inferred labels; model vs dummy output, retained source objects, unknown mappings, safe text rendering. |
| View preference | Toggle both ways, reopen another event, reload, default behavior, unavailable storage. |
| Model features | Validated categories, labeled dummy results, request failures, bounded transcripts, preserved source session. |
| Feature boundaries | Empty and selective allowlists, dependencies, invalid/duplicate declarations, disabled routes/OpenAPI/CLI/UI requests, mandatory capture, optional derived writes, shared first-party adapters, catalog without database creation. |
| Native continuation | Plan validation and fake app-server protocol tests; no paid model execution required for unit tests. |
| Real Codex endpoint | Opt-in environment-configured Responses API provider; isolated AgentBoard receiver/database; successful response plus log, trace, and normalized LLM-event assertions. Skips before execution when unconfigured. |
| App-server schema workflow | Stored manifest/file/reference integrity; version and file drift detection; no-op refreshes; removed-file cleanup; preservation on failed generation/replacement. [Tests](../backend/tests/test_schema_workflow.py) use a fake CLI. |
| INPUT-01 | Context/internal exclusion, quoted markup, mirror ordering, parent metadata, wait boundaries, and explicit reimport corrections. |

For setup and commands, follow [the developer testing guide](testing.md).

**STYLE-01 — Implemented (2026-09-26):** The curation, embedding and independent classifier scopes use readable formatted source, typed configurations and named results, explicit interface annotations, boundary validation and accurate dependency diagnostics. Separate view rendering from controller actions, and model loading/epoch execution from training orchestration. Preserve caller options and rows; unexpected implementation errors propagate. Tests exercise exported behavior and shared synthetic factories. The [style check](../scripts/check_style.py) enforces the adopted Ruff/Prettier scope; repository-wide formatter enforcement is not claimed. Preserve workspace/export and classifier artifact formats, review and evaluation semantics, deterministic splits, lazy optional dependencies and locked atomic persistence. Per-model training scripts and their configuration dataclasses remain the classifier entry points.

Use browser checks for visuals/persistence and Node for JS tests/syntax; Node is not an app runtime dependency. Acceptance criteria are not claims of complete automated coverage.

**E2E-01 — Implemented, opt-in (2026-09-07):** All live model tests use only `http://192.168.1.220:30000/v1`; other endpoints are rejected. `pytest --run-private-e2e -m e2e` enables the Codex telemetry, synthetic classification and synthetic embedding tests. Model IDs can be pinned or discovered from a single advertised model; unavailable/incompatible services fail with no hosted-provider fallback. The Codex harness uses a temporary home, disables OpenAI authentication and retries, and runs one short synthetic prompt with a temporary receiver/database. Normal tests need no model requests. See [private test configuration](testing.md#private-model-tests) and [offline policy coverage](../backend/tests/test_private_endpoint.py).

Implementation references: [CLI](../backend/agentboard/cli.py), [Codex adapter](../backend/agentboard/adapters/codex.py), [telemetry](../backend/agentboard/otlp.py), [storage](../backend/agentboard/store.py), [API](../backend/agentboard/api.py), [field-origin descriptions](../frontend/provenance.js), [UI](../frontend/app.js), [models](../backend/agentboard/models.py), and [native resume](../backend/agentboard/resume.py). See also [architecture](architecture.md) and [runnable examples](../examples/README.md).

### 12.1 Required examples

**EXAMPLE-01 — Implemented examples, with the execution limits noted below**

Maintain the repository’s `examples/` folder and a documented demonstration of each initial use case. Network examples must consume the public backend API used by the frontend. Use synthetic data so ordinary examples do not require importing private sessions or running real repository commands.

The [runnable example catalog](../examples/README.md) maps every required use case to a script or walkthrough: import; LLM/tool/parallel timing; OTLP; external trace analysis; model classification; edited transcript replay; input extraction; native Codex plan; independent classifier; UI inspection; feature configuration and retained-capture reprocessing; and a storage benchmark. Keep that catalog current. Input extraction retains the section 7 limitation; the benchmark does not certify service capacity.

Install the development extra for example/test dependencies. Model examples use section 10.1’s OpenAI client, endpoint/discovery, and `auto` fallback rules. Keep unavailable-service dummy results explicit and reachable-service errors visible. Non-model examples must work without a model service.

Acceptance: every row has a runnable script or an explicit UI/CLI walkthrough. A missing local model must not prevent import, timing analysis, telemetry, export, or UI development. Native Codex execution is separate from dummy transcript replay and requires an actual locally stored Codex session.

## 13. Open work and scope boundaries

| Item | Current position |
| --- | --- |
| Verified human authorship | Rollout rules exclude recognized context/internal input; unknown wrappers, unmarked automation, and identical human-supplied envelopes remain ambiguous (§7). |
| Automatic rollout watcher/import hooks | Not implemented; imports are explicit. |
| Dashboard live refresh | Not implemented; manually refresh after ingestion. |
| Exact human think time, embedded approvals, async question waits | Not reliably established from the currently supported records. |
| Mandatory complete raw capture and per-field source-line lineage | Complete Codex/OTLP capture is implemented independently of optional features. Codex field links exist when enabled; verified cross-source and analysis/model lineage remains open. See [archive semantics](data-lineage.md#35-raw-archive-and-export). |
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
| Plugins and user configuration for optional features | Section 11. | Refined to first-party configurable features; third-party loading deferred. |
| Analyze time spent on LLM responses and tool calls | Section 6. | Documented, including measured/estimated distinctions and overlapping intervals. |
| Classify writing, coding, bug-fixing, and similar sessions using another AI model or coding session | Section 10.1. | Documented, including external coding-session classification. |
| Resume from the middle and change user input | Sections 10.2 and 10.3. | Documented through transcript replay and native continuation at supported turn boundaries; exact replay and file restoration are not claimed. |
| Easily filter and extract all user inputs | Sections 7 and 9. | Implemented with inferred rollout origin and explicit reimport; authorship coverage limits remain (§7). |
| Repository `examples/` folder with a demo for every initial use case | Section 12.1. | Explicit requirement with a runnable demo mapping. |
| Use the OpenAI client with the local model service and its models endpoint | Sections 10.1 and 12.1. | Python OpenAI client and exact models endpoint specified. |
| Continue development/testing with a dummy model if the local service is unavailable | Sections 10.1 and 12.1. | Documented explicitly for examples and model-feature development. |
| Keep OpenCode, Pi Agent, and Claude Code in mind without implementing them now | Section 11.1. | Three named candidates; future integrations only. |

### 14.1 Scope clarification resolved

On **2026-09-06**, the user moved high-volume service support to [WL-001](backlog.md#wl-001--efficient-high-volume-tracing-service) for later review. Initial scope remains local/modest private service, excluding multi-tenant SaaS, distributed storage, and durable queues. No throughput, retention-volume, concurrency, latency, or resource targets are agreed.

Scalability remains a desired outcome; high volume alone does not require multi-tenancy, distributed infrastructure, or a language rewrite. The synthetic benchmark does not establish production capacity.

On **2026-09-08**, the user clarified that complete raw collection must always support future inference/calculation and that high service performance is required while prototyping in Python. RAW-01 and PERF-01 (§1.1) record these requirements. High-volume deployment infrastructure remains deferred; efficient implementation and performance measurement apply now.

## 15. Durable experiment storage

**Filesystem implemented — 2026-09-25.** The recorder, reader and coverage report preserve experiment inputs, results and report dependencies independently of Git worktrees. Optional self-hosted MLflow remains planned. The [experiment storage design](experiment-storage.md) defines the implemented interface, format and remaining architecture.

**Clarified 2026-09-25:** `AGENTBOARD_DATA_HOME` is the machine data root, selected as `/Users/coral/.agentboard/data` on the current machine. Place experiment storage under `experiments/{datasets,runs,staging,sync}/`, without a `projects/agentboard/` level; project identity stays in metadata. The one-time migration preserved the combined **Local through September 24 + Spark** dataset and historical subset evidence. The selected 2026-09-26 report has complete saved coverage for eight independent configurations; this is not human verification. [Dataset and coverage rules](experiment-storage.md#dataset-and-classification-coverage) own the dated counts, configuration selection and reuse semantics. Future Hugging Face dataset version storage is deferred to [WL-004](backlog.md#wl-004--hugging-face-dataset-version-storage).

The acceptance cases below remain the full contract. [Synthetic storage tests](../backend/tests/test_experiments.py) and the [offline example](../examples/experiment_storage.py) cover filesystem behavior in EXP-01, 03–07, 13–14 and the local portions of EXP-02, 10 and 12. EXP-11 is the completed one-time migration, evidenced by the [migration audit](experiment-storage.md#local-migration-audit). EXP-08–09 and all shared-mode, deployment and operational backup checks remain unimplemented. Passing local tests do not establish those guarantees; storage verification makes no model calls.

| ID | Requirement | Acceptance criterion |
| --- | --- | --- |
| EXP-01 | Use `<AGENTBOARD_DATA_HOME>/experiments/` outside all relevant Git checkouts, without project-directory nesting. | Two worktrees resolve the same configured data root and `experiments/` archive; project identity is metadata. Deleting one worktree preserves runs. Invalid roots fail without falling back to the working directory. Separate writable dev databases remain independent. |
| EXP-02 | Provide filesystem-only and filesystem + MLflow modes using one format. | Record, list, inspect, verify, and regenerate a report without MLflow installed or network access. Switching publication on preserves existing IDs and bytes. |
| EXP-03 | Connect only explicit experiment producers. | An instrumented synthetic script records a run; ordinary browsing, ingestion, and model calls outside that recorder create no MLflow experiment records. Recording does not execute a model or arbitrary artifact code. |
| EXP-04 | Preserve complete supplied evidence and every attempt. | Round-trip arbitrary bytes, unknown JSONL records, line endings, prompts, schemas, responses, and intermediate files by SHA-256. Missing files, interrupted writes, and disk exhaustion cannot become successful finalized runs. |
| EXP-05 | Give runs stable IDs and immutable finalized manifests. | Concurrent runs from different worktrees cannot overwrite each other. Changed content under an existing finalized ID is rejected. Recovery preserves interrupted evidence; corrections create a new run with an explicit relationship. |
| EXP-06 | Record inputs and transformations precisely. | Manifests identify source artifact hashes, code/environment/configuration, output hashes, metric units and origins, and known provenance gaps. Unknown model/code versions and absent metrics remain unknown, never zero or inferred success. |
| EXP-07 | Regenerate derived resources from pinned dependencies. | A report consuming two saved runs executes offline with their preserved code/configuration and inputs; its new manifest references both. Missing dependencies or incompatible schemas fail explicitly; no implicit use of latest runs or fresh model calls. |
| EXP-08 | Publish complete archives through the shared service. | A second machine retrieves a finalized run and all declared input dependencies without access to the producer's filesystem. Downloaded bytes match manifests. Clients use MLflow APIs, not direct database or server-path access. |
| EXP-09 | Separate execution outcomes from publication outcomes. | Server outage, partial upload, and interrupted fetch preserve local results and expose retryable publication state. Retrying a known mapping reuses it; an ambiguous remote creation blocks for reconciliation rather than blindly creating another run. |
| EXP-10 | Preserve privacy and isolate optional infrastructure. | Filesystem mode makes no tracking requests. Test authentication failures and an unapproved destination using synthetic data. MLflow credentials never enter recorder-generated manifests; optional clients are not imported by normal tracing startup. |
| EXP-11 | Complete the one-time migration without data loss, using one current combined dataset; retire legacy migration tooling afterward. | Completed migration evidence records the source inventory, verified copied bytes, original subsets/references and unknown historic provenance. Originals remain intact, and historical runs retain their original inputs. Current readers verify the migrated bundles without migration code. No legacy import command, script or backward-compatibility layer is required. |
| EXP-12 | Back up and restore the archive and catalog. | Restore local bundles and the server's metadata, artifacts, and access-control configuration to an isolated destination; verify hashes, dependency resolution, and report inputs. Document the tested backup schedule and recovery window before real-data rollout. |
| EXP-13 | Track partial classifier coverage separately from execution and publication outcomes. | For a synthetic dataset and historical subset results, join by session/turn identity, complete input hash, and model/configuration. Missing, invalid, or changed-input results remain pending; historical outcomes and bytes stay unchanged. Coverage reports preserve the full requested denominator. Later classification creates new runs, without migration invoking models or modifying finalized runs. |
| EXP-14 | Keep dataset identity and pinned content independent of storage location. | The filesystem reader resolves dataset/subset references by immutable manifest and artifact hashes, not original machine paths or moving latest aliases. Unknown storage providers fail explicitly. Filesystem recording/reporting needs no Hugging Face client or network; an external adapter and its migration tests are deferred to WL-004. |

The maintained filesystem scope includes the local recorder/reader and coverage reports. The legacy migration is complete; its tooling was removed on 2026-09-25 at the user's request, with no ongoing backward-compatibility requirement. Publication/retrieval and a private shared deployment remain accepted follow-up work. Hugging Face dataset migration, S3, DVC, distributed scheduling, live model tracing, automatic experiment discovery, multi-tenant SaaS, and deterministic model reruns are outside this first implementation. Any future missing-target classification is a separate explicit experiment, not part of storage migration. The [current local-only data policy](development.md#automatic-worktree-data-setup) remains effective until a scoped private-sharing workflow is implemented and documented; this specification does not authorize uploading existing archives.

### 15.1 Independent classifier comparisons

**Requested September 25–26 and completed 2026-09-26.** Complete the pending GPT-6 Luna low targets, replace each remaining batched model with independent calls, then add GPT-6 Astra extra-high and both GPT-6 Luna/GPT-5.6 Luna max configurations. Preserve the exact rubric, complete input hashes and preceding-turn context, one target per fresh process, empty targets and every attempt. Independent model queues may run concurrently with separate progress/failure handling. Retire batched results from active coverage/comparison only after verified completion; preserve immutable historical evidence. Low/max results are distinct configurations. The user explicitly authorized OAuth for these dataset experiments; application integration tests retain their private-endpoint requirement.

**Reference decision, 2026-09-26:** GPT-6 Astra extra-high's saved categories and reasons are the starting reference answers, superseding the earlier GPT-6 Sol reference. Pin every answer to its original result and full input hash. Compare each of the other seven configurations only against Astra over the complete selected denominator. Recalculate comparisons offline from saved labels without new model calls. These are model-generated answers awaiting human review; report category agreement rather than human-validated accuracy.

**Pricing and cache requirement, implemented 2026-09-26:** Present total and per-turn hypothetical API cost beside agreement, including Astra's cost separately. Use recorded successful-call usage and a dated, cited rate card; preserve cached-input discounts, count reasoning within output once and expose input cache counts. State service tier, context/cache assumptions and excluded unknown failed-attempt usage. Do not infer OAuth charges. Save the calculation, pricing snapshot and usage provenance with the immutable report.

The [selected results](experiment-storage.md#dataset-and-classification-coverage) cover all 1,431 targets across eight configurations, with zero pending. Full execution, archive verification and offline regeneration evidence remain local. These completed experiments do not authorize further model execution or imply human adjudication or supervised-model training.

## 16. Supervised classifier comparison

**Implemented 2026-09-26; real-data execution pending.** Compare a fine-tuned BERT-like classifier, LightGBM over frozen embeddings, and saved GPT predictions for the existing eight-category turn-purpose task. Requirements cover data preparation, fixed splits, training, inference and evaluation.

The dedicated `cronjob/classifier/` project owns all experiment code, tests and dependencies. Training uses one script per model with its configuration dataclass in that script. AgentBoard application code, CLI and root dependencies remain unchanged; only explicit archive recording may reuse the existing recorder API. The [classifier workflow](../cronjob/classifier/README.md) owns current interfaces, data formats, commands and verification. The non-training utility CLI is an implementation convenience, not a required training interface.

| ID | Requirement and acceptance criteria |
| --- | --- |
| CLS-01 | Prepare ordered target and immediately preceding turn messages from the archived turn format. Preserve original inputs, identities and source hashes; record the rendered text hash and reference-label provenance. Reject duplicates and stale labels; report unlabeled/invalid targets explicitly. Human-reviewed labels are the default; GPT pseudo-label training is explicit and evaluation against them is called reference agreement. |
| CLS-02 | Produce reproducible, seeded 70/15/15 train/validation/test splits by session, with optional conversation-family groups and exact duplicate inputs kept together. Preserve every eligible example, actual ratios, category support and missing-class warnings. Allow holdout group counts to shrink when training needs additional groups for class coverage; fail only when nonempty isolated splits with training coverage are impossible. Never use test examples for fitting or checkpoint selection. |
| CLS-03 | Fine-tune a local Hugging Face sequence-classification checkpoint, selecting the best epoch by validation macro-F1. Train LightGBM on cached frozen Sentence Transformer vectors, with validation early stopping. Reject DART and its parameter aliases before data/embedding work; do not seal a LightGBM model without a positive validation best iteration. Keep dependencies optional; save label order, preprocessing, token limits/truncation counts, seeds, parameters, source model fingerprints, package versions, validation results and reloadable model artifacts. |
| CLS-04 | Batch inference reloads either saved model with identical preprocessing and label order, returning categories, probabilities and input identities/hashes. Missing dependencies, incompatible artifacts and altered caches fail explicitly. No automatic model download or remote-code execution. |
| CLS-05 | Compare BERT, embedding/LightGBM and each GPT configuration on the same test IDs and input hashes. Report requested/available/missing/invalid counts, fixed eight-category confusion matrices, accuracy/reference agreement, macro/weighted F1 and per-class support. Preserve incomplete coverage; compute paired metrics only on the common available subset and identify it. Reject duplicates or mixed configurations. |
| CLS-06 | Keep private inputs and artifacts in ignored local working storage; explicitly archive complete experiment evidence with the filesystem recorder. Include source/lock snapshots and saved inputs for offline comparison. Synthetic offline regressions cover splits, provenance, failure handling, training adapters and reload contracts. Live tests follow the private endpoint policy; implementation does not imply a trained real model or measured accuracy. |
| CLS-07 | Keep the complete classifier project under `cronjob/classifier/`, with its own scripts, dependency manifest/lock and tests. Preparation, training, inference and comparison must run without importing/installing AgentBoard. AgentBoard application files, root dependencies and CLI remain unchanged. An explicit optional archive adapter may reuse the existing recorder; saved comparison code must run independently after restoration. |
| CLS-08 | Launch each model directly from `train_bert.py` or `train_lightgbm.py`; no shared training launcher is required. Define the model's configuration dataclass in that same script, including paths, runtime settings and training hyperparameters; the LightGBM configuration also owns embedding creation/cache settings. Script execution uses an editable default config, while Python callers can pass a config instance. Save the effective configuration with artifacts and test nondefault settings. |

## 17. Dataset curation

**Implemented 2026-09-26.** Review turn-level classifier disagreements and suggest duplicates from target user input only. The [curation design](dataset-curation.md) defines transformations, persistence and interfaces for `dataset-curation-v2`. Offline and browser checks cover both provider paths; live remote embedding interoperability remains unverified because the required private endpoint was unavailable.

| ID | Requirement and acceptance |
| --- | --- |
| CUR-01 | Join pinned results by session/turn and full input hash, preserving configuration and taxonomy. Reject duplicates; expose missing, invalid and changed-input results without treating them as agreement. |
| CUR-02 | Use an explicitly selected reference configuration for `inferred` labels, including unanimous results. Missing reference results stay `unlabeled`. Only an explicit human confirmation/correction creates `verified`; mixed statuses are valid. |
| CUR-03 | Provide a disagreement queue and review interface with full target/context, original labels, reviewer, reason, timestamp and persistent audit history. Reopening restores inference; changed data requires fresh review. |
| DEDUP-01 | Calculate versioned input-only embedding cosine similarity, deterministic for saved vectors. Assistant/tool/context/label changes do not affect scores. Exclude empty input; expose threshold and exact/near-match suggestions. |
| DEDUP-02 | Let the human keep both or select which suggested duplicate to remove. No automatic deletion. Persist `removed_duplicate`, retained target and attributed decision; support restoration and prevent dangling/cyclic representatives. |
| DEDUP-03 | Show both complete recorded turn transcripts, including assistant answers, and classifier results in the duplicate review before a keep/remove decision. Preserve long output text, label status, identity and expandable context/history. Similarity continues to use user input only. |
| DEDUP-04 | Use embedding cosine exclusively, removing the lexical method and compatibility paths. Configure a local Sentence Transformers model with an immutable revision, or a remote OpenAI-compatible embeddings endpoint with an explicit model ID. Save effective provider settings, preprocessing, vectors and provenance. Local chunking preserves all normalized input; remote requests send full input and expose unknown server truncation. Reject invalid results and provider failures without fallback. Label scores as cosine, retain full-turn human review and leave prior workspace files untouched but unsupported. |
| DEDUP-05 | Remote embedding requests contain only normalized target user text. Support batching, timeout and optional bearer authentication from a named environment variable; persist no token. Validate batch indexes/dimensions, associate reordered results correctly, reject changing reported models and publish no partial workspace on failure. Review/export make no network/model calls. |
| CUR-04 | Export new immutable datasets with mixed label statuses, every removed turn explicitly marked, a separate active view and complete audit/source references. Original archives remain unchanged. |
| CUR-05 | Isolate curation from live trace storage and classifier execution. Embedding generation is explicit; review/export use saved evidence. Verify local persistence, stale-write rejection, API/UI flows, input validation and unchanged source artifacts with synthetic tests. |

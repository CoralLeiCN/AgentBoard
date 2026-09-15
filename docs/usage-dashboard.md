# Workspace usage dashboard

**Implemented 2026-09-15.** Open **Usage dashboard** in the sidebar. It requires the existing `token_usage` feature and summarizes all matching stored sessions, including sessions beyond the displayed page. It makes no model or billing request.

## Use

- Choose **All time**, **Last 7 days**, **Last 30 days**, or custom inclusive calendar dates in UTC. Presets include today.
- Filter by title/ID search, agent, producer, recorded model, or comma-separated tags. All selected tags must match; tag matching is case-sensitive.
- Select a metadata field and exact value, then **Add filter**. Repeat for additional fields. Remove a filter with its × button and press **Apply filters**.
- Inspect daily usage, model totals, and matching sessions. Open a session to edit its **Session tags**, then **Save tags**.

Applied filters are retained in the URL across refresh and browser history. **Reset** restores All time with no filters. A session's existing usage panel continues to show its entire selected archive, independently of dashboard filters.

## Calculation and evidence

The [report builder](../backend/agentboard/dashboard.py) uses the existing [usage and pricing rules](data-lineage.md#71-token-usage-and-api-value). All values are Calculated from recorded archive evidence; tags are operator assertions, separate from imported metadata.

| Input | Rule / result |
| --- | --- |
| Stored session identity | Only `identity_kind=session` counts. Unattributed telemetry groups are excluded. A bounded period includes sessions that start or have an event start/usage timestamp inside it; an interval merely spanning the period does not establish activity. |
| Archived rollout | Analyze the latest raw archive by import ID for each session, once. Earlier snapshots and repeated imports are not added. No archive means usage unavailable. |
| Usage timestamp | Normalize RFC 3339 offsets to UTC. API `start` is inclusive and `end` exclusive. The UI's **Through** date becomes midnight of the following day. Missing/invalid timestamps are excluded from bounded periods and counted separately; All time includes them outside daily buckets. |
| Cumulative counters | Difference the whole archive before filtering; assign each difference to the ending notification timestamp. This cannot apportion a multi-request difference across a date boundary. |
| Recorded model | Select matching usage records, including model changes within a session. Pricing still analyzes the whole archive first, preserving session-wide long-context rules. |
| Metadata | Exact, type-sensitive JSON Pointer/value matches against current session metadata; all conditions must match. Example: `/git/branch` matches a nested branch, `/service.name` a literal dotted key. Reimports may update metadata; this is not metadata as of each historical request. |
| Tags | Trim, deduplicate and sort up to 50 tags of 80 characters each; commas are separators. Separate `session_tags` storage preserves tags across reimports. An empty list removes all tags. |
| Token sum | Input + output; cache reads/writes are included in input, reasoning in output. A missing optional breakdown makes that aggregate breakdown unavailable. No supported records means unavailable, not zero. |
| Cost | Sum decimal USD costs for priced records. Any missing usage, partial session, excluded undated record or unpriced record suppresses the full estimate; a known priced subtotal remains visible. Unknown prices never mean free. |

**Synthetic example:** a session begins September 1 and records 1,000 input + 100 output tokens on September 7. Selecting September 7 includes the session and 1,100 tokens. Another record at September 8 midnight is excluded. Request dates, not import dates, determine usage inclusion.

The report includes only evidence supplied to AgentBoard. Separate sessions are summed independently; inherited fork history shared across different session IDs is not deduplicated ([DQ-15](data-quality-gaps.md#register)). Prices use the existing dated catalog, not historical rates at each event date. Tool fees, nonstandard service tiers, subscription charges and taxes are excluded. Unknown model prices and partial archive findings remain explicit. These limitations prevent treating the report as an actual bill.

## API and storage

`GET /api/v1/usage-summary` accepts `start`, `end`, `q`, `agent`, `producer`, `model`, repeated `tag`, `metadata` (JSON object), `limit` and `offset`. It returns the complete filtered `summary`, `daily` and `models` aggregates, filter facets, and paginated session `items`. Agent/tag/metadata facets cover all stored sessions; model options cover sessions matching the session filters, before date/model filtering. Summary and rows share one SQLite read snapshot.

Example metadata parameter value: `{"/git/branch":"main","/flag":true}`. Booleans and numbers remain distinct. Invalid dates, reversed ranges, invalid metadata objects and unsupported producer filters return 422.

`PUT /api/v1/sessions/{sid}/tags` accepts `{"tags":["work","review"]}`. It remains available in core when token analysis is disabled. Schema v11 adds the tag table without rewriting archives or existing session metadata. Checkpoint an existing development database before migration, following the [development workflow](development.md#automatic-worktree-data-setup). Older binaries cannot open v11 databases.

Reports scan matching archives on demand. Pagination limits returned session rows, not aggregation work; no persistent usage cache or large-installation performance guarantee is provided.

## Verification

[Synthetic backend regressions](../backend/tests/test_dashboard.py) cover pagination-independent totals, latest-archive selection, UTC boundaries, nested and typed metadata, tags, missing evidence, model filtering, long-context pricing, disabled features, and v10 upgrades. [Rendering tests](../frontend/tests/dashboard.test.cjs) and [interaction tests](../frontend/tests/features.test.cjs) cover query construction, missing/zero presentation, escaping, navigation, persistence state and stale responses. These tests make no model request.

**Validation on 2026-09-15:** `uv run --extra dev pytest -m "not e2e" -q` passed 474 tests (5 live tests deselected); `node --test frontend/tests/*.test.cjs` passed 44. Ruff and `git diff --check` passed. In-app browser checks covered single-day and combined metadata/tag filters, empty results, tags and filters after refresh, 390-pixel and desktop layouts, and a separate `features=[]` profile. Private-model and Codex schema checks were not needed: no model execution, CLI upgrade or app-server integration changed.

The existing 10,000-event synthetic ingestion probe ran sequentially on Darwin arm64 / Python 3.13.3: base `5a29eb3` took 3.945 s with 33.91 ms stats; this change took 3.975 s with 34.54 ms stats. Each used a new temporary database. One run per version is a smoke comparison, not a throughput guarantee or a dashboard-scale benchmark.

# Unified Codex timeline

Implemented 2026-09-06. The dashboard defaults to **Unified timeline** when a session contains historical rollout or Codex item records. Prompts, assistant messages, estimated intervals and measured items appear chronologically in one view. The source selector retains both original views for debugging; OTel and replay remain separate.

## Composition rules

The [read model](../backend/agentboard/unified.py) reads normalized events without changing stored records or raw archives.

| Inputs | Display behavior | Origin |
| --- | --- | --- |
| One rollout tool `attributes.call_id` equals one item's `attributes.item.call_id` (or `item.id` when no call ID exists), in the same session and nonempty turn; kinds agree and item has an end | One row retains the item ID, timing and status, with rollout text when present. Both original normalized records are included in inspector evidence. | Inferred identity association; timing retains its existing quality |
| `item.parent_call_id` equals a unique same-turn rollout call ID | Separate child row with an explicit parent-event reference and child label; parent and child are not collapsed. | Normalized parent identifier; calculated lookup |
| Missing, conflicting, duplicate or different IDs; missing turn; incomplete item | Separate rows, labeled unmatched or ambiguous. No matching by name, command text or timestamp proximity. Historical estimates remain available. | No established correspondence |
| Historical messages and inferred LLM intervals; item output streaming intervals | All remain visible. Streaming and estimated response activity are distinct measurements. | Existing event provenance |

For example, a rollout call from 00:00:00–00:00:05 and a same-turn item from 00:00:01–00:00:09 with an exact unique call ID display once using the item's interval. The inspector retains both intervals and their source records. Different IDs retain two rows even if their names and timestamps match.

The local example session `01a07416-7481-7693-9a48-c08c7d09d324` has rollout `call_…` IDs and item `exec-…` IDs without an explicit bridge. Its overlapping command and file edit remain labeled item rows; the unified view does not claim to have matched them to rollout calls.

## Timing, groups and inspection

Summary timing stays partitioned by source, kind and quality after exact duplicate removal. The LLM card prefers historical estimates when available; the tool card prefers measured item timing. Each card names its source. These cards are not totals across all displayed rows. Unmatched rows may describe related work, so their durations must not be added across sources. Open intervals have no duration; conversation messages appear as point events.

Parallel groups are computed before search or pagination, retaining source/quality/turn/trace/parent boundaries. Labels include **Items** or **Rollout** to distinguish source-local group numbers. A recorded overlap is not proof of a shared model-request batch. Explicit child relationships remain visible in the inspector.

The new `GET /api/v1/sessions/{sid}/unified` endpoint returns composed events, source-separated statistics and parallel groups. `limit`, `after` (offset), `kind` and `q` apply after full-session composition. Pagination assumes a fixed dataset: restart browsing after explicit imports or other changes. Existing event, statistics and export endpoints retain their source-level behavior. **Export source records** exports original normalized records, including both sources, rather than the composed rows.

No reimport or database migration is required. Composition currently loads the session's Codex events into memory; large sessions may need a cached/indexed read model later. Missing identity links and nesting metadata remain correctness limits, not inferred relationships.

Verification: [synthetic composition and API tests](../backend/tests/test_unified.py) cover exact matches, ambiguous and missing identities, turn boundaries, explicit children, incomplete-item fallback, provenance preservation, separate timing totals, filter/pagination stability and unchanged source exports.

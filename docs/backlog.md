# AgentBoard backlog and wishlist

Updated: 2026-09-06

Future capabilities for review, not initial-release acceptance criteria or delivery commitments. Current requirements live in the [specification](specification.md); current correctness concerns in the [gap register](data-quality-gaps.md).

## Wishlist register

| ID | Item | Status / origin |
| --- | --- | --- |
| WL-001 | Efficient high-volume tracing service | Initial requirement; user explicitly deferred it to the wishlist on 2026-09-06. |
| WL-002 | Migrate backend components if performance justifies it | Future option from initial requirements; retain Python now. |
| WL-003 | OpenCode, Pi Agent, and Claude Code integrations | Future candidates from initial requirements; only Codex ships. |

Priorities, owners, implementation plans, and delivery dates are unassigned. Row order is not priority order.

## WL-001 — Efficient high-volume tracing service

### Desired outcome

Support larger service workloads while retaining lightweight local operation and one API for frontend/external clients. Service infrastructure must remain optional locally.

### Current position

Python, SQLite WAL, streaming CLI imports, bounded HTTP ingestion, and grouped timing aggregation support local/modest private use. The synthetic [benchmark](../examples/benchmark.py) probes storage behavior; it does not establish production capacity. Multi-tenancy, distributed storage, and durable queues are outside initial scope; high volume alone does not require them.

### Questions for a future review

Agree targets before choosing architecture:

- Sustained/burst ingestion in events and bytes; retained session/event counts and retention duration.
- Concurrent writers, users, and external clients; acceptable import/query/aggregation/export latency.
- Local/hosted CPU, memory, disk, and operational budgets.
- Durability, retry, backpressure, and availability guarantees; tenant isolation versus one larger shared workspace.

### Review checklist

- [ ] Agree workload/resource targets.
- [ ] Measure realistic ingestion, queries, exports, and overlapping traces.
- [ ] Profile parsing, normalization, storage, indexes, and aggregation.
- [ ] Compare focused optimizations with optional storage/collector/worker changes.
- [ ] Preserve a small local installation without service-only dependencies.
- [ ] Assess WL-002 from measurements, not an assumed need to rewrite.
- [ ] Promote accepted scope and measurable criteria into the specification.

No architecture or numerical capacity target is selected.

## WL-002 — Optional backend language migration

Consider replacing some/all Python components only if profiling shows they prevent agreed performance targets. Candidate boundaries: ingestion, telemetry normalization, storage. No language or component is selected.

Review API/canonical-event compatibility, data migration, local installation cost, and maintenance. Preserve these boundaries now without building a speculative second backend.

## WL-003 — Future agent integrations

Consider **OpenCode**, **Pi Agent**, and **Claude Code** after initial Codex support. For each, review session/telemetry formats, identity, human-input evidence, timing quality, tool lifecycle, and continuation behavior. Use optional adapters/plugins and the shared event/API contract; do not assume Codex-equivalent evidence or replay capabilities.

## Review process

Review date: **not scheduled**; no automatic reminder exists. At review, retain, investigate, accept, or drop each item. Log the decision and update the specification only for accepted scope. Throughput, deadlines, and priorities remain open until agreed.

Human/context/internal-input correctness remains tracked in [specification §7](specification.md#7-user-inputs-and-internal-context) and the gap register; it is not a deferred capacity feature.

## Decision log

| Date | Decision |
| --- | --- |
| 2026-09-06 | User requested a separate backlog/wishlist for high-volume service support, to be reviewed later. Keep initial-release deployment scope unchanged. |
| 2026-09-06 | Recorded the original future-language and future-agent options alongside that item; no implementation or prioritization decision was made. |

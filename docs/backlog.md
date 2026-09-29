# AgentBoard backlog and wishlist

Updated: 2026-09-26

Future capabilities for review, not initial-release acceptance criteria or delivery commitments. Current requirements live in the [specification](specification.md); current correctness concerns in the [gap register](data-quality-gaps.md).

The separate [experiment follow-ups](#experiment-storage-and-classification-follow-ups) track remaining shared-storage implementation and human dataset review; they do not defer its requirements to the wishlist.

**Clarified 2026-09-08:** the [principles](principles.md) require high performance and complete raw collection throughout the Python prototype; [RAW-01/PERF-01](specification.md#11-raw-capture-and-performance-acceptance) define acceptance. WL-001 defers larger deployment capacity and infrastructure. Efficient implementation and performance measurement apply now.

## Wishlist register

| ID | Item | Status / origin |
| --- | --- | --- |
| WL-001 | Efficient high-volume tracing service | Initial requirement; user explicitly deferred it to the wishlist on 2026-09-06. |
| WL-002 | Migrate backend components if performance justifies it | Future option from initial requirements; retain Python now. |
| WL-003 | OpenCode, Pi Agent, and Claude Code integrations | Future candidates from initial requirements; only Codex ships. |
| WL-004 | Hugging Face dataset version storage | User-selected future direction, 2026-09-25; adapter and migration deferred, datasets remain local initially. |

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

Consider **OpenCode**, **Pi Agent**, and **Claude Code** after initial Codex support. For each, review session/telemetry formats, identity, human-input evidence, timing quality, tool lifecycle, and continuation behavior. Use the [first-party module contract](features.md#first-party-module-contract), owned adapters, and shared event/API contract; do not assume Codex-equivalent evidence or replay capabilities.

## WL-004 — Hugging Face dataset version storage

Move dataset version storage to Hugging Face in a future phase while retaining experiment results under `<AGENTBOARD_DATA_HOME>/experiments/`. The [storage design](experiment-storage.md#future-dataset-storage) owns the separation between logical dataset identity and storage location. The implemented recorder works entirely with local files; no adapter, repository, or upload is configured now.

Before implementation, define repository ownership/access, immutable revision mapping, subset/schema compatibility, and a scoped sharing policy for selected evidence. Verify source bytes and dependency resolution across migration, retain local evidence required for offline reports, and preserve existing result/input hashes. Do not use a moving branch as a reproducibility reference or silently redact raw sources during transfer. Test the adapter and restoration with synthetic artifacts before selected real data.

## Third-party extensions

Deferred until a concrete external integration cannot reasonably ship with AgentBoard. Any proposal must cover package identity/versioning, permissions, installation/removal, recovery, observability and support. The default design direction is supervised separate-process execution with a small versioned RPC contract, cancellation and health checks; no implementation is selected. Core would retain database, migrations, auth, lifecycle and UI ownership. Compare worker layouts and coarse operations using performance and threat requirements before adding isolation overhead.

## Experiment storage and classification follow-ups

The filesystem recorder, one-time migration and curation module are implemented. The [selected coverage report](experiment-storage.md#dataset-and-classification-coverage) has complete saved results for eight independent configurations, including both Luna max extensions; the missing-target execution item is complete for that selection. Seven comparisons use Astra extra-high as the model-generated reference, with recorded cache usage and hypothetical API costs. These entries track remaining work and do not schedule models or assign human labels.

| Follow-up | Status / acceptance source |
| --- | --- |
| Private MLflow service, publisher/fetcher and operational backup/restore | Accepted, not implemented. [EXP-08–10 and EXP-12](specification.md#15-durable-experiment-storage) require complete dependency transfer, separate publication state, retry/conflict handling, access policy and verified restoration. |
| Supervised turn-purpose baselines against GPT | Independent [classifier workflow](../cronjob/classifier/README.md) implemented under [CLS-01–08](specification.md#16-supervised-classifier-comparison). Reference-label selection/adjudication and real-data training/comparison remain pending experiment execution; no accuracy result is implied. |
| Human adjudication of disagreements and suggested duplicates | Review module implemented under [CUR-01–05 and DEDUP-01–05](specification.md#17-dataset-curation); labels become verified and turns become removed only through explicit attributed decisions. |
| Live remote embedding integration verification | Adapter implemented and covered with mock HTTP. The required private endpoint refused connection on 2026-09-26; rerun the isolated synthetic test when it supports embeddings. [Verification limits](dataset-curation.md#verification-and-limits). |
| Large-scale similarity indexing and automatic review transfer | Deferred. Current cosine comparison uses matrix blocks, and changed inputs require a new workspace without automatic verification transfer. |

Future classifier runs remain explicit experiments when new inputs/configurations need coverage. They are not storage migration or automatic curation work. These follow-ups do not relocate baseline/dev databases or resolve split-session normalization. WL-004 is a separate future dataset-storage adapter.
## Review process

Review date: **not scheduled**; no automatic reminder exists. At review, retain, investigate, accept, or drop each item. Log the decision and update the specification only for accepted scope. Throughput, deadlines, and priorities remain open until agreed.

Human/context/internal-input correctness remains tracked in [specification §7](specification.md#7-user-inputs-and-internal-context) and the gap register; it is not a deferred capacity feature.

## Decision log

| Date | Decision |
| --- | --- |
| 2026-09-06 | User requested a separate backlog/wishlist for high-volume service support, to be reviewed later. Keep initial-release deployment scope unchanged. |
| 2026-09-06 | Recorded the original future-language and future-agent options alongside that item; no implementation or prioritization decision was made. |
| 2026-09-08 | User requires high performance while prototyping in Python and complete raw capture for future analysis. Record these as current requirements (RAW-01/PERF-01); keep larger deployment infrastructure and language replacement separate. |
| 2026-09-25 | Use `/Users/coral/.agentboard/data` as the selected machine data home, with experiment evidence under `experiments/` and no project-directory layer. Keep one current combined dataset; historical runs provide partial coverage, with remaining classifications pending for later execution. |
| 2026-09-25 | Record future Hugging Face dataset version storage as WL-004; keep filesystem recording first and optional MLflow publication separate. |
| 2026-09-25 | Implemented filesystem storage and completed the subsequently requested local migration. Preserve original evidence and partial coverage; defer model execution and remote publication. The validity-aware audit adds one pending GPT-5.6 Luna result to the earlier input-reuse count. |
| 2026-09-25 | Retire legacy filesystem migration scripts and the import command after the verified migration. Maintain the recorder/reader and coverage reports; retain immutable migrated evidence and the audit receipt. |
| 2026-09-26 | The selected report now contains complete results for five independent classifiers. Keep historical subset results unchanged; human verification remains separate from model coverage. |
| 2026-09-26 | Extend independent coverage with Astra extra-high and both Luna max configurations, keeping low/max distinct. Use Astra as the starting reference instead of Sol, retain seven comparisons across eight configurations and show recorded input cache plus dated API cost estimates. Preserve earlier results and batched archives; human review remains pending. |
| 2026-09-26 | Implement curation with mixed inferred/verified labels, full-turn duplicate review, reversible removal and configurable local/remote embedding cosine. Retire lexical similarity and earlier workspace compatibility. |

# AgentBoard principles

Established: 2026-09-08

AgentBoard helps people understand coding-agent behavior through preserved evidence and useful analysis. These product intentions and design principles guide architecture, implementation, and review. They state the required direction; implementation status and acceptance criteria live in the [specification](specification.md), with refactor steps in the [modular feature plan](modular-features-plan.md).

## Preserve complete raw data

Always collect and preserve complete raw data from configured sources so future inference, calculations, and improved parsers can use the original evidence. Preserve unknown records and fields even when current analysis cannot interpret them. Normalized events, selected attributes, hashes, and derived results cannot replace the original source.

Feature switches and performance optimizations must never reduce raw completeness. Preserve captured evidence across interpretation failures and make capture failures explicit. Lossless storage optimizations are compatible with this principle; silently sampling, truncating, or dropping source data is not.

## Keep the core dependable and analysis optional

The dashboard, core browsing, shared storage, complete raw capture, configuration, and service lifecycle form a dependable core. Analysis, evidence inspection, export, and continuation capabilities can be enabled independently according to their dependencies.

Disabling an optional capability removes its actions and skips its optional clients and derived work while preserving raw collection and existing history. HTTP, CLI, and UI must agree on capability availability. Configured ingestion sources remain explicit; analysis settings must not disable their collection.

## Demand performance throughout prototyping

High service performance is a design requirement from the prototype onward. Keep ingestion and queries efficient, resource use bounded, and tracing overhead on the coding agent small. Measure throughput, latency, and resource consumption with representative workloads and complete raw capture.

Python is the current prototyping language. Profile and optimize demonstrated bottlenecks; keep component boundaries suitable for later replacement when measurements justify it. Language choice does not relax performance or data-completeness requirements, and no replacement language or rewrite is predetermined.

## Give modules clear responsibilities

Core owns shared data correctness, storage, configuration, lifecycle, and UI placement. Features use narrow interfaces for their work. Source adapters preserve evidence and normalize it into a shared contract. Adding a capability should have predictable integration points and avoid unintended effects on unrelated capabilities.

Keep one coherent API for the dashboard and external clients, with consistent semantics across entry points.

## Make evidence and uncertainty visible

Distinguish original evidence, normalized values, calculations, inferences, and model-generated results. Keep provenance, timing quality, and completeness visible. Missing evidence is unknown; it must not be presented as zero activity or proof of success.

Changes to interpretation must identify effects on existing results and whether reprocessing is needed. Preserve the original data needed to revisit those interpretations.

## Grow through demonstrated needs

Keep local operation simple and the architecture understandable. The current direction is a first-party modular service with capabilities shipped and tested together. Introduce third-party distribution, process isolation, additional infrastructure, or another implementation language when a concrete requirement and evidence justify their cost.

Keep enduring principles here, measurable requirements and gaps in the [specification](specification.md), current behavior in the [architecture](architecture.md) and [data lineage](data-lineage.md), and deferred options in the [backlog](backlog.md).

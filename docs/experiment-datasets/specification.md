# Experiment dataset specification

**Updated 2026-10-14. Intent/workflow accepted; initial Codex-only draft builder implemented.** This is the authoritative requirements document for the independent [experiment dataset work](README.md). The [technology decision](technology.md) distinguishes current behavior from the remaining design and acceptance targets below.

**Workflow accepted 2026-10-04:** inspect all selected content; combine detection with contextual review; apply controlled transformations or exclude unsuitable examples; validate privacy and task usefulness separately; review before public release. The user selected a custom **Codex runtime** integration and dropped the Pydantic AI requirement. The initial implementation uses Codex findings and deterministic edits; supplemental rules, validated detector model selection, quality thresholds and final review/release mechanisms remain open.

## Accepted requirements

| ID | Accepted requirement |
| --- | --- |
| DATA-01 | Prepare new datasets above the raw turn-data layer. Preserve original evidence and historical dataset/results versions; sanitization changes derivatives only. |
| DATA-02 | Serve model-selection, benchmark, fine-tuning and training experiments. The output contract is for research consumers, not import into AgentBoard. |
| DATA-03 | Remove PII and sensitive information from prepared datasets. Address selected context, responses, annotations and metadata as well as prompts; preserve enough task meaning for the declared use, or exclude unsuitable examples explicitly. |
| DATA-04 | Account for possible future publication as a benchmark. Preparation must support privacy assessment before release; current intent is not authorization to publish existing archives or a commitment to a release date. |
| DATA-05 | Maintain dedicated, self-contained documentation that can move outside the AgentBoard repository. Keep repository-specific integration evidence separate from the portable requirements and design. |
| DATA-06 | Use a custom integration with Codex directly, using normal provider configuration and saved authentication; default PII runs to `gpt-6.1-sol` with `high` reasoning effort, with explicit per-run overrides. Pydantic AI is not required. Ground integration decisions in sample-data review and verification, distinguishing offline software coverage from live interoperability and model detection quality. This supersedes the earlier Pydantic AI preference and private-endpoint-only approach for PII work. |

**Prompt organization, accepted 2026-10-14:** keep main detection and batch instructions in dedicated prompt files that travel with the worker. Preserve exact instructions and prompt hashes in private build evidence. The [current implementation](technology.md#selected-components) preserves the existing detection wording and policy.

## Task profiles

Keep one versioned sanitized representation and explicit task projections; each profile defines its own available information and prediction point.

| Profile / status | Consumer input and target boundary |
| --- | --- |
| Turn-purpose classification — selected first profile | Completed target turn plus immediate predecessor, as defined in the [adapter decision](technology.md#adapter-and-first-profile). Category/taxonomy and label provenance are separate from input. |
| Model selection before a response — proposed | Only information available at the selection point. Future assistant output, tool results and later turns cannot enter the input. Selection targets and comparison metrics still need definition. |
| Response benchmark or supervised fine-tuning — proposed | Explicit context/prompt and separate reference response or scoring contract. Sanitize both. A recorded assistant response is a candidate target, not automatically a correct answer. |
| Other model training — proposed | Explicit objective, selected content and consumer serialization. Do not infer a training objective from the presence of transcripts. |

Context depth, tool inclusion and evaluation targets are profile decisions. Record whether context is absent in the source, excluded by policy or unavailable in the selected subset; an empty list alone cannot express all three. Never bridge a removed predecessor to a different turn silently. Either rebuild and review the dependent example with declared reduced context, or exclude it.

## Terminology and conventions

- **Raw evidence:** preserved source bytes and their private provenance; preparation never edits them.
- **Turn:** a source-adapter-defined ordered interaction unit. The adapter must explain boundaries, roles, context and missing evidence; no universal upstream turn schema is assumed.
- **Example:** one task-specific input with its selected context, optional target and annotations. A turn may support more than one profile, each with its own input contract.
- **Sanitized:** transformed and reviewed under a stated policy. It is not a claim of guaranteed anonymity or a public-release approval.
- **Dataset version:** an immutable selection of examples, metadata, split assignments and transformation/review evidence. Changed content or policy produces a new version.

Origin describes how a field was obtained at a named processing stage, separately from original authorship, privacy review, label verification and timing quality:

| Origin | Meaning |
| --- | --- |
| Normalized | Extracted, selected, propagated, decoded or reformatted from supplied evidence. Imported assistant text can be Normalized even though its original author was a model. |
| Calculated | Computed from other values, such as hashes, counts or durations. |
| Inferred | Assigned by application rules or heuristics; does not by itself express a probability. |
| Model-generated | Produced by an actual model operation at the recorded stage. Preserve upstream model authorship/provenance separately when importing its result. |
| Unavailable | No usable value, with a reason; zero and false remain values. |
| Unknown | Insufficient evidence to establish origin. |

Use UTC RFC 3339 timestamps for new build/review records. Generalize or omit identifying source timestamps according to policy. State units for durations and metrics; unavailable values are null with reasons, never silently zero. Exact consumer serialization and schema encoding remain open design decisions.

## Proposed acceptance checks

These checks define the complete implementation target. The [current offline checks](technology.md#verification-and-confidence) cover source projection, process orchestration and draft transformation; they do not establish sanitizer efficacy or completion of the review/release workflow.

| Area | Evidence required |
| --- | --- |
| Raw preservation | Hashes of selected sources, raw archives and prior experiment artifacts are unchanged after preparation, failure and retry. Preparation requires no mutation of source-system storage. |
| Privacy coverage | Synthetic canaries exercise every allowed text/metadata surface, repeated entities, Unicode/escaped strings, nested code/JSON and contextual identifiers. Secrets cannot survive into any emitted file or diagnostic intended for sharing. Unknown fields, detector failure and unsupported media block or exclude affected content visibly. |
| Review gates | Build states distinguish unreviewed, blocked, ready for a specified private use, and approved for a specified public release. Open findings cannot produce an approved package; changed bytes invalidate review of the prior version. Attribution and rationale remain auditable privately. |
| Task validity | Role/order/context relationships survive as declared; exclusions and transformations are counted. Changed text triggers label/answer reassessment. Prediction-time profiles reject future information; task-specific syntax and scoring checks pass. |
| Reproducibility | Saved recipe, versions, mapping and decisions reproduce identical sanitized payloads for deterministic steps. Any model-assisted step records exact inputs/outputs privately and its nondeterminism; no unsaved rerun is called reproducible. |
| Splits and metrics | Related context, session families and reviewed duplicate groups do not cross partitions. Masking collisions are detected. Metrics pin sanitized inputs, target origin, exclusions and denominator; historical raw-input scores are never relabeled as sanitized-dataset results. |
| Package isolation | Load/evaluate with private source storage unavailable. Traverse and inspect every packaged file/reference; no source IDs/paths, raw hashes, mappings, reviewer identities or private artifact dependencies leak. Public metadata undergoes the same review as examples. |
| Publication readiness | Record an explicit decision for the exact package, permitted source uses, distribution/license terms, residual privacy findings and benchmark limitations. Agree a correction/withdrawal process before release; removal cannot promise recall of existing downloads. |

Detector selection, thresholds and human review coverage must be evaluated on representative held-out examples before claiming efficacy. Record false negatives, false positives and utility loss by sensitive-content category with sample counts and limitations; no universal numerical target is selected yet. Proposed initial public releases require review of every emitted example as well as the full package. Sampling may measure detector performance but does not automatically authorize unreviewed content for release.

The [roadmap](roadmap.md) records unresolved scope and thresholds. These checks do not establish current sanitizer efficacy, label correctness or publication readiness.

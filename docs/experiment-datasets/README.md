# Experiment datasets

**Intent and privacy workflow accepted 2026-10-04; initial Codex-only draft builder implemented.** Prepare datasets above preserved turn-based interaction data for model selection, benchmarking, fine-tuning and training. Remove personally identifiable information (PII) and sensitive content from the derivatives while preserving the raw evidence. Live detector effectiveness and a future public benchmark release remain unverified.

This work has its own documentation set so it can move out of the AgentBoard repository. The output is a research dataset format, not an AgentBoard import format. AgentBoard is an initial source/integration context; the portable contract does not require its runtime or storage implementation. The user selected a [custom Codex integration](technology.md) with no Pydantic dependency. The host repository contains the standalone worker and usage guide; a future repository and distribution platform remain unselected.

## Document ownership

| Document | Owns |
| --- | --- |
| [Specification](specification.md) | Accepted requirements, task profiles, terminology and proposed acceptance checks |
| [Design](design.md) | Proposed preparation pipeline, privacy transformations, artifact boundaries and row contract |
| [Codex integration decision](technology.md) | Implemented worker boundaries, source-review findings, verification and unresolved model-quality gate |
| [Roadmap and decisions](roadmap.md) | Open choices, proposed implementation sequence and dated decisions |

Keep each detailed rule in its owning document. Accepted requirements, proposals and implemented behavior must remain distinct. The worker emits private drafts; review persistence and release approval are not implemented. Examples are synthetic. The user authorized the custom integration; existing private-data processing and publication boundaries still apply.

## Portability and source integration

Move this directory as a unit: links between its documents stay within it, and required definitions are included here. Optional upstream references are external URLs. Repository-specific code evidence, development commands and storage paths belong in the host repository's integration notes. They are not required to interpret this specification.

A source adapter must declare its input schema/version, turn and context mapping, identity/provenance fields, missing-data rules and supported content. It supplies pinned inputs to the preparation layer and preserves original evidence. A consumer adapter declares its task serialization, labels/targets and metrics. Neither boundary requires importing sanitized outputs into the source application.

Moving the documentation does not move private datasets, credentials, implementation code or source-use permissions. Existing evidence keeps its access and retention policy until a separate scoped decision changes it.

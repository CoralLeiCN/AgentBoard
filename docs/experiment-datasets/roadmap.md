# Experiment dataset roadmap and decisions

**Updated 2026-10-14.** The initial custom Codex worker produces private review drafts. This roadmap separates implemented scope from the remaining [dataset specification](specification.md). Owners, deadlines and a public delivery commitment are unassigned.

## Decisions and remaining choices

| Decision | Current state / remaining work |
| --- | --- |
| First experiment | Text-only completed-turn classification with immediate predecessor context selected and projected. See the [adapter contract](technology.md#adapter-and-first-profile). |
| Runtime | Custom Codex-only integration using normal provider configuration/authentication, defaulting to `gpt-6.1-sol` with `high` reasoning; Python standard-library validation. Explicit model/effort overrides and private-provider mode remain available. No Pydantic dependency. |
| Detection quality | Normal-Codex synthetic detection passed after the user's execution-mode clarification. The saved 100-sample pilot measures transformations; contextual recall, false positives and retained utility still need adjudication. No automatic provider fallback. |
| Privacy policy | Exact repeats share placeholders within session/context families. Finalize confidential-content coverage, ambiguous aliases, same-name identities, acceptable utility loss and whole-example exclusion criteria. |
| Long inputs | Local character coverage is implemented. Model/token-aware budgeting, cross-window and cross-message detection, and provider truncation need evaluation and implementation. |
| Review and reproducibility | Implement persisted human decisions, syntax/task validation, safe resumption, offline replay of recorded findings and final immutable dataset sealing. Draft completion is never approval. |
| Quality and splits | Agree human versus model-generated labels, applicability after redaction, leakage grouping, holdouts and quantitative targets. Existing labels/splits are evidence, not automatic defaults. |
| Distribution | Choose audience, source-use permissions, license, review owner and correction/withdrawal process. Public packaging is separate from private evidence storage; no publication is selected. |

## Next implementation sequence

1. Adjudicate the completed 100-sample pilot through normal Codex: review detections, missed sensitive content, false positives and task usefulness. Its measured text changes are not privacy approval.
2. Evaluate the selected detector model against labeled synthetic cases reflecting the source review, including technical false positives, indirect identifiers and long/Unicode content. Resolve chunk/context limitations and validate detection quality for the chosen configuration.
3. Add human review persistence, utility checks, replay/resumption and sealing before claiming an approved dataset workflow.
4. Conduct a scoped private-data pilot and measure misses, false positives, utility loss and label applicability.
5. Implement reviewed splits, consumer adapters, benchmark scoring and public packaging after the private contract is validated.

## Decision log

| Date | Decision | Status |
| --- | --- | --- |
| 2026-10-04 | Create an extra layer above raw turn data for model selection, benchmarks, fine-tuning and training. Preserve source evidence and remove sensitive content from derivatives; outputs are not for AgentBoard import. | Accepted; DATA-01–03 |
| 2026-10-04 | Allow possible future public benchmarks and keep dedicated portable documentation. | Accepted; DATA-04–05. No publication authorized or scheduled. |
| 2026-10-04 | Accept inspect/detect/transform/review/validate with separate task-usefulness and release review. | Accepted workflow. |
| 2026-10-04 | Initially prefer Pydantic AI; review nine sampled excerpts, profile/project the source and run 18 offline feasibility checks. | Historical evidence. The technology preference is superseded below; its checks do not validate the new Codex runner. |
| 2026-10-04 | Clarify that harness means Codex runtime; assess a custom adapter and distinguish official provider support from third-party examples. | Investigation completed; no third-party package adopted. |
| 2026-10-04 | Request a custom Codex-only integration and drop Pydantic AI if it does not fit. | Current DATA-06. Standalone draft worker implemented with synthetic process tests; live inference failed at endpoint discovery. See [technology decision](technology.md). |
| 2026-10-04 | Use the default way of Codex for PII. | Normal Codex configuration/model/login selected explicitly, superseding its private-endpoint-only restriction. Completed the saved 100-sample selection; results and mixed-contract limitations are in the [technology decision](technology.md#verification-and-confidence). |
| 2026-10-11 | Default future PII removal to `gpt-6.1-sol` with `high` reasoning. | Implemented as worker invocation settings with per-run overrides; normal provider/login retained. Existing pilot evidence keeps its original model/effort. |
| 2026-10-14 | Move detection instructions into dedicated prompt files. | Main and batch prompt files ship with the portable worker. Exact prompt text is preserved, and new builds record per-file hashes alongside existing attempt instructions. |

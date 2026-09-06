# Codex session grouping review

Reviewed **2026-09-06** against a fixed copy of the local AgentBoard database, preserved decoded OTLP spans, and the local Codex state index. This is a data-correctness audit, not a claim that every Codex task has been imported.

## Finding

The reported 3,178 sessions was an inflated count. Telemetry was arriving during review; the fixed snapshot contained **3,186 entries**, comprising **17 observed/imported conversation IDs** and **3,169 unattributed trace groups**. The previous repair preserved unknown records under trace IDs but the dashboard counted those groups as sessions. That presentation was incorrect.

| Snapshot evidence | Count |
| --- | ---: |
| Events assigned to conversation IDs | 123,791 |
| Events in unattributed trace groups | 8,411 |
| Traces with no recognized conversation evidence | 3,168 |
| Traces with one recognized conversation ID | 138 |
| Traces containing two conversation IDs | 1 |

Common unattributed operations were `append_items`, `persist_rollout_items`, authentication, configuration reads, and app-server requests. The inspected unattributed spans contained no OTel links. Neither a shared OS worker ID nor temporal proximity establishes conversation ownership.

The 17 conversation IDs comprise nine rollout-backed IDs and eight live-telemetry IDs. Four of the eight live IDs appeared in the local Codex state index; four did not. Telemetry-only startup/reviewer identities therefore must not be presented as verified human conversations or necessarily persisted Codex tasks.

## Corrections and validation

- **Separate identity kinds and counts:** schema v5 and the UI/API distinguish sessions from unattributed trace/resource groups. Unknown records remain accessible at their original URLs. The default sessions API excludes them; `identity_kind=unattributed` and `all` expose them explicitly.
- **Recognize Codex `thread_id`:** the earlier mapping recognized UUID-valued `thread.id` but missed its underscore variant, including on `session_loop` spans.
- **Respect mixed traces:** direct identity wins; descendants may inherit the nearest identified ancestor. Trace-wide inference is used only when there is one candidate. Mixed-trace roots and other ambiguous records remain unattributed. Separate traces naming one conversation share one session; a worker ID alone never joins them.

Applying the correction to the fixed snapshot reassigned **1,464 events**, leaving **17 session identities and 3,164 unattributed trace groups**. All **132,202 event IDs, row IDs and preserved decoded OTLP records** matched before and after by SHA-256 over their canonical JSON representation. These figures describe that snapshot; live counts continue to change.

[Regression tests](../tests/test_otlp_sessions.py) cover list scope, migration, delayed identity, aliases, multiple traces per conversation, mixed parent/child attribution, conflicting evidence, retries, and evidence preservation. Exact current rules, API scope and the existing-data repair command live in [data lineage §9](data-lineage.md#9-live-telemetry-mappings).

## Remaining limits

Missing conversation evidence remains missing. Unattributed records are not automatically assigned by worker, time, model, or proximity. Late parent/identity evidence can change inferred associations. Rollout import and OTel capture remain separate; neither guarantees complete local task coverage. Reviewer/human-origin classification and broad tool-name heuristics remain tracked in [DQ-01 and DQ-09](data-quality-gaps.md).

The live repair subsequently verified preservation of **159,051 existing event IDs, row IDs and decoded OTel records**. The in-app browser showed **18 session identities** after another conversation ID arrived during review, with unattributed telemetry counted separately. **120 Python tests and 7 JavaScript tests passed**; UI checks covered both lists and an unattributed group’s inspectable timeline.

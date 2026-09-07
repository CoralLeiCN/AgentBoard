# Real input-origin excerpts

**Source-derived, redacted evidence.** These 62 records come from three actual local Codex rollouts, selected after scanning 82 rollout files from September 1–6, 2026. Later files were excluded to avoid sampling this implementation task. The scan found 47 files with explicit internal-session metadata. These are file counts, not independently verified human-conversation counts or classifier accuracy measurements.

The excerpts were extracted from source records, not written as simulated conversations. Both the previous `codex-jsonl-v3` adapter and the pre-rebase input-attribution adapter (`codex-jsonl-v4`) were run on the original selected records and their redacted copies. After rebasing onto upstream field lineage, the combined adapter uses `codex-jsonl-v5`; regression tests verify the redacted expectations. Input classifications, prompt counts, and between-turn wait durations matched before and after redaction. The [manifest](manifest.json) records hashes of the **redacted** files, original line numbers, selection windows, omitted fields, producing CLI versions, and the baseline code reference.

## What the sample demonstrates

| Excerpt | Recorded evidence | Old → new input count | Old → new between-turn wait |
| --- | --- | --- | --- |
| [CLI](cli.jsonl), CLI 0.153.0, 17 records | A user-role environment envelope followed by an ordinary user request. Complete short session. | 2 → 1 | No wait in either interpretation |
| [Desktop](desktop.jsonl), CLI 0.153.4, 18 records | A three-part plugin/AGENTS/environment bundle, plus environment context between a completion and the next ordinary user request. | 4 → 2 | 2,716,373 → 2,716,391 ms (**18 ms longer**) |
| [Reviewer](reviewer.jsonl), CLI 0.153.4, 27 records | `source.subagent.other=guardian` and `thread_source=guardian_review`; two review requests represented as fragmented user-role messages and matching event-message mirrors. | 3 → 0 | 1,538,183 ms → no human-wait event |

Across these excerpts, nine previously counted inputs become **three human-attributed inputs, four context records, and two internal requests**. The context/internal records remain inspectable. The reviewer interval is 25 min 38.183 s of time between internal review turns; these records do not establish that it was time waiting for a person.

The desktop's current wait is **45 min 16.391 s**, displayed as **45.3 min · estimated · 1 wait**. None of these excerpts contains an explicit blocking `request_user_input` call. The desktop wait is derived from completion and next-prompt timestamps, without a raw Codex record saying it was waiting for a person. The CLI has only one prompt, so there is no completed between-turn wait; the reviewer has internal requests, so its gap is excluded. For these selected records, the original and redacted wait results match. See [wait semantics and missing-data behavior](../../../docs/data-lineage.md#43-between-turn-waits).

The desktop excerpt includes original lines **1–10 and 91–98**. Lines 91–98 form an uninterrupted completion-to-next-input window: completion at excerpt line 11, environment context at 14, ordinary input at 17. Missing earlier work makes full-session/LLM timing and total prompt counts unsuitable for analysis. Reviewer lines 1–27 preserve the first two complete turns, but omit subsequent turns. The CLI excerpt retains all 17 record envelopes, with fields redacted as described below.

## Redaction and limits

- All original prose is replaced, including prompts, reviewer requests, instructions, responses, plugin listings, and context bodies. Only the recognized outer context delimiters and AGENTS.md header syntax remain. The replacement path is `/example/workspace`.
- Session, message, turn, trace, item, client, and parent identifiers are consistently pseudonymized. Original identity equality is preserved; the reviewer's parent is not included in this sample.
- Every source is shifted to an artificial **2000-01-01** time origin. Retained outer and item timestamps receive the same offset, preserving exact gaps and durations while removing the original activity times.
- An allowlist removes other fields, including repository identity, home paths, configuration, account/quota information, tokens, encrypted reasoning, world state, and annotations. Empty record payloads indicate omitted fields, not originally empty source records.
- Private source locators, hashes, and redaction maps remain only under ignored `.agentboard/input-origin-private/`, with restricted filesystem permissions. They are not included in these fixtures or the public manifest. Original Codex files were read only.

This sample supports the existence of the patterns and regression checks for these specific cases. It does **not** measure precision/recall across Codex releases, independently verify human authorship, or measure human thinking time. No `subagent_notification` envelope was found in the scanned files; notification and `thread_spawn` rules still have synthetic coverage. Quoted-markup false-positive tests also remain synthetic. Heavy text redaction prevents using these fixtures to evaluate semantic authorship detection.

## Inspect locally

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/input-origin-real/*.jsonl
uv run agentboard --config config/dev.toml serve --port 4325
```

Open [the isolated dev dashboard](http://127.0.0.1:4325) in Codex's internal browser. This walkthrough overrides the dev profile's default port 4319 with 4325.

1. Open [real-input-desktop](http://127.0.0.1:4325/#real-input-desktop) and select **Unified timeline** or **Historical rollout**. Expect **2 User inputs** and **45.3 min / estimated / 1 wait**. Timeline includes **Between turns (inferred user wait)**.
2. Under **All events**, inspect `task_complete` and the following **User input** via Raw JSONL. Their shifted timestamps are `2000-01-01T00:02:24.481Z` and `2000-01-01T00:47:40.872Z`; subtracting gives **2,716,391 ms**. The intervening **Injected context** at `00:47:40.854Z` does not close the wait. The inferred wait itself has no actual raw event record.
3. Compare [real-input-cli](http://127.0.0.1:4325/#real-input-cli), with **1 User input**, and [real-input-reviewer](http://127.0.0.1:4325/#real-input-reviewer), with **0 User inputs**. Both show **No recorded waits**; All events retains their context/internal inputs.

Display dates are artificial; the manifest maps excerpt lines back to source lines. These IDs identify imported excerpts; they cannot be used for native Codex continuation. **Load demo** imports the separate synthetic checkout session, not these real excerpts.

[Regression tests](../../../backend/tests/test_real_input_origins.py) cover current attribution, exact wait boundaries, mirror deduplication, archive fidelity, source links, branch rejection, and the redaction allowlist. These tests require no access to private rollouts.

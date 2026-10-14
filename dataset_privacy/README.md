# Codex-only dataset privacy worker

Initial implementation, 2026-10-04. A standalone Python 3.11+ module that reads turn JSON, asks Codex for sensitive spans and creates a **private draft requiring review**. Runtime dependencies are the Python standard library and Codex CLI; Pydantic, Pydantic AI, Presidio and third-party Codex SDKs are not used. AgentBoard databases and application imports are not needed. Copy this directory intact to another project's root to use it there.

## Run

Run from the directory containing `dataset_privacy/`. Planning reads only local files and does not start Codex:

```sh
python3 -m dataset_privacy plan /absolute/path/to/turns.json --limit 10
```

The output reports the source SHA-256, selected/excluded turn counts, unique messages and planned chunks. `--limit` selects the first N source rows as targets; their immediate predecessors are resolved from the complete source. Omitting it selects every row. The current adapter expects a JSON array with unique integer `index`, nullable `previous_turn_index`, nonempty `original_codex_session_id`, and ordered `messages` containing `role` (`user` or `assistant`) and string `content`. Other fields stay out of draft rows. Missing/cyclic predecessor links, unsupported roles and nontext messages fail validation.

Preparation uses normal Codex provider configuration and saved authentication, with PII-specific defaults of **`gpt-6.1-sol`** and **`high`** reasoning effort (selected 2026-10-11). It requires a **new** output directory whose parent exists. Replace the hash below with the value reported by planning:

```sh
python3 -m dataset_privacy prepare /absolute/path/to/turns.json \
  --limit 10 --expected-sha256 SOURCE_SHA256 \
  --output /absolute/private-data-root/privacy-run-001
```

`--model MODEL_ID` and `--reasoning-effort LEVEL` override the PII defaults for a run; the selected model must support the chosen effort. These settings apply only to the privacy worker's Codex invocation. Provider configuration, authentication and service tier continue to come from normal Codex configuration. Credentials remain managed by Codex and are never copied into experiment artifacts. Each attempt records its requested model and reasoning effort in `invocation.json`; existing drafts and experiment results retain their original settings. Normal mode sends detection inputs to the configured Codex provider. `--codex` selects the executable, and `--timeout` sets seconds per detection attempt (default 120). The version probe has its own 10-second timeout. The adapter currently requires `codex-cli 0.160.0`; review configuration and tests before changing the version gate.

An explicit `--base-url http://192.168.1.220:30000/v1 --model PRIVATE_MODEL_ID` selects the optional isolated private-provider mode. That mode accepts literal private/loopback IP endpoints ending in `/v1` and optionally takes `--api-key-env VARIABLE_NAME`; it never falls back to normal login. The user explicitly selected normal Codex for PII work on 2026-10-04, including this pilot and its synthetic preflight. Other repository live tests retain their private-endpoint policy. Neither mode is an inference-network firewall.

## Prompts

Edit the dedicated UTF-8 files to change detection instructions:

- [prompts/detection.txt](prompts/detection.txt) contains the main privacy detection prompt, used by single-chunk and batch calls.
- [prompts/batch.txt](prompts/batch.txt) adds the batch response and coverage instructions.

[prompt_loader.py](prompt_loader.py) loads both files relative to the module once per Python process. Batch calls append a blank line and the batch instructions to the main prompt. Keep the `prompts/` directory when copying the module, and start a new process after edits. Each run records the prompt-file hashes in `manifest.json` (`prompt_sha256`), and each attempt retains its exact composed `instructions.txt`. The 2026-10-14 extraction preserves the previous prompt text and detection policy; existing artifacts keep their original instructions and hashes.

## Processing and limits

- Empty targets and targets with no nonblank user message are recorded as excluded. Recorded roles/order are preserved; a user-role message is not assumed to be human-authored prose. Only target and immediate-predecessor message text is included.
- Selected target/context messages share a private inventory, so a reused predecessor is scanned and transformed once. Sessions connected by predecessor links share a placeholder namespace. Repeated exact `(category, quote)` values within that family share replacements; aliases and same-name people still require review.
- Text is divided into overlapping **character** windows: 4,096 characters with 512 overlap by default. Configure `--chunk-characters` and `--overlap`; the former is capped at 12,000. Every nonblank selected message is covered without local truncation. This is not a tokenizer-aware context-budget guarantee; cross-message inference, server truncation and long-secret handling remain evaluation gaps.
- `--max-chunks` defaults to 256 and is checked before any detection calls. At most one validation retry is allowed per chunk. Process, event-stream and transport errors are not retried. A timeout, invalid output, conflicting overlap, unexpected tool action or changed source prevents a successful draft.
- Every attempt gets an empty working directory. Normal mode uses the existing Codex home/configuration/authentication; explicit private mode uses a fresh Codex home. The runner disables configurable execution, configured MCP servers, browsing, plugins, hooks, delegation and content telemetry; it uses a read-only sandbox and rejects action events. App pipes and unrelated credentials are excluded. One exact startup notice about disabled Code Mode is retained but tolerated; other error/action events fail validation.
- The optional `CodexDetector.scan_batch` API groups up to 16 chunks and 12,000 total characters per call. Every chunk must have exactly one valid result; omissions, duplicates and invalid spans reject the batch. The single-chunk CLI remains sequential.
- The model supplies exact quotes and an occurrence selector (`-1` for all literal matches, otherwise a zero-based occurrence index). Python computes Unicode offsets and validates the canonical spans. Unmatched quotes, invalid selectors, coercion, extra keys and overlaps fail validation. Across windows, the worker deduplicates identical findings and retains a complete validated quote over a matching contained fragment of the same category. Other conflicting overlaps fail. It records containment collapses and applies replacements from right to left. Syntax preservation and task usefulness require subsequent review.

## Private artifacts

The output directory is owner-only (`0700`); files are `0600`. It must stay private, including `draft.jsonl`: missing findings can leave sensitive content in a draft.

| Artifact | Meaning |
| --- | --- |
| `manifest.json` | Source/code/policy/prompt hashes, recipe, counts, state and completed-chunk count. `complete_draft` means processing completed; `privacy_status` remains `needs_review`. |
| `selection.json`, `lineage.json` | All selected/excluded targets and source-to-message/group mapping. Source identifiers stay private. |
| `chunks/*.json` | Pending/running/completed/failed receipts with character coverage, attempt counts and model review flags. |
| `attempts/*/` | Exact requests, schema, instructions, invocation without credential values, Codex version, final output, events and diagnostics. These may contain raw sensitive text. |
| `findings.json`, `mapping.json` | Validated spans and consistent replacement map; retain privately for audit/reapplication. |
| `draft.jsonl` | Allowlisted transformed inputs/context and opaque IDs. Labels/reference targets are omitted and marked unreviewed; no privacy approval is inferred from an empty findings list. |

An existing run is never overwritten. Caught failures retain evidence and a failed manifest but remove draft output. A hard process kill may leave a running manifest, incomplete files or a draft before its final manifest commit; consumers must require `complete_draft` and verify `draft_sha256`. That still grants no privacy approval. Resumption, human review persistence, final dataset sealing/splits, public export, media handling and model-quality certification are not implemented.

## Verification

Synthetic tests use a fake executable to exercise process invocation, strict findings, shared context, replacement, failure handling and source preservation. They verify single/batch prompt composition and recorded hashes, then copy the module to a separate directory and run planning and preparation with Python's site packages disabled. From the host repository root:

```sh
uv run --extra dev pytest -m 'not e2e' dataset_privacy/tests -q
uv run --extra dev pytest --run-private-e2e -m e2e backend/tests/test_dataset_privacy_endpoint_e2e.py -q
```

The second command is host-specific and uses its isolated private endpoint fixture; it makes a real synthetic inference request only if discovery succeeds. When moving the module, keep the portable tests here and supply the destination's equivalent isolated live-test policy. No test needs the private real dataset.

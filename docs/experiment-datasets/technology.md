# Codex-only integration decision

**Initially implemented 2026-10-04; defaults updated 2026-10-11.** The user requested a custom Codex integration and selected normal Codex provider configuration/login for PII work, then selected `gpt-6.1-sol` with `high` reasoning as its default. Normal-mode synthetic detection passed and the fixed 100-sample pilot completed using the earlier inherited model settings. The worker produces private review drafts, and general PII-removal effectiveness remains unverified. The earlier private-endpoint attempt remains preserved as failed evidence.

## Selected components

| Component | Current responsibility |
| --- | --- |
| Python standard library | Source projection, strict JSON and span validation, chunk inventory, replacement mapping, source/hash checks and private artifacts. No Pydantic or Pydantic AI dependency. |
| Codex CLI | Non-interactive detection with schema-constrained final output, either one chunk or a bounded batch per attempt. It proposes sensitive spans; it does not write dataset rows or approve privacy. |
| Prompt files | Bundled UTF-8 `prompts/detection.txt` for the main policy and `prompts/batch.txt` for batch coverage/output instructions. Loaded relative to the portable module once per process. Batch calls append a blank line and the batch instructions. Extracted 2026-10-14 with identical prompt text and policy version. |
| Model service | Normal Codex provider configuration, service tier and saved authentication, with worker defaults `gpt-6.1-sol` and `high` reasoning. Explicit model/effort overrides apply per run. A private-provider URL requires an explicit model ID and selects the isolated alternative; neither mode automatically falls back to the other. The completed pilot used configured `gpt-6-astra` with `xhigh` reasoning. |
| Review and release | All emitted drafts remain `needs_review`. Persistent human adjudication, final dataset sealing and publication are future work. |

The runner currently gates on `codex-cli 0.160.0`, inspected locally and exercised with a normal-mode synthetic preflight. It uses `codex exec --output-schema`, separate JSONL progress events and a final-response file. Schema-valid responses still require exact source-span validation. These are [official CLI interfaces](https://developers.openai.com/codex/noninteractive#create-structured-outputs-with-a-schema); the privacy adapter is our custom implementation, not an upstream PII product.

The worker supplies `model` and `model_reasoning_effort` as per-invocation Codex configuration overrides, using the [official configuration keys](https://learn.chatgpt.com/docs/config-file/config-reference). `--model` and `--reasoning-effort` can select other supported values. Each private attempt records both requested settings; the shared user configuration is not edited. Existing drafts and archived pilot results retain their original settings.

The earlier Pydantic AI prototype remains historical feasibility evidence. Its 18 checks do not validate this replacement. No Pydantic AI provider or community Codex SDK is used by the current worker.

## Source review and its consequences

The earlier review profiled one pinned dataset, inspected excerpts from nine purpose-selected turns, and ran local pattern checks. It was not a random sample or complete human privacy adjudication. Counts describe structure and pattern matches, not confirmed PII prevalence.

| Observed evidence | Integration decision |
| --- | --- |
| 1,915 turns; 13,662 messages: 2,067 user and 11,595 assistant | Process every message included by the task projection, including assistant echoes. |
| Source rows/messages carry IDs, paths, hashes, host values, event IDs and raw-line locations | Allowlist draft fields. Store source mappings and provenance privately. |
| 143 assistant-first turns; 30 targets have no user message, including 3 empty targets | Preserve order and roles. Exclude empty/no-user targets explicitly without deleting their raw evidence. |
| 16 turns contain shell-output envelopes in user-role text; 114 contain IDE-context headers | Role is recorded metadata, not proof of human authorship. Treat embedded instructions as data. |
| Longest target has 80,533 characters; largest target plus predecessor has 80,789 | Bound requests and record complete local coverage; do not silently truncate source text. |
| 22 turns contain CJK characters | Use Python Unicode character offsets. Character presence does not establish language coverage. |
| 912 turns match local-path patterns; 635 contain HTTP(S) URLs | Inspect identifying paths, accounts, machines, endpoints and project context. Presence alone does not prove sensitivity. |
| Reviewed SSH configuration contains identifying machine/user/path details missed by the earlier rule-only checks; a technical algorithm token triggered a false email match | Contextual detection and technical-text review remain necessary. No rule-only privacy approval. |

No raw excerpts, identifying values or replacement maps belong in this document. The host's private archive owns exact source selections and historical evidence.

## Adapter and first profile

The implemented first profile is **completed-turn classification**: all target user/assistant messages plus their immediate predecessor. This is retrospective input and must not be reused as before-response routing input. Labels and reference responses are omitted from the draft and marked unreviewed for the changed input.

The source adapter checks unique integer indexes, missing/invalid/cyclic predecessor links, supported roles and text types. It preserves exact role/order/content before transformation and ignores arbitrary source metadata in draft rows. Empty targets and targets without nonblank user input are excluded with reasons. A limit selects the first N source rows as targets while context resolves from the entire pinned source.

Planning the full reviewed source with the new worker succeeds without model calls: **1,885** eligible examples, **27** no-user exclusions and **3** empty exclusions. Its shared inventory contains **13,628** target/context messages and schedules **13,722** chunks at the default settings. The source originally contains 13,662 messages; the difference comes from profile exclusions and context selection. None of these counts represents approved or model-sanitized data.

A message reused as predecessor context has one inventory entry and receives exactly the same transformation. Placeholder scope is the source session/context family, joining families connected by predecessor links. Exact repeated `(category, quote)` pairs share replacements within the family. Each build gets a fresh namespace, and generated placeholders avoid collisions with source text. Ambiguous aliases and distinct people sharing a name still require review.

## Finding and edit contract

| Field/check | Implemented behavior |
| --- | --- |
| `chunk_id` | Worker-assigned ID; result must match the requested chunk exactly. The private receipt maps it to a message and source location. |
| `quote`, `occurrence` | Model returns an exact source quote and `-1` for all literal occurrences or a zero-based occurrence index. Unmatched quotes, invalid selectors and normalization are rejected. |
| `start`, `end` | Worker calculates zero-based Python Unicode character offsets with exclusive end from exact matches, then translates chunk offsets to message offsets. The canonical validator checks bounds, source equality and overlaps. |
| `category` | Enum: `PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ACCOUNT`, `USERNAME`, `HOST`, `PATH`, `PROJECT`, `SECRET`, `CONFIDENTIAL`. Category detection quality remains unevaluated. |
| `requires_review` | Required boolean from the model. Even `false` plus no findings leaves the draft in `needs_review`. |
| Strict validation | Reject extra keys, duplicate JSON keys, invalid types/coercion, unknown categories, bad bounds, incorrect quotes and conflicting overlaps. Identical findings in overlapping windows are deduplicated. During cross-window merging only, a complete validated quote subsumes an exactly matching contained fragment of the same category; other overlaps still block the build. |
| Retry | One fresh attempt after invalid findings, with generic validation feedback. Process, event-stream and transport errors stop the build without retry. Each attempt is retained privately. |
| Edits | Apply validated findings from right to left using typed placeholders. This creates proposed replacements for review, not a privacy-approved transcript. |

The current `turn-privacy-v2` detector returns quotes/occurrences rather than asking the model to count characters, reducing offset-counting work. Earlier validated offset responses remain historical pilot evidence; their canonical spans are checked again before reuse.

The implementation uses 4,096-character windows with 512-character overlap, configurable up to 12,000 characters per window. Empty/whitespace-only messages are retained without detection calls. Nonblank messages have complete local character coverage. These windows do not establish a model-specific token budget; server truncation, cross-window identifiers and cross-message semantic detection remain gaps. Single-chunk requests see one message window; a batch contains several windows but does not guarantee complete turn/predecessor context or semantic continuity. The draft retains all selected predecessor context.

## Execution and artifact boundary

The worker is independent of live ingestion and application databases. `plan` reads local sources only. `prepare` uses normal Codex configuration/login unless an explicit private endpoint is selected, requires a new private run directory, checks a default 256-chunk execution budget, and makes sequential single-chunk calls. The optional batch API accepts at most 16 chunks and 12,000 total characters, requires every chunk exactly once, and rejects the batch if any result fails validation. The experimental pilot initially grouped the saved 1,088 chunks into 74 batches with four concurrent calls. After timeouts, it preserved 294 validated chunks and resumed the remaining work in 55 batches using exact-quote detection. Validated results were replayed through the deterministic draft builder. One contained path fragment at a chunk boundary was collapsed into the complete independently validated path, with a regression test and manifest count. Both detection contracts and all interrupted/failed attempts remain evidence; this mixed-contract pilot is not a controlled accuracy comparison. An optional expected source hash pins the operator's selection; every build records the initial source hash and checks it again before emitting a successful draft.

Each attempt gets an empty workspace, read-only sandbox and disabled configurable execution/browsing/delegation/MCP/plugins/hooks/telemetry. Normal mode uses the existing Codex home and its own credential management, preserving configured provider/service-tier settings while applying the worker's requested model and reasoning effort; credentials are never copied into artifacts. Explicit private mode uses a fresh home and isolated provider. Desktop pipes and unrelated credentials are excluded. Requests travel on stdin. Unknown CLI versions, tool/action events, incomplete or inconsistent final responses, timeouts and oversized output fail the build. One exact pre-turn diagnostic about intentionally disabled Code Mode is tolerated and retained; other errors still fail. A read-only tool sandbox does not enforce inference-server redirect or network-egress policy, and normal-mode inputs reach the configured Codex provider.

Private artifacts include the source/code/policy hashes, selection, lineage, per-chunk receipts, exact attempt inputs/outputs/events, mapping and a draft hash. New builds also record each bundled prompt's UTF-8 SHA-256 in `prompt_sha256`; exact composed instructions remain in each attempt's `instructions.txt`. Existing artifacts keep their original hashes and instructions. Owner-only permissions apply to the run directory and files. A successful manifest says `complete_draft` with `privacy_status=needs_review`. Caught failures record `failed` and remove draft output. A hard kill can leave running state or incomplete files; successful consumption requires both the completed manifest and matching draft hash. Resumption is not implemented.

The draft contains only opaque IDs, transformed role/content inputs and context, context status, unavailable target status and review flags. It still requires privacy/task review and must stay private. There is no public export or approval command. See the [artifact boundary](design.md#artifacts-and-row-contract) for the broader consumer/release design; the current draft is not that final release contract.

## Verification and confidence

| Evidence on 2026-10-04 | Result and limit |
| --- | --- |
| New source adapter and planner | All 1,915 reviewed rows validated locally; 1,885 eligible examples and 13,722 planned chunks. No model calls or changes to raw data. |
| Synthetic executable tests | 61 passing offline tests cover the subprocess adapter and draft builder using a fake Codex executable: environment isolation, flags, valid/invalid results, retry, timeout, action rejection, context reuse, replacements, source changes and overwrite refusal. These do not prove live CLI/provider compatibility. |
| Dependency independence | Copy the module into a separate directory and execute planning with Python site packages disabled. Runtime imports require no AgentBoard or Pydantic packages. |
| Live synthetic tests | Earlier private-endpoint preflight failed at discovery. After the user selected normal Codex, a two-chunk synthetic batch passed: a name/email was detected and a clean response returned no findings. This establishes basic default-login interoperability, not detector recall. |
| Requested 100-turn pilot | Selected 100 distinct eligible targets uniformly without replacement and saved the seed/indexes; replay reproduced the selection. Including predecessor context, the plan contains 1,070 unique messages and 1,088 chunks. The first attempt processed 0/100 because private discovery failed. Normal Codex subsequently processed all 100: **79** examples had text replacements (**72** target-turn changes, **7** context-only); **21** had no text replacement. There are **477** replacement spans in **184** unique messages. All drafts remain `needs_review`; source hash unchanged. Exact selection, attempts, per-sample outline and private before/after report are archived. |

**Confidence:** the custom adapter, local projection and controlled transformation path are covered offline, and normal Codex passed a synthetic interoperability preflight. Privacy recall, technical syntax preservation and retained task usefulness still require adjudication of the pilot and representative held-out examples. Human review, token-aware budgeting, reviewed dataset sealing/splits and public packaging remain separate implementation work.

**Defaults verification, 2026-10-11:** 67 offline privacy tests pass, including CLI defaults and explicit overrides, recorded model/effort, saved-login reuse and unchanged shared configuration. This configuration update made no live model requests and did not rerun the 100-sample pilot; live availability and detection quality for the new default remain unverified.

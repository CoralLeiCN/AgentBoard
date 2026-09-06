# Real Codex rollout excerpt

**Source-derived, redacted fixture — 2026-09-06.** [codex-real-excerpt.jsonl](codex-real-excerpt.jsonl) contains 13 selected records from an actual Codex Desktop session using CLI **0.153.4**. It captures the opening request and first tool operation from the AgentBoard event-inspector task. It was extracted from the local rollout, not authored as a simulated conversation.

The excerpt retains original record order, ordinals, timestamps, item durations, exit codes, token counts, the user request, assistant commentary, and command inputs. Session/call/item identifiers are consistently pseudonymized; local paths, command output, repository identity, and process IDs are redacted. Instruction metadata, account quota/credit status, timezone, and detailed local configuration are omitted. `payload.agentboard_fixture` was added to label the excerpt. JSON was reserialized. Thus raw export is exact for this **redacted fixture**, not byte-identical to the private original.

[Provenance](codex-real-excerpt.provenance.json) records the producing version, excerpt hash, source-line mapping, and changed or omitted field paths. The original prefix hash and local source locator are retained only in the ignored `.agentboard/real-excerpt-source.json`; private source content is not checked in. Original lines 3–7 (instructions, environment context, and world state) and all records after line 18 are omitted. This is a partial turn, not a complete conversation or representative performance benchmark.

| Excerpt line | Original line | Record |
| --- | --- | --- |
| 1 | 1 | Session metadata |
| 2 | 2 | Task started |
| 3 | 8 | Turn context |
| 4–5 | 9–10 | User message and completed user item |
| 6–7 | 11–12 | Recorded assistant item timing and assistant message |
| 8 | 13 | Outer `exec` custom tool call |
| 9 | 14 | Token usage record |
| 10–11 | 15–16 | Two `CommandExecution` completed items |
| 12 | 17 | Outer custom tool result |
| 13 | 18 | Token-count event |

Import into the isolated development dataset:

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-real-excerpt.jsonl
```

Then open session `real-codex-excerpt`. Raw JSONL line numbers refer to the excerpt file; use the table or provenance manifest for original rollout line numbers. The outer call maps to excerpt lines **8 and 12**. Each recorded command item maps to its own line, **10 or 11**. No parent relationship is invented for these items: the current matcher requires explicit supported identities. Normalization emits nine events; `token_usage_record` is currently retained only in the archive, and the completed user item does not emit a duplicate user event. Inferred intervals have no raw event record.

[Regression coverage](../../backend/tests/test_real_excerpt.py) verifies archive fidelity, direct record links, recorded nanosecond durations, call/result pairing, and unmapped-record retention. The synthetic `codex-session.jsonl` remains the stable fixture for existing examples and timing expectations. Pseudonymized session IDs cannot be used for native Codex continuation.

Privacy review **2026-09-07**: all retained text was reviewed, and regression checks reject account/configuration fields, native identifiers, private home paths, contact details, and common credential formats. The public fixture deliberately retains the reviewed feature request and commentary, repository-relative commands, timestamps, durations, and token counts. It is redacted, not anonymous; these checks are not a general secret-detection guarantee.

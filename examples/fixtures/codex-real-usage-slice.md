# Real Codex usage slice

Redacted fixture captured **2026-09-07** from the opening two turns of the local AgentBoard backlog discussion, recorded by Codex CLI **0.153.4** with model **gpt-6-astra**. This is a fixed, token-focused slice of a real rollout, not synthetic usage or a complete session/timeline.

[JSONL](codex-real-usage-slice.jsonl) contains 17 selected records from the first 55 physical source lines: session metadata, two model contexts, two reviewed user questions, six response usage records and six cumulative mirrors. [Provenance](codex-real-usage-slice.provenance.json) records the excerpt hash, source line/ordinal mapping and retained fields.

Original counters, model, timestamps, ordinals and retained order are preserved. Session/turn/response identifiers are consistently pseudonymized. Local paths, instructions, assistant/tool content, other prompts, configuration and quota/credit data are omitted. Fixture labeling is added to session metadata. The source rollout is never modified; tests require no local Codex home or live collector.

| Calculated value for this slice | Expected |
| --- | ---: |
| Recorded input, including cached input | 193,844 |
| Uncached input: input − cached input | 36,916 |
| Cached input | 156,928 |
| Cache writes, included in uncached input | 0 |
| Output, including reasoning | 809 |
| Total: input + output | 194,653 |
| Reasoning | 140 |
| Reasoning / total × 100 | 0.0719228576% (UI: 0.07%) |
| API equivalent at the dated Standard rates | $0.566538 |

The six response counts sum to the last recorded `thread_token_usage`. The last `event_msg/token_count` cumulative mirror resets to the second turn's usage, while the thread counter continues. [Backend regressions](../../backend/tests/test_real_usage.py) verify that response records win without adding mirrors, and that a deliberately legacy-only projection reports the observed reset as partial. They also cover archive reimport, pagination, export and weighted reasoning percentages. A deliberately modified copy omits response line 16 while retaining its summary; API/export then report a consistency warning and a priced subtotal. This tests missing-record detection, not an observed omission in the original rollout. [UI regressions](../../frontend/tests/usage.test.cjs) render the recorded thread total and last response. Synthetic tests remain for missing/invalid fields, positive cache writes, model changes and pricing thresholds absent from this slice.

Explicitly import into the isolated dev database:

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-real-usage-slice.jsonl
```

Open `#real-codex-usage-slice-v1` on the dev server. The UI labels it **REDACTED SESSION SLICE**. Timeline gaps are affected by omitted events; use it to verify token analysis, not complete session timing. This one format/version does not establish support for every Codex rollout.

# Independent GPT-6 Luna turn classification

Completed experiment, 2026-09-23. All **666 turns from 138 real user sessions** were classified using **GPT-6 Luna, low reasoning, one target per model call**, through the existing Codex OAuth login. This is a separate experiment; the production database, original eight-category rubric, earlier results and Sol reference answers were unchanged.

## Comparison with the batched run

The earlier experiment classified the same 666 turns in 39 GPT-6 Luna batches. The independent run agrees with those labels on **479/666 (71.92%)** and changes **187** labels. Cohen's kappa is 0.652003.

| Comparison to GPT-6 Sol extra-high reference | Matching turns | Agreement | Kappa |
| --- | ---: | ---: | ---: |
| Original batched Luna low | 468/666 | 70.27% | 0.639860 |
| Independent Luna low | 479/666 | 71.92% | 0.649079 |

Among changed labels, 84 move into agreement with Sol, 73 move out of agreement, and 30 change between two labels that both disagree with Sol. Sol remains a **model-generated reference, not human ground truth**.

| Category | Batched Luna | Independent Luna | Change |
| --- | ---: | ---: | ---: |
| writing | 68 | 52 | -16 |
| coding | 178 | 260 | +82 |
| bug-fixing | 42 | 49 | +7 |
| research | 68 | 79 | +11 |
| analysis | 45 | 25 | -20 |
| creative-media | 1 | 1 | +0 |
| guidance | 143 | 141 | -2 |
| other | 121 | 59 | -62 |

Of the original `other` labels, 70 move to a named category; 8 previously named categories move to `other`. Both runs used only the original eight categories shown above.

## Inputs and execution

Each saved prompt contains the original classification instructions followed by exactly one `turn_N` item. That item includes ordered `role`/`content` records for the target and its immediately preceding turn. All original input hashes were verified. The instruction prefix was preserved byte-for-byte, including its batch wording; only target membership and matching output-schema keys changed. Keys retain the original dataset indices.

Every call starts a fresh ephemeral Codex CLI 0.155.1 process with `gpt-6-luna`, `model_reasoning_effort="low"`, the OpenAI provider, ignored user configuration/rules, read-only sandbox and `/private/tmp` working directory. The first call verified output capture; remaining calls ran with up to six concurrent processes. Concurrent calls have no shared transcript context. No model tools were used. Raw prompts, schemas, responses and usage logs were retained privately.

Input includes normalized user and assistant text, including recorded reasoning summaries. Dedicated tool calls/results and system/developer events are excluded, although shell or review text embedded in a user message remains. Neither prior labels nor neighboring targets were shown. Empty targets were retained. Assistant-only turns remain distinct from new human requests.

There were **666 attempts for 666 completed calls**. Structurally valid answers are preserved without manual relabeling; empty-target rule violations are flagged rather than retried to select a preferred category. **0 answers have recorded prompt-compliance errors.**

## Usage and estimated cost

Usage is recorded **per target call and per attempt**, allowing actual per-turn totals rather than allocating a batch total. The following sum includes all attempts. Counts include Codex runtime input overhead as well as the classification prompt.

| Token measure | Tokens |
| --- | ---: |
| Input, including cache | 9,677,664 |
| Cached input (subset) | 34,304 |
| Cache writes (subset) | 0 |
| Output, including reasoning | 32,784 |
| Reasoning output (subset) | 0 |
| Input + output | 9,710,448 |

Estimated Standard API-equivalent cost is **$0.981071 USD**, using [official GPT-6 Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna), checked 2026-09-23: $0.10 uncached input, $0.01 cached input, $0.125 cache writes and $0.50 output per million tokens. Reasoning tokens are included in output; cache tokens are input subsets. No call exceeds the long-context pricing threshold. This is **not an observed OAuth charge**. Repeated runtime and instruction input can raise the total despite identical target/context text.

The original batched Luna run used 1,208,764 input and 23,162 output tokens, with an estimated API-equivalent cost of $0.130084. Independent calls used 8.01 times the input tokens and 7.54 times the estimated cost. This comparison includes all measured runtime overhead and caching differences.

## Interpretation and evidence

This comparison measures disagreement between one batched pass and one independent pass. It does **not** establish that batching caused each changed answer: sampling variation and provider-side changes remain possible. The higher agreement with Sol is agreement with a reference model rather than proven accuracy. Repeated controlled runs or human adjudication would be needed to attribute an effect or establish correctness. Different concurrency also prevents a controlled latency comparison.

Only this final report is versioned. Results and supporting artifacts remain in the ignored private archive `.agentboard/experiments/2026-09-23-turn-purpose-independent-luna/` in the main checkout. `aggregate-results.json` includes the category transition matrix. `comparison.json` joins each answer with its original session/turn identity, input hash, token usage, estimated cost, batched Luna label and Sol reference. `disagreements.json` records changed labels. `turns.json` retains full normalized inputs; `calls/` holds all rendered prompts, schemas, responses and logs. Raw rollout references and hashes point to the preserved original cohort archive.

Validation: all 666 unique original identities, one target per call, unchanged instruction/input hashes, schema-valid outputs, classifier logs without tool execution, token/cost arithmetic, category totals, comparison joins, archive/source hashes, document links and `git diff --check`. No application behavior changed, so application suites were not run. This is the explicitly requested OAuth classification experiment, separate from private-endpoint application integration tests.

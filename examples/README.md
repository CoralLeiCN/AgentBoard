# Runnable use cases

From the repository root:

```sh
uv sync --extra dev
uv run agentboard --config config/dev.toml serve
# In another terminal:
AGENTBOARD_URL=http://127.0.0.1:4319 uv run python examples/01_import.py
```

Numbered examples run independently, import synthetic fixtures idempotently, and use the frontend’s public API. Set `AGENTBOARD_URL` for the server and `AGENTBOARD_API_TOKEN` for authentication. The checked-in dev TOML overrides environment feature settings: to run every example, copy it to a temporary configuration, keep a separate database and port 4319, and add `otlp_logs`, `otlp_traces`, `classification`, `replay`, and `native_resume` to its feature array. Start the service with that configuration. Model and native continuation features are off by default; see the [feature catalog](../docs/features.md).

| Use case | Demo |
| --- | --- |
| Historical Codex import | [01_import.py](01_import.py); also `agentboard import /path/to/sessions` |
| Limited recent import | `agentboard --config config/dev.toml import examples/fixtures --limit 1`; [selection rules](../docs/specification.md#33-selecting-a-smaller-batch) |
| LLM versus tool time, including parallel calls | [02_analyze_timing.py](02_analyze_timing.py) |
| Native OpenTelemetry SDK export, asynchronous batching, protobuf | [03_opentelemetry.py](03_opentelemetry.py); live Codex configuration in [codex-otel.toml](codex-otel.toml) |
| External trace analysis via streamed API export | [04_external_analysis.py](04_external_analysis.py) |
| AI session purpose classification | [05_classify.py](05_classify.py); batch: `agentboard classify --all`; [configuration and taxonomy](../docs/session-purpose.md) |
| Resume transcript from the middle with edited input | [06_replay.py](06_replay.py) |
| Filter and extract human-attributed inputs | [07_user_inputs.py](07_user_inputs.py); attribution walkthrough below |
| Native Codex branch from a preceding completed turn | [08_native_codex_resume.py](08_native_codex_resume.py); prints a plan, because synthetic IDs are not native Codex sessions |
| Independent model worker/coding-session classification | [09_external_classification.py](09_external_classification.py) |
| Frontend visualization | Open [the dev UI](http://127.0.0.1:4319), choose **Load demo**, then Timeline / User inputs / All events |
| Configurable tracing | `agentboard features` lists the catalog; an empty allowlist keeps core browsing/UI, and `features = ["import"]` adds complete capture and normalized rollout import. See [configuration examples](../docs/features.md#configuration) |
| Retained capture inspection/reprocessing | `agentboard captures`; `agentboard capture-export ID > payload.bin`; `agentboard reprocess ID`; [semantics](../docs/data-lineage.md#35-raw-archive-and-export) |
| Feature startup and concurrent import/read probe | `uv run python examples/feature_benchmark.py --requests 20`; [measurement limits](../docs/architecture-review.md) |
| Reproducible storage efficiency probe | `uv run python examples/benchmark.py --events 10000` |

Models default to `http://localhost:30000/v1`; the OpenAI client discovers `/models` and calls Chat Completions by default. Set `AGENTBOARD_MODEL_API=responses` for a Responses endpoint; [live classification tests](../docs/session-purpose.md#live-responses-api-test) are opt-in. Unavailable-service results carry `dummy: true`, preserving API/store/replay checks. The dev config pins `model_mode = "dummy"`; set `model_mode = "local"` in the temporary configuration to require a model. `AGENTBOARD_MODEL_MODE` applies when TOML does not override it. The independent worker also reads its own environment settings. Tests verify real-client discovery/completion through mock HTTP without an external service.

A separate [real Codex excerpt](fixtures/codex-real-excerpt.md) contains 13 reviewed, redacted records from a local CLI 0.153.4 session, with original line mapping and preserved timings. Import it explicitly into the dev dataset to inspect recorded item lifetimes and custom tool calls. **Load demo** continues to load the synthetic fixture “Fix the checkout total rounding bug” (`demo-codex-checkout-usage-v1`).

[Real input-origin excerpts](fixtures/input-origin-real/README.md) add 62 redacted records from three actual CLI, Desktop, and guardian sessions. They preserve context wrappers, fragmented mirrors, and exact timing gaps while replacing all prose, identifiers, paths, and original activity times. Their manifest records the old/new importer comparison: nine counted inputs become three human-attributed inputs, and a 25-minute internal reviewer gap stops counting as a human wait. This is selected regression evidence, not a representative accuracy benchmark.

For a real example with **Waiting for user**, use `real-input-desktop`: it shows one estimated wait of **45.3 min**. The CLI and reviewer samples have none for different reasons; follow the [inspection walkthrough](fixtures/input-origin-real/README.md#inspect-locally). Between-turn waits are inferred from timestamps, without an explicit raw wait record.

The two-turn fixture includes overlapping tools, a failed test, a patch, and a passing suite. Expected tool sum: **17,500 ms**; active union: **16,400 ms**; LLM gap estimate: **38,380 ms**; between-turn wait: **31,900 ms**. Its command text is never executed. The UI packages an identical copy.

The native resume example prints a reviewable plan. For an actual continuation, import a real local Codex session and run:

```sh
uv run agentboard export SESSION_ID --inputs-only
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Your replacement' --cwd /your/repo --execute
```

This makes a new read-only Codex branch; it does not restore the repository to its historical state. A local model replay is a text continuation and cannot substitute for native Codex execution.

For exact Codex source export after importing a file, run `agentboard export SESSION_ID --raw > rollout.jsonl`. List source versions with `GET /api/v1/sessions/SESSION_ID/raw-imports`, then select one using `--raw --import-id ID`. Older imports require reimporting their original files; normalized event export cannot recover omitted source fields.

The synthetic demo also exposes explicit parallel labels: `GET /api/v1/sessions/demo-codex-checkout/parallel-groups?source=codex_jsonl` returns one two-tool group with peak concurrency 2 and `overlap_ms=1100`. Open Timeline to inspect **Parallel P1**, then filter for `cat src` to see its partial-membership label.

To inspect input attribution using synthetic data in the isolated dev dashboard:

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-context.jsonl examples/fixtures/codex-reviewer.jsonl
uv run agentboard --config config/dev.toml serve
```

Open [the dev dashboard](http://127.0.0.1:4319) in Codex’s internal browser. `demo-context-inputs` has **2 human-attributed prompts**, **2 injected context records** under All events, and **6,000 ms** inferred waiting time. The context at second 4 does not end the wait from second 3 to the human prompt at second 9. `demo-internal-reviewer` has **0 human prompts/waits**, keeps its context and internal requests inspectable, and links to `demo-context-inputs` as its recorded parent. Branch controls appear only on the human-attributed inputs. These classifications are inferred; [coverage and reimport limits](../docs/data-lineage.md#341-input-attribution) apply.

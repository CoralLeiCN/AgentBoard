# Runnable use cases

From the repository root:

```sh
uv sync --extra dev
uv run agentboard serve
# In another terminal:
uv run python examples/01_import.py
```

Numbered examples run independently, import synthetic fixtures idempotently, and use the frontend’s public API. Set `AGENTBOARD_URL` for the server and `AGENTBOARD_API_TOKEN` for authentication. Model examples need the default `classification,replay` features enabled.

| Use case | Demo |
| --- | --- |
| Historical Codex import | [01_import.py](01_import.py); also `agentboard import /path/to/sessions` |
| LLM versus tool time, including parallel calls | [02_analyze_timing.py](02_analyze_timing.py) |
| Native OpenTelemetry SDK export, asynchronous batching, protobuf | [03_opentelemetry.py](03_opentelemetry.py); live Codex configuration in [codex-otel.toml](codex-otel.toml) |
| External trace analysis via streamed API export | [04_external_analysis.py](04_external_analysis.py) |
| AI session classification | [05_classify.py](05_classify.py) |
| Resume transcript from the middle with edited input | [06_replay.py](06_replay.py) |
| Filter and extract every user input | [07_user_inputs.py](07_user_inputs.py) |
| Native Codex branch from a preceding completed turn | [08_native_codex_resume.py](08_native_codex_resume.py); prints a plan, because synthetic IDs are not native Codex sessions |
| Independent model worker/coding-session classification | [09_external_classification.py](09_external_classification.py) |
| Frontend visualization | Open [the UI](http://127.0.0.1:4318), choose **Load demo**, then Timeline / User inputs / All events |
| Optional plugin | Start with `AGENTBOARD_PLUGINS=examples.extension uv run agentboard serve`; fetch `/api/v1/extensions/example` |
| Minimal tracing-only configuration | Start with `AGENTBOARD_FEATURES= uv run agentboard serve`; classification/replay return 404 and UI controls disappear |
| Reproducible storage efficiency probe | `uv run python examples/benchmark.py --events 10000` |

Models default to `http://localhost:30000/v1`; the OpenAI client discovers `/models` and calls Chat Completions. Unavailable-service results carry `dummy: true`, preserving API/store/replay checks. Set server `AGENTBOARD_MODEL_MODE=dummy` to force fallback or `local` to require a model. The independent worker also reads its own environment settings. Tests verify real-client discovery/completion through mock HTTP without an external service.

The two-turn fixture includes overlapping tools, a failed test, a patch, and a passing suite. Expected tool sum: **17,500 ms**; active union: **16,400 ms**; LLM gap estimate: **38,380 ms**; between-turn wait: **31,900 ms**. Its command text is never executed. The UI packages an identical copy.

The native resume example prints a reviewable plan. For an actual continuation, import a real local Codex session and run:

```sh
uv run agentboard export SESSION_ID --inputs-only
uv run agentboard resume SESSION_ID INPUT_ID --prompt 'Your replacement' --cwd /your/repo --execute
```

This makes a new read-only Codex branch; it does not restore the repository to its historical state. A local model replay is a text continuation and cannot substitute for native Codex execution.

For exact Codex source export after importing a file, run `agentboard export SESSION_ID --raw > rollout.jsonl`. List source versions with `GET /api/v1/sessions/SESSION_ID/raw-imports`, then select one using `--raw --import-id ID`. Older imports require reimporting their original files; normalized event export cannot recover omitted source fields.

The synthetic demo also exposes explicit parallel labels: `GET /api/v1/sessions/demo-codex-checkout/parallel-groups?source=codex_jsonl` returns one two-tool group with peak concurrency 2 and `overlap_ms=1100`. Open Timeline to inspect **Parallel P1**, then filter for `cat src` to see its partial-membership label.

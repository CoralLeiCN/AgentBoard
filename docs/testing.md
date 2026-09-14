# Developer testing guide

Use this guide to choose, run and report checks when developing a feature or fixing a bug. It is the primary testing workflow; [AGENTS.md](../AGENTS.md) gives agents the short instruction. The commands below use the existing test suites; they do not configure CI or establish a new test result.

## Prepare the environment

Run commands from the repository root. Use Python 3.11 or newer, `uv`, and Node.js with its built-in `node:test` runner. Node is needed for frontend tests, not to run AgentBoard; there is no npm dependency installation. Python dependencies and pytest configuration are in [pyproject.toml](../pyproject.toml).

In each worktree, complete [the worktree setup](development.md#automatic-worktree-data-setup) before development or UI verification. It runs `uv sync --locked --extra dev`, preserves an existing dev database, and snapshots the main checkout's fixed baseline when available. The development guide owns the setup, checkpoint and recovery commands.

Backend tests use temporary databases, fixtures, mocks, dummy models and a fake app-server. They do not need a running dev server or the real baseline. Use synthetic data for new committed tests. Complete real sessions and their provenance stay in ignored local `.agentboard/` storage; never commit or upload them. Inspecting that dataset makes no model request.

## Develop with focused tests

Write a regression for the changed behavior, including relevant failure cases; a bug regression should reproduce the original failure. Test observable results rather than copying implementation details. Start with the affected tests, for example:

```sh
uv run --extra dev pytest -m "not e2e" backend/tests/test_capture.py backend/tests/test_features.py -q
node --test frontend/tests/features.test.cjs
```

Use `-k` to select a test by name while iterating. Choose additional coverage according to the change:

| Change | Checks to add or update |
| --- | --- |
| Parsing, capture or storage | Expected normalized output, unknown/malformed inputs, complete raw retention, retries/reimports and preservation across affected migrations. See [capture](../backend/tests/test_capture.py) and [reimport tests](../backend/tests/test_feature_reimports.py). |
| Optional feature or API/CLI behavior | Enabled and disabled configurations, required dependencies, route/OpenAPI/CLI availability, and retained data. See [feature coverage](features.md#verification). |
| Frontend behavior | Rendering, state and requests in [Node tests](../frontend/tests/), plus the affected flow in the [browser](#browser-checks). For feature controls, verify disabled endpoints are not requested. |
| Model or native continuation integration | Mock/dummy/fake-protocol regressions, plus relevant [private model tests](#private-model-tests) when real execution is needed. Native execution needs an extension to that isolated harness. |
| Codex CLI upgrade or app-server integration | [Schema compatibility checks](#schema-and-performance-checks), plus affected integration regressions. |
| Documentation only | Verify changed links, examples and claims against their source, and run `git diff --check`; application suites are unnecessary unless behavior also changes. |

## Routine checks before handoff

After code changes, run the complete offline backend and frontend suites, Python lint and whitespace checks:

```sh
uv run --extra dev pytest -m "not e2e" -q
node --test frontend/tests/*.test.cjs
uv run --extra dev ruff check backend examples scripts
git diff --check
```

| Check | Coverage and limits |
| --- | --- |
| Backend pytest | Unit and integration coverage for imports, storage, API/CLI behavior, telemetry, timing, usage, lineage, model failures and native protocol. The [shared fixtures](../backend/tests/conftest.py) use a temporary database and dummy model for API tests. Stored app-server schema integrity is also verified offline. |
| Frontend Node tests | JavaScript rendering and interaction logic, including simulated DOM and HTTP responses. They do not launch a browser or verify visual layout. The wildcard runs all test files. |
| Ruff | Python lint and import checks; it does not establish runtime correctness. |
| Git diff check | Whitespace errors in tracked changes; review new files as well. |

`-m "not e2e"` explicitly deselects live tests. Plain `pytest -q` normally skips them, but exporting an `AGENTBOARD_E2E_CODEX_*` or `AGENTBOARD_E2E_CLASSIFICATION_*` base URL, model or key also opts the corresponding suite into live execution. Prefer explicit selection for predictable offline runs.

The repository currently has no checked-in GitHub Actions workflow; run these checks locally. Passing tests establish only the behavior they cover.

## Browser checks

After worktree setup, start the isolated dev server:

```sh
uv run agentboard --config config/dev.toml serve
```

Open [the dev dashboard](http://127.0.0.1:4319) in Codex's internal browser (`iab`). Do not use personal Chrome or another external browser unless the user explicitly requests it. GitHub authentication and PR workflows use the user's system default browser; this restriction applies to AgentBoard UI testing.

Confirm the **DEV** label and intended database, then use **Load demo** or the isolated dataset. Exercise the affected flow from input to visible result, its relevant empty/error states, persistence after refresh where applicable, and desktop/narrow layouts for layout changes. For optional capabilities, check enabled and disabled profiles, including controls and network requests. Report any part you could not observe, such as a download reaching the API without a verified saved file.

The [dev configuration](../config/dev.toml) pins port 4319, `.agentboard/dev.db`, dummy model mode, and tracing features without live telemetry receivers. Leave the live collector and Codex exporters on 4318. Use `serve --port 4320` if another worktree uses 4319. Never serve from the shared baseline or a checkpoint; checkpoint an existing dev database before migration or replacement.

Classification, replay and native resume are excluded from the dev allowlist. For their UI flows, use a separate temporary configuration based on the dev profile, adding the required [features and dependencies](features.md#feature-list), and keep model mode dummy. Dummy mode applies to classification/replay; it does not isolate actual native Codex execution. Plan generation and fake-protocol tests can run without a model; real execution must follow the private harness below. Receiver experiments likewise need a separate configuration and controlled synthetic requests. See [configuration boundaries](development.md#configuration-and-boundaries).

Python changes under `backend/agentboard/` reload automatically. Refresh the browser after frontend changes; restart the server after TOML or environment changes.

## Private model tests

Every test that invokes Codex or another model must use **`http://192.168.1.220:30000/v1`**. Run live checks explicitly when validating model integration; they are not required for every routine change:

```sh
uv run --extra dev pytest --run-private-e2e -m e2e -q
```

This runs the [Codex telemetry test](../backend/tests/test_codex_endpoint_e2e.py) and [synthetic classification tests](../backend/tests/test_classification_endpoint_e2e.py). The Codex test requires `codex` on `PATH`. Mark new live tests `pytest.mark.e2e`, use the [private_endpoint fixture](../backend/tests/conftest.py), and use synthetic prompts instead of shared real sessions.

The [shared endpoint policy](../scripts/private_endpoint.py) rejects any different base URL before a model request or Codex process. Discovery requests only the private `/models`, disables proxies/redirects, and selects automatically only when exactly one model is advertised. Pin a model with `AGENTBOARD_E2E_CODEX_MODEL` or `AGENTBOARD_E2E_CLASSIFICATION_MODEL` when needed. The matching `*_API_KEY` is optional; credentials stay out of process arguments. [example.env](../example.env) lists the settings. As noted above, exporting a suite's endpoint/model/key settings also opts it into live testing unless `e2e` is deselected.

The endpoint must support streaming Responses for Codex and structured Responses for classification. The Codex test requests one short synthetic response, disables retries, and checks the final message plus ingested `otlp_log`, `otlp_trace` and normalized `llm` events. Its temporary Codex home ignores user configuration, OAuth state and execution-policy rules; tools inherit no environment and execution is read-only without approvals. The receiver/database are temporary and separate from ports 4318/4319 and shared real sessions.

The tests clear inherited proxy and hosted OpenAI environment settings. The Codex child inherits only runtime essentials and the optional private-provider key, excluding desktop app pipes/session IDs. It pins `RUST_LOG=info` for consistent log filtering; parent configuration is unchanged. Child stdout/stderr diagnostics stay in the temporary test directory.

If the private service is unreachable, incompatible or ambiguous, report a failure and fix the configuration. Never substitute OpenAI, ChatGPT login, another provider or a paid model. Do not use `agentboard resume --execute` with normal user Codex configuration as a test; extend the isolated private harness if native resume needs live coverage. The dev dashboard stays in dummy mode. [Offline policy tests](../backend/tests/test_private_endpoint.py) verify rejection and discovery behavior, not service availability. Simple live classification cases do not establish general classification accuracy.

## Schema and performance checks

When upgrading the Codex CLI used by AgentBoard or changing app-server integration, run against the intended executable:

```sh
python scripts/codex_schemas.py check --codex /absolute/path/to/codex
```

Review drift and follow [the schema workflow](../schemas/README.md#codex-upgrade-workflow) before updating the baseline; never hand-edit generated schemas. The routine suite checks stored schema integrity offline. App-server schemas are not a contract for raw rollout JSONL; review affected rollout fixtures separately.

For storage or performance changes, use the relevant synthetic probe to compare the same workload and environment before and after:

```sh
uv run python examples/benchmark.py --events 10000
uv run --extra dev python examples/feature_benchmark.py --requests 20
```

Both probes use temporary databases. Record workload, environment and source revision with measurements; these probes have no universal pass threshold and do not establish production capacity. See [measurement evidence and limits](architecture-review.md#measurements).

## Report verification

Before handoff, record the commands run and their results, the browser flows/configuration checked, and any skipped, failed or blocked checks with reasons. Distinguish dummy or fake-protocol coverage from actual model execution. If a later change affects an earlier check, rerun the relevant checks. Link any unresolved correctness issue to the [gap register](data-quality-gaps.md).

Keep this guide about the repeatable workflow. Record dated results alongside the relevant change, as in [the architecture review](architecture-review.md#verification), rather than treating an earlier passing run as evidence for new code.

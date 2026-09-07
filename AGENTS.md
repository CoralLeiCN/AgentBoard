# Project documentation

When creating or updating documentation, follow [the documentation rules](docs/documentation-guide.md). Keep changes concise, evidence-based, and explicit about current behavior, proposals, and correctness gaps.

# Codex schema workflow

When upgrading the Codex CLI used by AgentBoard or changing its app-server integration, follow [the schema workflow](schemas/README.md). Run `python scripts/codex_schemas.py check` against the intended executable and review any drift before updating the baseline. Never hand-edit generated schemas. Regular tests verify the stored baseline offline. App-server schemas must not be used as a contract for raw rollout JSONL.

# Browser testing

When testing the app UI, use Codex’s internal in-app browser (`iab`). Do not use the user’s personal Chrome or another external browser unless explicitly requested.

# GitHub workflows

For GitHub authentication and pull-request workflows, use the user's system default browser, not Codex's internal in-app browser. The internal-browser requirement above applies only to AgentBoard UI testing.

# Isolated development data

In **each worktree**, run the setup commands in [the shared data workflow](docs/development.md#automatic-worktree-data-setup) before development, debugging, or UI verification. Use the main checkout's `.agentboard/baseline.db` as the fixed shared source and an independent `.agentboard/dev.db` in each worktree. Preserve existing dev databases; checkpoint before replacing or migrating them. Never symlink to, serve from, or run writable tests against the baseline or a checkpoint.

Select complete real Codex rollouts for local development with `uv run python scripts/seed_dev_data.py /absolute/path/to/rollout.jsonl ...`. This creates the baseline and its checkpoint once. Keep **every source line and event**, including unknown record types, internal inputs, tool outputs, and compaction records; do not trim or redact this local dataset. The database includes the full raw archives and local selection provenance. Real session data, database copies, checkpoints, and manifests stay in ignored `.agentboard/` storage. Never commit, push, upload, or paste these data into docs, fixtures, issues, or PRs. Use synthetic data for committed tests; leave the existing reviewed redacted fixtures separate.

Serve with `uv run agentboard --config config/dev.toml serve` on port 4319 (use `--port` to avoid another worktree's server). Keep existing Codex exporters on port 4318; routine development must not mutate the live collector's database or receive continuous telemetry. Use Codex's internal browser for the dev URL.

# Private model tests

All tests that actually invoke Codex or another model must use **`http://192.168.1.220:30000/v1`**. Use `uv run --extra dev pytest --run-private-e2e -m e2e -q` and [the private endpoint workflow](docs/development.md#private-model-tests). The shared harness validates the endpoint, discovers a single advertised model (or uses an explicitly configured model ID), and isolates the child Codex home, auth, telemetry, and database. An unavailable endpoint is a failure; never fall back to OpenAI, ChatGPT login, another provider, or a paid model. Do not run `agentboard resume --execute` with the user's normal Codex configuration as a test; extend the isolated private harness if native resume needs live coverage.

Normal unit tests use mocks, synthetic fixtures, dummy models, or a fake app-server and need no model request. Keep new live tests behind the opt-in flag and use the `private_endpoint` fixture. Use synthetic prompts for live model checks; importing/inspecting the shared real dataset requires no model call.

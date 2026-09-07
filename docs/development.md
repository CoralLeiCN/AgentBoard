# Isolated development

Implemented 2026-09-06. Use the dev endpoint and a separate dataset for UI testing, debugging and data audits.

| Mode | Endpoint | Database | Live OTel ingestion |
| --- | --- | --- | --- |
| Existing local collector | `http://127.0.0.1:4318` | `agentboard.db` by default | Enabled |
| [Dev config](../config/dev.toml) | `http://127.0.0.1:4319` | `.agentboard/dev.db` | Disabled; `/v1/logs` and `/v1/traces` return 403 |

Codex's existing log and trace exporter destinations stay on port 4318. Starting a dev server does not change Codex configuration or redirect the running Codex process. The live collector can continue receiving telemetry while the dev dataset stays fixed. Codex supports separate configured log and trace exporters; see the [official telemetry documentation](https://developers.openai.com/codex/config-advanced#observability-and-telemetry).

## Start with fixtures

```sh
uv run agentboard --config config/dev.toml serve
```

Open [the dev dashboard](http://127.0.0.1:4319) in Codex's internal browser. The page shows **DEV** and the database name. Use **Load demo** or explicitly import a fixture:

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-session.jsonl
```

For token usage verification, import the [redacted real Codex slice](../examples/fixtures/codex-real-usage-slice.md):

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-real-usage-slice.jsonl
```

It preserves recorded usage from six responses, including cached input and reasoning. It is a fixed partial session with documented redactions; synthetic fixtures still cover unsupported edge cases.

Opening `frontend/index.html` as a local file redirects to `http://localhost:4319`, preserving the session fragment. Start the dev server first: styles, scripts and API routes are served together and require HTTP. If a dev server outlives a deleted worktree, stop that stale process and start the server from the current checkout; its old dataset may no longer be available.

## Debug a fixed copy of real data

Before starting dev against a new database:

```sh
uv run agentboard --config config/dev.toml snapshot --source agentboard.db
uv run agentboard --config config/dev.toml serve
```

The snapshot command opens the source read-only and uses SQLite's backup API, including committed WAL data. It does not instantiate the source `Store`, migrate it, or reassign its events. The destination must be new; existing databases and manifests are never overwritten. Subsequent live writes do not appear in the snapshot.

A sibling `dev.db.snapshot.json` records source/destination paths, creation time, row counts and the database SHA-256 at creation. The snapshot can subsequently change through explicit imports, schema migrations or other dev actions; the manifest describes its initial state. Snapshot files and manifests are ignored by Git.

For another snapshot, choose a new destination explicitly:

```sh
uv run agentboard --config config/dev.toml --database .agentboard/review-2.db snapshot --source agentboard.db
uv run agentboard --config config/dev.toml --database .agentboard/review-2.db serve
```

## Automatic worktree data setup

Workflow assumption (2026-09-07): new worktrees for parallel agent work are created from the main checkout. That checkout can hold a shared `.agentboard/baseline.db`; when present, each worktree gets its own writable `.agentboard/dev.db` copy.

Implemented 2026-09-07. The [Codex environment](../.codex/environments/environment.toml) installs dependencies, then snapshots the shared baseline when available through an inline shell step. Codex runs the selected environment's setup script when creating a worktree; see [local environments](https://learn.chatgpt.com/docs/environments/local-environment).

To seed worktrees with a shared dataset, create `.agentboard/baseline.db` once in the main checkout from an explicitly chosen dataset. For example, from the main checkout, with its dev server stopped:

```sh
uv run agentboard --config config/dev.toml --database .agentboard/baseline.db snapshot --source .agentboard/dev.db
```

Setup uses `git rev-parse --path-format=absolute --git-common-dir` to locate the shared `.git` directory, then takes its parent as the main checkout. No username or repository location is hardcoded, and paths containing spaces are supported. It snapshots the baseline into the current checkout's `.agentboard/dev.db`. Each copy is writable and independent; the source is opened read-only. The existing `.agentboard/` Git ignore rule covers the baseline, copies and manifests. Keep the baseline fixed while comparing branches.

Existing dev databases are preserved. If the baseline is absent, setup prints a notice and succeeds without creating a database or manifest; [start with fixtures](#start-with-fixtures) to load dev data. Present but invalid baselines (including broken symlinks) and snapshot failures still fail setup. After creating the baseline, rerun the environment setup for existing worktrees; this does not replace an existing database or refresh it with later baseline changes.

Commit the environment and include it in the branch used to create future worktrees. This assumes a normal clone with `.git` inside the main checkout; bare repositories and separately located Git directories are unsupported. Setup initializes data only; the dev server still uses port 4319, so simultaneous servers require a separate port-policy change.

Verification: [setup tests](../backend/tests/test_worktree_setup.py) run the environment's shell script against temporary Git worktrees and synthetic SQLite data to check discovery, independent writable copies, reruns, skipping absent baselines, adding a baseline later and rejecting invalid databases. Dependency installation is bypassed in these tests.

## Configuration and boundaries

`--config` works with every CLI command. Precedence is CLI overrides, explicit TOML values, environment defaults, then built-in defaults. TOML database paths resolve relative to the config file; CLI `--database` paths resolve relative to the working directory. Unknown keys and invalid types are rejected. The dev file pins its database, port, dummy model mode and empty plugin list, so inherited live database/model/plugin settings do not override them.

Dev mode blocks automatic OTel uploads but permits explicit imports and normal application actions. For an OTLP receiver experiment, use a separate temporary test configuration with `otlp_enabled=true` and controlled synthetic requests; do not point the global Codex exporters at it.

The dev config enables `reload = true`: Python changes under `backend/agentboard/` automatically restart the backend. Each worker retains the resolved config and CLI overrides, including the dev database and disabled telemetry ingestion. Refresh the internal browser after UI changes. Restart the command after TOML or environment changes; these settings are captured at startup. Database writes do not trigger reloads. The default collector has reload disabled.

This config isolates endpoints and data; it does not freeze another process's Python code. Run a persistent live collector from a stable checkout or installation, without development auto-reload. Do not replace or restart the collector as part of routine dev testing.

Verification: [dev configuration tests](../backend/tests/test_dev_config.py) cover default endpoint compatibility, config precedence, CLI port selection, rejected live uploads, explicit imports, snapshot WAL consistency and refusal to overwrite existing data.

## Optional real Responses endpoint test

The normal suite uses fixtures, mocks, a dummy model, and a fake Codex app-server. To verify the complete model-request-to-telemetry path, [the opt-in test](../backend/tests/test_codex_endpoint_e2e.py) launches an ephemeral Codex process with a custom Responses API provider and points its log and trace exporters at a temporary AgentBoard receiver. Start from the repository's [`example.env`](../example.env); `.env` is ignored by Git.

```sh
cp example.env .env
# Edit .env before continuing.
set -a
. ./.env
set +a
uv run --extra dev pytest -m e2e backend/tests/test_codex_endpoint_e2e.py -q
```

`AGENTBOARD_E2E_CODEX_API_KEY` is optional. When present, Codex reads it from the child process environment and sends it as the provider credential; the test never includes the value in command arguments. The endpoint must support Codex's streaming Responses API wire format. The test disables request and stream retries, requests one short response, and asserts a successful final message plus ingested `otlp_log`, `otlp_trace`, and normalized `llm` events.

The test skips when both required variables are absent and fails clearly when only one is set. It gives the child process a temporary Codex home, ignores user configuration and execution-policy rules, prevents shell tools from inheriting its environment, and runs read-only without approvals. It therefore cannot use stored OpenAI OAuth state and uses neither the development service on port 4319 nor the normal collector/database on port 4318. The operator owns availability, data handling, and cost for the configured endpoint.

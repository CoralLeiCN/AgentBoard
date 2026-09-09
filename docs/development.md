# Isolated development

Implemented 2026-09-06. Use the dev endpoint and a separate dataset for UI testing, debugging and data audits.

| Mode | Endpoint | Database | Live OTel ingestion |
| --- | --- | --- | --- |
| Existing local collector | `http://127.0.0.1:4318` | `agentboard.db` by default | Enabled |
| [Dev config](../config/dev.toml) | `http://127.0.0.1:4319` | `.agentboard/dev.db` | Disabled; `/v1/logs` and `/v1/traces` are absent (404) |

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

A sibling `dev.db.snapshot.json` records source/destination paths, creation time, counts of sessions/events/raw imports/raw lines, and the database SHA-256 at creation. The snapshot can subsequently change through explicit imports, schema migrations or other dev actions; the manifest describes its initial state. Database snapshots are created with owner-only permissions. Snapshot files and manifests are ignored by Git.

For another snapshot, choose a new destination explicitly:

```sh
uv run agentboard --config config/dev.toml --database .agentboard/review-2.db snapshot --source agentboard.db
uv run agentboard --config config/dev.toml --database .agentboard/review-2.db serve
```

## Automatic worktree data setup

Implemented 2026-09-07. The main checkout holds the fixed shared `.agentboard/baseline.db`; each worktree gets its own writable `.agentboard/dev.db`. Share the starting dataset, not a writable SQLite file. Keep the main checkout and its `.agentboard/` directory on this machine. A separate clone or host needs its own local setup.

**Create the baseline once.** Select a few completed real sessions from local Codex `sessions/` or `archived_sessions/`, covering multiple turns, tool use, usage records, and internal/subagent activity. Pass complete files explicitly:

```text
uv run python scripts/seed_dev_data.py /absolute/path/to/rollout-one.jsonl /absolute/path/to/rollout-two.jsonl
```

The [seed script](../scripts/seed_dev_data.py) imports complete files through core capture and normalization with field lineage enabled, verifies SHA-256 equality between each source and its raw export, checks SQLite integrity, creates a timestamped recovery database under the main checkout's `.agentboard/checkpoints/`, then snapshots that checkpoint to `baseline.db`. Captured payloads and attempt outcomes survive in both copies. Source paths and hashes live in the database's `dev_seed_sources` table. It refuses existing destinations and publishes no baseline after a failed import. A changed source fails verification; select fixed completed files. The checkpoint is a separate local recovery copy, not protection against loss of the disk.

**Retain all local evidence.** Preserve every line, message, tool output, unknown event, internal input, and compaction/rollback record in the selected files. Normalized events cover recognized mappings; the complete `raw_lines` archive retains unsupported records too. Do not cap events, take excerpts, redact, summarize, or delete history to reduce this dataset. This preserves the complete available rollout file, not unrecorded upstream model state or separate telemetry that was never imported. Seeding and snapshotting make no model request.

Databases, archives, checkpoint copies and manifests are local only under ignored `.agentboard/`. Do not add real rollouts to the repository, force-add ignored files, upload them, or paste their contents/identifiers into docs, PRs or issues. Existing reviewed redacted fixtures are separate; new committed regression tests should use synthetic data.

**In every worktree**, run the selected [Codex environment](../.codex/environments/environment.toml), which installs dependencies and snapshots an available baseline only when the local dev database is absent. Codex runs the selected environment during worktree creation; see [local environments](https://learn.chatgpt.com/docs/environments/local-environment). The equivalent manual commands, from that worktree's root, are:

```sh
set -e
uv sync --locked --extra dev

if [ ! -e .agentboard/dev.db ]; then
  agentboard_git_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
  agentboard_main_root="$(dirname "$agentboard_git_dir")"
  agentboard_baseline="$agentboard_main_root/.agentboard/baseline.db"
  if [ -e "$agentboard_baseline" ] || [ -L "$agentboard_baseline" ]; then
    uv run agentboard --config config/dev.toml --database .agentboard/dev.db snapshot \
      --source "$agentboard_baseline"
  else
    printf 'Skipping dev database snapshot: no baseline at %s.\n' "$agentboard_baseline"
    printf 'Start the dev server and use Load demo, or import a fixture (see docs/development.md).\n'
  fi
fi
uv run agentboard --config config/dev.toml serve
```

Git discovery works from main and linked worktrees, including paths with spaces. It assumes a normal clone with `.git` inside the main checkout; bare repositories and separately located Git directories are unsupported. If the baseline is absent, setup prints a notice and succeeds without a database or manifest; use fixtures or create the baseline and rerun setup before comparing branches with shared data. Present but invalid baselines (including broken symlinks) and snapshot failures still fail setup. Never symlink the dev database to the baseline or serve from a checkpoint. Existing dev databases and their edits are preserved; setup does not automatically refresh them. Check their `.snapshot.json` provenance before assuming they use the current baseline. Include the environment and these instructions in the branch used to create worktrees. Simultaneous dev servers need distinct ports, e.g. `serve --port 4320`.

**Checkpoint and recover.** Before replacing a dataset, changing its schema, or doing destructive experiments, use `snapshot --source` with a new `.agentboard/checkpoints/<name>.db` destination. Keep the initial seed checkpoint fixed. To return to it, restore into a new writable file:

```text
uv run agentboard --config config/dev.toml --database .agentboard/restored.db snapshot --source /absolute/path/to/main/.agentboard/checkpoints/baseline-TIMESTAMP.db
uv run agentboard --config config/dev.toml --database .agentboard/restored.db serve
```

Do not overwrite or delete an existing dev database as a setup shortcut. For a deliberate refresh of `.agentboard/dev.db`, stop its dev server, checkpoint it, move that database and its manifest aside, then rerun setup. Never manipulate WAL/SHM files while a writer is running. Keep the live collector on 4318 separate.

Verification: [worktree tests](../backend/tests/test_worktree_setup.py) cover actual setup in temporary Git worktrees, independent copies, reruns, skipped absent baselines, adding a baseline later, invalid baselines, and preserved local edits. [Seed tests](../backend/tests/test_seed_dev_data.py) cover full raw round-trips (including unknown records and line endings), checkpoint restoration without source files, partial-import failure, and overwrite refusal. All committed test data are synthetic.

## Configuration and boundaries

`--config` works with every CLI command. Precedence is CLI overrides, explicit TOML values, environment defaults, then built-in defaults. TOML database paths resolve relative to the config file; CLI `--database` paths resolve relative to the working directory. Unknown keys and invalid types are rejected. The dev file pins its database, port, tracing feature allowlist, and dummy model mode, so inherited feature/model settings do not enable live receivers or model calls. Legacy plugin configuration is rejected; remove it before starting. The [feature catalog](features.md) describes the allowlist and dependencies.

The dev allowlist excludes `otlp_logs` and `otlp_traces` but enables explicit imports and tracing analysis. For a receiver experiment, use a separate temporary test configuration whose feature list includes the required OTLP signal and send controlled synthetic requests; do not point the global Codex exporters at it. To test classification or continuations, add the relevant feature and its dependencies to that temporary configuration.

The dev config enables `reload = true`: Python changes under `backend/agentboard/` automatically restart the backend. Each worker retains the resolved config and CLI overrides, including the dev database and disabled telemetry ingestion. Refresh the internal browser after UI changes. Restart the command after TOML or environment changes; these settings are captured at startup. Database writes do not trigger reloads. The default collector has reload disabled.

This config isolates endpoints and data; it does not freeze another process's Python code. Run a persistent live collector from a stable checkout or installation, without development auto-reload. Do not replace or restart the collector as part of routine dev testing.

Verification: [dev configuration tests](../backend/tests/test_dev_config.py) cover default endpoint compatibility, config precedence, CLI port selection, rejected live uploads, explicit imports, snapshot WAL consistency and refusal to overwrite existing data.

## Private model tests

Implemented 2026-09-07. Every live Codex/model test uses **`http://192.168.1.220:30000/v1`**. The normal suite uses fixtures, mocks, a dummy model, and a fake Codex app-server. Run live checks explicitly:

```sh
uv run --extra dev pytest --run-private-e2e -m e2e -q
```

This runs the [Codex telemetry test](../backend/tests/test_codex_endpoint_e2e.py) and [synthetic classification tests](../backend/tests/test_classification_endpoint_e2e.py). The [shared policy](../scripts/private_endpoint.py) rejects any different base URL before a model request or Codex process. Discovery requests only the private `/models`, disables proxies/redirects, and auto-selects only if exactly one model is advertised. Pin a model with `AGENTBOARD_E2E_CODEX_MODEL` or `AGENTBOARD_E2E_CLASSIFICATION_MODEL` when needed. The matching `*_API_KEY` is optional; credentials remain in the child environment, never process arguments. [`example.env`](../example.env) lists these settings. Exporting endpoint/model/key settings also opts that suite into live testing; leave them unset for the normal offline suite.

The endpoint must support streaming Responses for Codex and the Responses structured output used by classification. The Codex test requests one short synthetic response, disables retries, and checks the final message plus ingested `otlp_log`, `otlp_trace`, and normalized `llm` events. Its temporary Codex home ignores user configuration, OAuth state and execution-policy rules; tools inherit no environment and execution is read-only without approvals. It uses a temporary receiver/database, not ports 4318/4319 or the shared real sessions. The tests clear inherited proxy and hosted OpenAI environment settings. The child inherits only runtime essentials and the optional private-provider key, excluding desktop app pipes/session IDs. It pins `RUST_LOG=info` for consistent log filtering; the parent configuration is unchanged. Child stdout/stderr diagnostics stay in the temporary test directory.

If the private service is unreachable, incompatible, or ambiguous, report the failure and fix that configuration. Never substitute a hosted/paid provider or an existing logged-in Codex session. In particular, `agentboard resume --execute` uses normal user Codex configuration and is not a suitable live test command; extend the isolated private harness for future native-resume coverage. The dev dashboard remains in dummy mode. [Policy tests](../backend/tests/test_private_endpoint.py) verify provider rejection and model-discovery behavior offline; they do not establish live service availability.

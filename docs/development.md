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

## Configuration and boundaries

`--config` works with every CLI command. Precedence is CLI overrides, explicit TOML values, environment defaults, then built-in defaults. TOML database paths resolve relative to the config file; CLI `--database` paths resolve relative to the working directory. Unknown keys and invalid types are rejected. The dev file pins its database, port, dummy model mode and empty plugin list, so inherited live database/model/plugin settings do not override them.

Dev mode blocks automatic OTel uploads but permits explicit imports and normal application actions. For an OTLP receiver experiment, use a separate temporary test configuration with `otlp_enabled=true` and controlled synthetic requests; do not point the global Codex exporters at it.

The dev config enables `reload = true`: Python changes under `agentboard/` automatically restart the backend. Each worker retains the resolved config and CLI overrides, including the dev database and disabled telemetry ingestion. Refresh the internal browser after UI changes. Restart the command after TOML or environment changes; these settings are captured at startup. Database writes do not trigger reloads. The default collector has reload disabled.

This config isolates endpoints and data; it does not freeze another process's Python code. Run a persistent live collector from a stable checkout or installation, without development auto-reload. Do not replace or restart the collector as part of routine dev testing.

Verification: [dev configuration tests](../tests/test_dev_config.py) cover default endpoint compatibility, config precedence, CLI port selection, rejected live uploads, explicit imports, snapshot WAL consistency and refusal to overwrite existing data.

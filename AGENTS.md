# Project documentation

When creating or updating documentation, follow [the documentation rules](docs/documentation-guide.md). Keep changes concise, evidence-based, and explicit about current behavior, proposals, and correctness gaps.

# Codex schema workflow

When upgrading the Codex CLI used by AgentBoard or changing its app-server integration, follow [the schema workflow](schemas/README.md). Run `python scripts/codex_schemas.py check` against the intended executable and review any drift before updating the baseline. Never hand-edit generated schemas. Regular tests verify the stored baseline offline. App-server schemas must not be used as a contract for raw rollout JSONL.

# Browser testing

When testing the app UI, use Codex’s internal in-app browser (`iab`). Do not use the user’s personal Chrome or another external browser unless explicitly requested.

# Isolated development data

For development, debugging, and UI verification, use `uv run agentboard --config config/dev.toml serve` on port 4319. Use fixtures or an explicit SQLite snapshot as described in [the development workflow](docs/development.md). Keep the existing Codex exporters on port 4318; do not test against or mutate the live collector's database as part of routine development. The dev dataset must not receive continuous telemetry. Use Codex's internal browser for the dev URL.

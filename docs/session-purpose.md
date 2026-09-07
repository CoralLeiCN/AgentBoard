# Session purpose classification

Implemented 2026-09-07. Run an optional LLM to assign one primary purpose per session, or let an external agent submit a result. Importing and collecting telemetry do not invoke models.

## Categories

The [2025 enterprise AI report, page 14](https://cdn.openai.com/pdf/7ef17d82-96bf-4dd1-9df2-228f7f377a29/the-state-of-enterprise-ai_2025-report.pdf#page=14) supplies six broad task types. AgentBoard adapts these into session labels and adds debugging and an unclear/other fallback. This is an application taxonomy, not OpenAI's original classifier or a reproduction of its study.

| API category | Session purpose |
| --- | --- |
| `writing` | Draft, edit, summarize, or translate text |
| `coding` | Implement, refactor, test, or review software |
| `bug-fixing` | Debug a specific failure or regression; displayed as **Debugging** |
| `research` | Find, compare, or synthesize information |
| `analysis` | Analyze data or perform calculations |
| `creative-media` | Create or edit visual, audio, or video material |
| `guidance` | Explain a procedure, teach, or advise |
| `other` | Another purpose or insufficient evidence for a more specific label |

Existing IDs and stored results remain valid. New submissions may use `debugging` as an alias for `bug-fixing`; either filter spelling finds those results. `unclassified` is a list filter for missing results, not a model category. Reimports preserve labels. Reclassification explicitly replaces the saved result; there is no automatic refresh after a session grows.

## Prompt management

Implemented 2026-09-07. Edit [prompts/session_purpose.txt](../backend/agentboard/prompts/session_purpose.txt) to change the classification instructions. Its `${categories}` placeholder receives IDs and descriptions from the shared taxonomy. The rendered text and prompt hash are unchanged by this move. See the [prompts README](../backend/agentboard/prompts/README.md) for template syntax, adding prompts, and restart behavior.

## Structured output contract

Implemented 2026-09-07. [The data models](../backend/agentboard/classification.py) define the contract:

| Model | Responsibility |
| --- | --- |
| `PurposeCategory` | Enum of the eight canonical IDs; taxonomy metadata references these members |
| `ClassificationLabel` | Model output: required enum `category` and nonblank `reason` (1–2,000 characters); extra fields forbidden |
| `ClassificationRequest` | External-agent input: adds `model` and optional `input_sha256`; explicitly accepts the `debugging` alias |
| `ClassificationResult` | Saved/API result: adds typed timestamps, provenance, hashes, and input metadata supplied by AgentBoard |

`GET /api/v1/classification-schema` returns `ClassificationLabel.model_json_schema()`. Both the model contract and API request/result enums are inspectable in `/openapi.json`. The model generates only this payload:

```json
{"category": "bug-fixing", "reason": "The session diagnoses and fixes a checkout rounding regression."}
```

For model classification, Responses sends the generated schema in `text.format`; Chat Completions sends it in `response_format.json_schema`. Both use `type=json_schema` and `strict=true`. This is [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), rather than JSON mode. A compatible model/server must support this format; schema rejection is an error and does not retry with unconstrained output. Conversation replay uses ordinary text output.

Every generated label is also validated locally. Unknown categories, missing/extra fields, blank/oversized reasons, and Markdown-wrapped JSON are rejected before persistence. Refusals and incomplete responses do not replace saved labels. The reason pattern uses an anchored full-string form so compatible decoders with full-match regex semantics still permit complete explanations.

`debugging` is accepted only for external submissions and normalized to `bug-fixing`; generated labels must use the canonical enum. New external requests reject unknown fields, including attempts to supply provider or dummy provenance. Existing stored classifications are preserved without migration; new runs add `output_schema_sha256` for the contract used. See [contract tests](../backend/tests/test_classification_contract.py) and [wire-format tests](../backend/tests/test_responses.py).

## Run a model

Install the model extra, configure an OpenAI-compatible endpoint, then classify sessions explicitly:

```sh
uv sync --extra models
export AGENTBOARD_MODEL_MODE=local
export AGENTBOARD_MODEL_BASE_URL=http://localhost:30000/v1
export AGENTBOARD_MODEL=my-model
# For a Responses API server (otherwise defaults to chat_completions):
export AGENTBOARD_MODEL_API=responses
# Optional for slower local models (default 30 seconds):
export AGENTBOARD_MODEL_TIMEOUT_SECONDS=120
# Set AGENTBOARD_MODEL_KEY if your endpoint requires authentication.
uv run agentboard classify SESSION_ID
uv run agentboard classify --all --limit 100
uv run agentboard classify --all --force
```

`--all` snapshots unclassified conversation IDs, newest first; unattributed telemetry is excluded. Explicit IDs also skip saved labels unless `--force` is set. Each result is printed as JSONL immediately, with a summary on stderr. A failed session does not stop later sessions; any failure yields exit status 1. `--limit` bounds the number selected with `--all`. Use `--config` or `--database` before `classify` to select a dataset. The command requires the `classification` feature.

`local` requires a reachable model. `auto` permits a labeled dummy fallback on missing dependencies, connection failures, or timeouts. `dummy` always uses test keyword rules. Model rejection or malformed output remains an error. Dummy labels are not LLM judgments.

For isolated development, use fixtures and the pinned dummy model:

```sh
uv run agentboard --config config/dev.toml import examples/fixtures/codex-session.jsonl
uv run agentboard --config config/dev.toml classify --all
uv run agentboard --config config/dev.toml serve
```

In the [dev UI](http://127.0.0.1:4319), choose **Classify purpose** inside a session or **Classify this page** for unclassified rows on the current page (up to 20). Page classification runs sequentially and reports progress, dummy results, and failures. **Stop after current session** prevents subsequent requests; closing the page also stops scheduling further requests. Use the purpose filter to inspect each category or unclassified sessions. Labels show the reason and model identity; truncated input is marked **Partial transcript**. The dev TOML explicitly pins dummy mode; an environment variable does not override it.

## Live Responses API test

Implemented 2026-09-07. [The opt-in test](../backend/tests/test_classification_endpoint_e2e.py) imports four synthetic sessions into temporary databases, calls AgentBoard's classification API using a real Responses endpoint, checks debugging/writing/coding/analysis labels, and verifies saved results. It requests the strict schema, checks explanatory reasons, and prints model identity, reason, schema/input hashes, and elapsed seconds. It uses `model_mode=local` with no dummy fallback, plugins, or live telemetry. Neither the collector nor dev database is used. Normal test runs skip these cases unless an endpoint is supplied.

```sh
AGENTBOARD_E2E_CLASSIFICATION_BASE_URL=http://localhost:30000/v1 \
AGENTBOARD_E2E_CLASSIFICATION_MODEL=my-model \
uv run --extra dev pytest -q -s backend/tests/test_classification_endpoint_e2e.py
```

The model variable is optional; without it, the gateway discovers the first advertised model. Set `AGENTBOARD_E2E_CLASSIFICATION_API_KEY` if the endpoint needs a credential. The live test allows 120 seconds per request, configurable through `AGENTBOARD_E2E_CLASSIFICATION_TIMEOUT_SECONDS`; normal gateway calls retain the 30-second default unless `model_timeout_seconds` / `AGENTBOARD_MODEL_TIMEOUT_SECONDS` is set. This test checks protocol compatibility and four simple purposes, not general classification accuracy. Its settings do not switch the running dev dashboard out of dummy mode.

## Run an external agent

Use the same backend API as the frontend:

1. Read `GET /api/v1/config` for `classification_taxonomy` (version, IDs, descriptions, source), and `GET /api/v1/classification-schema` for the model JSON Schema.
2. List sessions with `GET /api/v1/sessions?category=unclassified`; follow `next_offset`. Collect candidate IDs before writing labels so the filtered pages do not shift under the worker.
3. Fetch `GET /api/v1/sessions/SESSION_ID/classification-input`. It returns ready-to-use `messages`, input/prompt hashes, selected event IDs, and truncation metadata. This read does not invoke a model.
4. Pass `messages` and the generated strict schema to your chosen LLM, or have your agent apply the included classification instructions to the transcript as data.
5. Submit `PUT /api/v1/sessions/SESSION_ID/classification` with `category`, a nonempty `reason` of at most 2,000 characters, `model`, and optionally the returned `input_sha256`.

External results are marked `provider=external` and `provenance=externally_asserted`; submitted model identity and input hash are not independently verified. The endpoint validates category and field shape, not whether an agent ran or whether its judgment is correct. [Example 09](../examples/09_external_classification.py) runs this workflow using the model gateway; an agent may also inspect normalized exports directly.

See [model data lineage](data-lineage.md#10-model-derived-data-and-continuation) for input selection, information loss, and provenance. [Automated tests](../backend/tests/test_classification.py) cover categories, validation, bounded inputs, source preference, batch behavior, and external submissions. They do not establish classification accuracy on real sessions.

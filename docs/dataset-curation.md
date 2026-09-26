# Dataset label review and input deduplication

**Implemented 2026-09-26.** This module curates archived turn-classifier results without rerunning classifiers or changing historical evidence. Duplicate suggestions use embedding cosine similarity, with configurable local or remote models. [Specification §17](specification.md#17-dataset-curation) owns acceptance criteria.

## Label review

Create a review workspace from pinned dataset/results artifacts, using the existing coverage formats (`turn-comparison-v1` and `independent-turn-v1`). Join by original session ID, turn ID and full classifier-input SHA-256, including predecessor context. Reject duplicate configurations/targets and incompatible schemas. Missing, changed-input and invalid results remain explicit coverage gaps; they do not vote.

The recipe selects one reference configuration by model, reasoning effort, execution mode and taxonomy. GPT-6 Sol extra-high is the convenience reference for the current local experiment; `--current` selects its batched configuration when present, otherwise its sole available execution configuration, and saves the exact selection. Missing or ambiguous reference configurations require an explicit recipe; model size/rank is not inferred from names. A valid reference label becomes `inferred`, even when all models agree. A missing reference leaves the label `unlabeled`; other models never silently substitute for it.

Two or more valid categories that differ produce a disagreement. Equal categories with different explanations count as agreement. Zero or one valid result is insufficient comparison evidence. Partial coverage is reported separately. The review queue prioritizes unresolved disagreements, while all turns remain inspectable and reviewable.

A human sees the target, predecessor context when present, per-configuration categories/reasons and unavailable-result reasons. Confirming or correcting a category requires a reviewer name and reason, and produces `verified` with a UTC timestamp. This is an attributed local review, not authenticated identity or proof of correctness. Preserve original model labels and every decision. Reopening review restores the reference's inferred label (or unlabeled state). Mixed inferred/verified/unlabeled datasets are valid; status is distinct from the original label's content origin.

## Input-only duplicate suggestions

Compare only `messages[*].content` where `role == "user"` on each target. Exclude assistant answers, tools, reasoning, predecessor turns, model labels and classifier prompts. Normalize Unicode with NFKC and collapse whitespace per message, preserving case and message boundaries. Retain the raw target unchanged for review. Empty/absent user text receives no suggestions; creation fails clearly if every input is empty. Normalized input fingerprints hash the UTF-8 JSON string plus a trailing newline (`ensure_ascii=False`); target/pair IDs hash ordered identity arrays using the archive encoder. Empty input fingerprints are null.

Encode each nonempty input and calculate cosine similarity (`dot(a,b) / (norm(a) * norm(b))`, range −1 to 1). Cosine is the only similarity approach. There is no lexical score, fallback or compatibility adapter. Workspace/recipe format is `dataset-curation-v2`; earlier files remain untouched but cannot be reviewed/exported with this version. The listing marks them unsupported; create a fresh workspace from pinned source artifacts.
Default threshold is 0.85; configurable range is 0.1–1. Emit all qualifying pairs, ordered by descending score then stable target identity, with algorithm/version, score, normalized input hashes and threshold. No transitive clustering or automatic removal. Before choosing, the pair view shows both complete recorded turn transcripts (including assistant answers), current labels, and every saved classifier category/reason, with expandable predecessor context, raw turn metadata and review history. Long messages are not truncated in this view; the queue retains short input previews. Similarity still uses only target user input. A pair review explicitly chooses keep both, remove either turn as a duplicate of the other, or reopen. Removed turns carry `removed_duplicate`, retained-turn ID, reviewer, reason and timestamp. Reject removal of a representative already referenced by a removed turn; restore its dependents first. Restoring a turn preserves label state and decision history.

### Configurable embedding providers

**Implemented 2026-09-26.** `embedding_provider` selects `local` (default) or `remote`. Models are explicit configuration, independent of the reference classifier. Both providers save normalized vectors and use the same cosine calculation in 128-row matrix blocks, without retaining a full pairwise matrix. Invalid, empty, nonfinite, zero-norm or inconsistent-dimensional vectors fail creation. Missing dependencies, weights, credentials or failed requests never switch provider or similarity algorithm. Review and export use saved evidence without inference.

| CLI flag / recipe field (replace hyphens with underscores) | Local | Remote |
| --- | --- | --- |
| `--embedding-provider` | `local` (default) | `remote` |
| `--embedding-model` | Cached Hugging Face namespace/model ID | Required endpoint model ID or alias |
| `--embedding-revision` | Required immutable 40-character SHA for custom models | Rejected; the API cannot enforce a revision |
| `--embedding-batch-size` | 32 chunks by default, range 1–128 | 32 full inputs by default, range 1–128 |
| `--embedding-base-url` | Rejected | Required HTTP(S) API base URL, e.g. `http://localhost:8080/v1` |
| `--embedding-api-key-env` | Rejected | Optional environment variable containing a bearer token |
| `--embedding-timeout-seconds` | Rejected | 60 by default; greater than 0, at most 600 |

Recipe fields materialize effective provider settings, including defaults. Unknown embedding options are rejected; tokens cannot be supplied directly in recipes. URLs cannot contain credentials, queries or fragments. No credentials are saved in workspaces/exports. The CLI has no `--similarity-method` option; that recipe field is also rejected.

#### Local Sentence Transformers

The default model is [`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), pinned to `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. It produces 384-dimensional embeddings for English sentence/short-paragraph input. The [Sentence Transformers documentation](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html) describes cosine comparison. Load cached weights on CPU with remote model code disabled. Downloads are a separate preparation step.

Recursively split normalized text near its midpoint (preferring whitespace) into contiguous, non-overlapping chunks that fit the loaded token window, including special tokens. Every normalized character is retained and tokenization disables truncation. Encode each chunk with an empty prompt and normalize. Average chunk vectors weighted by token count including special tokens, then normalize the turn vector. This pooling is an AgentBoard approximation for long input. Save revision, package versions, device, window, batch/chunk counts and pooling method.

```sh
uv sync --locked --extra dev --extra embeddings
uv run --extra embeddings agentboard experiments curate --current \
  --embedding-provider local --similarity-threshold 0.85
```

For custom cached weights, also pass `--embedding-model namespace/model --embedding-revision COMMIT_SHA`. To populate the default cache explicitly:

```sh
uv run --extra embeddings hf download sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --include '*.json' '*.txt' '*.safetensors'
```

#### Remote embeddings

The provider uses an OpenAI-compatible `POST <base-url>/embeddings` API, supported by [Hugging Face Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference/quick_tour#openai). Send `model`, an array of complete normalized user inputs, and `encoding_format: "float"`. No assistant results, context, labels or target identities are transmitted. A response must contain exactly one finite numeric vector for each unique batch index; out-of-order results are reassociated by index. Normalize returned vectors locally. Record the requested and reported model IDs; an absent reported ID remains unknown, and a model change between batches fails. An alias or reported name does not establish immutable server weights.

AgentBoard neither chunks nor truncates remote inputs. The generic API cannot establish the server's token limit, pooling, prompt or truncation behavior. Record `client_truncated: false`, `truncated: null`, `server_truncation: "unknown"`, and display this limit in pair review. Configure the service to reject overlength inputs for lossless use. HTTP/timeout/invalid-response failures abort creation before a workspace is published; error messages omit response bodies. Requests disable inherited proxies, redirects and retries, and never fall back to local weights or another endpoint.

Synthetic deployment example (replace the endpoint and model with your service; the environment variable must already be set if supplied):

```sh
uv sync --locked --extra dev --extra remote-embeddings
uv run --extra remote-embeddings agentboard experiments curate --recipe /absolute/path/to/recipe.json \
  --embedding-provider remote --embedding-model text-embeddings-inference \
  --embedding-base-url http://localhost:8080/v1 \
  --embedding-api-key-env EMBEDDING_API_KEY --embedding-batch-size 32
```

Omit the key flag for an anonymous service. Explicit remote creation transmits selected user input to that configured service; review/export remain local. Existing private development archives retain their local-only policy unless explicitly authorized for that endpoint. Live tests use only the isolated [private harness](testing.md#private-model-tests) with synthetic input.

The 0.85 threshold is an initial suggestion cutoff, not a calibrated duplicate probability. Shared boilerplate and pooling can inflate similarity or obscure differences. Model choice changes scores; calibrate a threshold on human-reviewed examples. Saved vectors, vector/input fingerprints and algorithm version support offline score recomputation. Re-encoding requires the configured model/runtime and may yield floating-point differences.

## Storage and interface

Use separate `experiments curate`, `curations`, `review` and `curation-export` commands and a loopback-only review web application, independent of the trace database and feature allowlist. `curate --current` reads the local navigation pointer's pinned coverage recipe; an explicit recipe supports other archived datasets. Mutable workspaces live under `<data-home>/experiments/sync/curation/`; locked atomic writes and an expected revision prevent lost decisions. Decisions persist across refresh/restart. A new dataset/input revision requires a new workspace; verification never carries over by ID alone.

Exports create a new immutable dataset bundle referencing all pinned inputs. Save the recipe, full workspace and audit, all turns (including removed turns), and a separate active-turn view. Export counts include label status and removal status; no source bytes are deleted or rewritten. Reviewing and exporting make no model calls. Workspace creation runs the configured embedding provider only. No trace-schema migration or classifier rerun is involved.

The local UI paginates queues, shows full selected evidence, preserves errors and stale-edit conflicts visibly, and supports narrow screens. Similarity generation is an explicit create-time calculation intended for the current modest dataset; large-scale search is deferred.

Internally, recipes are validated once into typed local/remote embedding configurations. Encoding returns a named result containing vectors, per-input metadata and provider provenance. Comparison returns row annotations without modifying its inputs; workspace construction applies those annotations explicitly. The review controller handles requests and selection separately from label/pair rendering. These interfaces preserve the v2 JSON format. Known local file/load failures retain their causes and distinguish missing files from runtime/model incompatibility; unexpected implementation errors propagate. See the [maintenance contract](specification.md#12-verification-and-maintenance) and [style checks](testing.md#routine-checks-before-handoff).

## Use the module

With the configured machine data home, prepare the existing results, then start the returned workspace:

```sh
uv run --extra embeddings agentboard experiments curate --current
uv run agentboard experiments curations
uv run agentboard experiments review WORKSPACE_UUID --port 4320
```

Open `http://127.0.0.1:4320` in Codex's in-app browser. Enter a reviewer name, inspect a queue item, choose a category or duplicate action, and supply a reason. **Save verified label** confirms or corrects the category. **Removed** exposes restoration. **Export reviewed dataset** creates a new immutable bundle and download links for all turns, active turns and the full workspace/audit. The server binds only to loopback and rejects cross-origin requests; port 4318 is reserved for the live collector. It is a local single-user tool with no authenticated reviewer accounts.

For reproducible selection, pass `curate --recipe /absolute/path/to/recipe.json`. Start from a saved coverage recipe: change `format_version` to `dataset-curation-v2`, add the exact `reference_configuration` below, and optionally add `similarity_threshold`. Keep `project`, `experiment`, `dataset` and `results` with their complete immutable artifact references. Add embedding fields from the provider table; omitted provider settings select the pinned local default. The source dataset must contain `messages` lists as in the archived turn inputs. A supplied `index` must be unique; an absent predecessor remains explicitly unavailable in the selected dataset.

```json
{
  "model": "gpt-6-sol",
  "reasoning_effort": "xhigh",
  "execution": "batched",
  "taxonomy": "session-purpose-v1"
}
```

`curate --similarity-threshold 0.9` overrides the recipe threshold. New threshold/reference/data selections produce a new workspace; existing reviews are preserved without automatic transfer. `curations` reports saved workspace IDs, revisions, statuses and queue counts. CLI export requires the observed revision:

```sh
uv run agentboard experiments curation-export WORKSPACE_UUID --revision 3
```

The API is shared with the UI, with its schema at `/docs`. `GET /api/workspace` returns counts/settings; `/api/turns?queue=disagreements|all|unlabeled|verified|removed` and `/api/pairs` paginate with `offset` and `limit` (default 30, maximum 100). `/api/turns/TARGET_HASH` returns full evidence/context/audit. `POST /api/decisions` accepts `revision`, `target`, `action`, `reviewer`, `reason` and `category` for verification. Actions are `verify`, `reopen_label`, `restore`, `keep_both`, `remove_left`, `remove_right`, `reopen_pair`; pair actions target the pair ID. Stale revisions return 409, invalid decisions 422, and storage failures 503. `POST /api/export` accepts `revision` and returns the new bundle identity.

The [synthetic demo](../examples/dataset_curation.py) creates six turns, with disagreements, exact/near duplicates, an assistant-only turn and a missing reference result:

```sh
uv run python examples/dataset_curation.py --data-home /private/tmp/agentboard-curation-demo
```

It prints the review command. Demo labels and hand-authored vectors are explicitly synthetic; no model executes. The demo patches the encoder solely while generating its own fixture. Demo data persists in the specified external data home until deliberately removed.

## Verification and limits

[Backend regressions](../backend/tests/test_curation.py) cover valid/stale/invalid/missing labels, configuration identity, mixed statuses, review history, locks/revisions, input-only similarity, empty inputs, removal/restoration, representative protection, exports, source hashes and API/CLI isolation. [Frontend tests](../frontend/tests/curation.test.cjs) cover escaped evidence, full duplicate-pair transcripts/results (including long assistant answers), explicit state rendering, failed loads/saves, inspected revisions and stale-pair rejection through the exported controller. Shared plain synthetic archive factories replace fixture unwrapping. The initial browser flow was checked with the synthetic demo at desktop and 390-pixel widths: required fields, verification, duplicate removal/restoration, reload persistence, empty queues, export and a saved JSON download.

[Embedding regressions](../backend/tests/test_embedding_similarity.py) use fake local tokenizers/encoders for long-input coverage, model pins, cosine geometry, accurate loader error causes and unchanged caller rows on success/failure. [Remote regressions](../backend/tests/test_remote_embeddings.py) use an HTTP mock transport to cover input-only payloads, configured model/endpoint/auth, batching, response order, validation, errors, secret omission, immutable exports and atomic failure. The opt-in [private endpoint test](../backend/tests/test_embedding_endpoint_e2e.py) is separate from offline coverage.

Human verification and similarity do not establish objective ground truth or semantic equivalence. Full classifier-input hashes are supplied by archived producers and checked through artifact integrity, not reconstructed. Fixed inputs require a new workspace when changed. Large-scale indexing, authenticated reviewer accounts and automatic review transfer remain outside this implementation.

The synthetic live embedding test on 2026-09-26 failed at private `/models` discovery: `http://192.168.1.220:30000/v1` refused the connection. No alternative endpoint was used; real remote interoperability remains unverified.

Style-refactor verification on 2026-09-26: `uv run --no-sync pytest -m "not e2e" -q` — **626 passed, 6 deselected**; `node --test frontend/tests/*.test.cjs` — **54 passed**. Ruff lint, the scoped Ruff/Prettier style check, local documentation links and `git diff --check` passed. The locked development/local embedding extras and pinned npm dependencies were installed. Live model tests were not rerun: provider request behavior is unchanged and covered by synthetic encoders/mock HTTP; the remote interoperability limitation above remains. No Codex CLI or app-server integration changed, so executable schema drift checks were not run; offline schema baseline coverage passed with the backend suite.

The refactored controller was checked in the in-app browser on a separate synthetic workspace: required review fields, full pair evidence, verification, removal/restoration, restart/refresh persistence, empty queues and export. The downloaded audit matched the immutable archive byte-for-byte, including all six synthetic turns and the three decisions. The desktop view had no horizontal overflow or captured console errors. Existing private workspaces were not modified by these checks.

On 2026-09-26, the in-app browser verified remote-provider provenance (including absent revision and unknown server truncation), cosine scores, full transcripts/classifier results and decision controls on a synthetic mock-HTTP workspace at desktop and 390-pixel widths, without horizontal overflow. A separate local curation operation used cached MiniLM weights with networking disabled and verified saved-vector integrity. Its review UI showed the configured model/revision and both full turns without console errors. This establishes the local operation and review presentation, not remote interoperability or duplicate-detection accuracy. The [selected classifier coverage](experiment-storage.md#dataset-and-classification-coverage) is recorded separately from human review status.

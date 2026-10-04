# Supervised turn-purpose classifiers

**Implemented 2026-09-26; older BERT/LightGBM execution pending.** Train a BERT-like encoder with [train_bert.py](train_bert.py), or LightGBM over frozen embeddings with [train_lightgbm.py](train_lightgbm.py). Each script owns its configuration dataclass and training loop. [Specification §16](../../docs/specification.md#16-supervised-classifier-comparison) defines requirements; this page describes the implemented workflow.

This project has its own dependencies, lockfile and tests. Preparation, training, inference and comparison run independently of AgentBoard; optional archive recording uses its existing recorder API. The [utility CLI](classifier/cli.py) currently provides preparation, inference, saved-GPT comparison and recording commands. It is a convenience for those operations, with no training subcommands; each training script runs directly.

## ModernBERT on Spark

**Implemented; Base and Large comparisons complete, 2026-09-29.** The [Spark execution record](../../docs/turn-classifier-run.md) describes preparation, training, serving and measured results. This separately versioned workflow uses all saved Astra labels, chronological session/fork groups, 8,192-token right truncation, a bounded learning-rate/seed comparison and a dedicated FastAPI service. Its optional dependencies are in the `encoder` extra; Spark uses the pinned NVIDIA image in [Dockerfile.spark](Dockerfile.spark) to retain its ARM64 CUDA PyTorch build. These defaults differ from the older BERT/LightGBM workflow below.

The [Base vs Large report](../../docs/turn-classifier-comparison.md) owns the ten-run comparison, loss figures, fixed-checkpoint diagnostics and fit analysis. Its aggregate inputs, plotting/PDF scripts and figures are preserved in a separate report archive with both original experiments pinned as dependencies.

| Entry point | Contract |
| --- | --- |
| [download_modernbert.py](download_modernbert.py) | Explicit public-weight download. `--model-id` accepts `answerdotai/ModernBERT-base` (default) or `answerdotai/ModernBERT-large`; pins the resolved revision and file hashes. |
| [prepare_turn_encoder.py](prepare_turn_encoder.py) | Verify Astra sources, group sessions chronologically and cache truncated tokens. Optional `--match-dataset` verifies identical membership, labels, input hashes and token IDs against a previous sealed dataset before sealing the new one. |
| [benchmark_modernbert.py](benchmark_modernbert.py) | Compare disposable Large models using training inputs only, including full-length stress updates and initial-score parity. Record failures, timings and memory; choose the fastest passing setting. |
| [train_modernbert.py](train_modernbert.py) | Smoke/save/reload, bounded five-run comparison and validation selection. Optional attention, fused AdamW, checkpointing, physical batch/token budgets and memory caps preserve effective batch 16. Repeated test reports carry `--evaluation-policy reused-holdout-comparison`. |
| [serve_turn_encoder.py](serve_turn_encoder.py) | Load a hash-pinned selection on Spark, restoring the saved attention backend and shared preprocessing. No Codex harness. |

## Jev through TypeSafe AI

**Direct provider selected and implemented 2026-10-04; live interoperability and classification quality unverified.** [run_jev.py](run_jev.py) uses TypeSafe AI's official [HTTP API](https://docs.typesafe.ai/api): `POST https://api.typesafe.ai/v1/systemone`, authenticated with `TYPESAFE_API_KEY`. It pins `jev-1.13.0`, the [stable version documented on 2026-10-04](https://docs.typesafe.ai/models), rather than a moving alias. The standalone, standard-library runner sends one `choice` question per target with the existing eight category IDs/descriptions. Its separately versioned prompt (`jev-turn-purpose-v1`) is unchanged. Jev returns a category, probabilities and confidence without a prose reason; results do not enter the application's session-classification endpoint, which requires a reason.

**Route migration:** Direct runs use `jev-turn-run-v2` and a **new output directory**. Vercel credentials/options are no longer accepted, including `--allow-provider-retention`. Old Gateway runs cannot be resumed by the direct runner; retain their artifacts and archived runner source unchanged. The provider switch itself sends no data.

Export a pinned `inputs/turns.json` using the [archive reader](../../docs/experiment-storage.md#filesystem-commands-and-producer-api) and retain its reference alongside the run. Use ignored `.agentboard/` storage. Commands run from the repository root and require no ML packages or provider SDK:

```sh
# Local preview: validates all inputs and saves the first ten targets without a request/key.
uv run python cronjob/classifier/run_jev.py \
  --turns .agentboard/jev/turns.json --output .agentboard/jev/direct-first-10 --limit 10

# Set TYPESAFE_API_KEY in your shell from the TypeSafe console.
# Execute exactly the previewed selection, or resume it after an interruption.
uv run python cronjob/classifier/run_jev.py \
  --turns .agentboard/jev/turns.json --output .agentboard/jev/direct-first-10 --limit 10 \
  --resume --execute

# Preview all turns in a NEW run; add --resume --execute afterward to submit them.
uv run python cronjob/classifier/run_jev.py \
  --turns .agentboard/jev/turns.json --output .agentboard/jev/direct-all-turns

# Preview a selected cohort while retaining predecessors from the complete source.
# The selection file is a JSON array of source turn index integers.
uv run python cronjob/classifier/run_jev.py \
  --turns .agentboard/jev/turns.json --target-indices .agentboard/jev/latest-484-indices.json \
  --output .agentboard/jev/direct-latest-484
```

`--execute` sends selected target and predecessor text directly to TypeSafe. The key is read only from `TYPESAFE_API_KEY`, never saved in arguments or artifacts; `AI_GATEWAY_API_KEY` is ignored. Create the key in the [TypeSafe console](https://console.typesafe.ai/); do not paste it into chat. TypeSafe account credit is separate from Vercel credit. The runner does not verify account balance or enforce a dollar budget. `--limit` bounds targets, not tokens or spend. Consult [current direct pricing](https://docs.typesafe.ai/models) before scaling up.

If the key is in an ignored local `.env`, use `uv run --env-file .env python ...` to load it; the Python runner does not automatically read dotenv files. Keep that file private and separate from experiment artifacts. Review the [privacy reference](#privacy-retention-and-training) and [observed access failures](#observed-access-failures) before execution.

**Direct preview prepared 2026-10-04:** The latest 484 targets are saved under `.agentboard/jev/direct-latest-484`, with complete predecessor context, zero requests and all labels pending. `TYPESAFE_API_KEY` was absent at setup. After configuring it in `.env`, the command for that prepared selection is:

```sh
uv run --env-file .env python cronjob/classifier/run_jev.py \
  --turns .agentboard/jev/turns.json --target-indices .agentboard/jev/latest-484-indices.json \
  --output .agentboard/jev/direct-latest-484 --resume --execute
```

### Privacy, retention and training

**Sources checked 2026-10-04; published policies, not an independent audit.** The current route sends requests directly to TypeSafe. Recheck its policies and account agreement before future runs. Vercel's historical guarantees below do not apply to direct traffic.

| Data or control | Direct TypeSafe policy and practical limit |
| --- | --- |
| Training | TypeSafe states Jev is not trained on customer requests/responses. The runner relies on that provider policy; the documented direct API has no equivalent to Gateway's `disallowPromptTraining` switch. [Models and data handling](https://docs.typesafe.ai/models#data-handling), [API contract](https://docs.typesafe.ai/api). |
| Retention and ZDR | The DPA sets retention by purpose and legal necessity, with no fixed day count. TypeSafe offers ZDR through enterprise arrangements; the runner cannot verify this account's agreement or deletion behavior. No request flag claims ZDR. [DPA, Schedule I §8](https://typesafe.ai/legal/data-processing), [enterprise ZDR](https://docs.typesafe.ai/legal). |
| Telemetry and other processing | MCA §4.1 excludes model-weight training on customer data without prior consent, but grants perpetual processing rights for telemetry, fraud/abuse monitoring and legal compliance. Section 4.3 permits telemetry use for product improvement. No-training does not exclude all analytics or secondary processing; no fixed telemetry expiry is established here. [Master customer agreement](https://typesafe.ai/legal/mca). |
| Processing location | The public policy describes US hosting and transfer of UK/EEA personal data to the US. This runner does not enforce regional routing. [Privacy policy](https://typesafe.ai/legal/privacy-policy). |
| Local evidence | Complete source bytes, rendered inputs and raw attempts stay in ignored `.agentboard/` storage; selected archives use the configured data home outside checkouts. They persist until deliberately managed; there is no automatic archive expiry or garbage collection. Preserve originals and dependencies under the [experiment storage policy](../../docs/experiment-storage.md#shared-deployment-privacy-and-recovery). |

**Implemented controls:** The endpoint is fixed to TypeSafe HTTPS, with no Gateway fallback, inherited proxies, redirects or automatic retries. The key stays separate from saved request bodies. `run.json` pins provider, endpoint, model and protocol, and explicitly records training as provider-policy based and retention as an unverified account agreement. These are provenance statements, not privacy settings sent to the API. The provider change preserves full inputs, local evidence, failure handling and exact-configuration resume checks. No direct Jev response has yet been observed here.

**Data boundary:** Each request contains full selected target and immediate-predecessor role/content text plus the classification rubric. Excluding identity metadata does not sanitize names, secrets or private code inside that text. Preserve complete local originals; do not commit or paste transcripts, credentials or raw attempts into documentation/issues. The [recorded authorization](../../docs/specification.md#152-jev-turn-classification-runner) covers the selected experiment and privacy settings, not unrelated data, providers or credit purchases.

### Observed access failures

**Historical Vercel route, observed 2026-10-04; both stopped on their first request with no labels.** These were separate Gateway account constraints. They do not establish direct TypeSafe access or provider deletion behavior.

| Authorized selection | Result |
| --- | --- |
| Initial ten-turn trial, default ZDR | HTTP 403: the Hobby account cannot use the Pro/Enterprise ZDR control. Preserve the original failed attempt separately. |
| Latest 484 targets with predecessor context; explicitly approved standard retention and no-training control | HTTP 403: this account's free Gateway credits do not permit Jev; the error requires paid credits. Routing metadata reports zero provider attempts. The other 483 requests were not dispatched; all 484 labels remain pending. Usage/cost were absent, so cost is unknown. |

The failed batch and original Gateway runner are archived locally. All 484 classifications are still pending; prepare a new direct run with the same full source and target index file. Do not modify the old run's endpoint/model or reuse its output directory.

### Historical Gateway privacy reference

**Superseded route; sources checked 2026-10-04.** Retained for interpreting the failed Gateway attempts. These policies and switches do not describe the current direct API.

| Gateway layer | Published policy and historical control |
| --- | --- |
| Content and provider ZDR | Vercel says Gateway deletes prompt/output content after completion. Provider ZDR filtering requires Pro/Enterprise; per-request filtering has no additional fee. Vercel lists TypeSafe as no-training/ZDR compliant under its agreements, with prompt/output retention limited to generation/contractual needs except legal obligations. [Gateway ZDR](https://vercel.com/docs/ai-gateway/security-and-compliance/zdr). |
| Training | Gateway does not train on prompts/responses. Its free, all-plan `disallowPromptTraining: true` filter restricts providers under Vercel agreements. It does not establish a deletion deadline. [No-training policy](https://vercel.com/docs/ai-gateway/security-and-compliance/disallow-prompt-training). |
| BYOK exception | Gateway's no-training filter does not apply to provider BYOK credentials; ZDR normally skips them unless marked compliant. The old runner did not verify downstream credential source. [BYOK and training](https://vercel.com/docs/ai-gateway/security-and-compliance/disallow-prompt-training), [BYOK and ZDR](https://vercel.com/docs/ai-gateway/security-and-compliance/zdr#byok). |
| Metadata | Gateway logs include timing, model/provider, authentication scope, status, usage and cost. Routing details last 30 days; older request records remain without those details. This is not a deletion deadline for all metadata. [Request logs](https://vercel.com/docs/ai-gateway/observability-and-spend/logs#retention-and-limits). |

The archived runner restricted routing to TypeSafe, defaulted to `zeroDataRetention: true`, and later added `disallowPromptTraining: true`. The approved 484-target attempt explicitly omitted ZDR using the former `--allow-provider-retention` switch. Exact settings remain in each historical run; no failure automatically changed them.

### Jev artifacts and verification

| Input or artifact | Behavior |
| --- | --- |
| Turns JSON array / JSONL | Same [input contract](#inputs-and-label-provenance) as the encoder workflow. Validate the complete source before selecting targets. By default use source-file order; optional `--target-indices` selects in listed order, then `--limit` takes the first N. Reject duplicate, unknown and noninteger indices. Empty targets stay eligible; a predecessor outside that subset is still included. |
| API `state` | Full compact `{"target":[...],"previous":[...]}` role/content JSON, identical to `turn_inputs`. Other metadata is excluded from the request. No client truncation; a provider context-limit rejection is a failed attempt. Server-side tokenization/truncation is unverified. |
| `source-turns`, `inputs.jsonl`, `question.json`, `run.json` | Complete original source bytes (including unknown fields), selected rendered inputs, exact question and configuration/source/selection hashes. The historical input hash is retained as asserted provenance; the rendered-text hash is computed independently. |
| Optional `target-indices.json` | Exact selection-file bytes and their SHA-256 in `run.json`. Resume checks the original and saved selection; full source turns remain available for predecessor lookup. |
| `attempts/*.request.json`, `*.response.json` | Every request body is saved before dispatch; raw decoded response body, HTTP status and elapsed wall seconds are saved afterward. Transport failures retain their exception type. An interrupted request without a response has unknown outcome/usage and may be charged again when explicitly resumed. |
| `predictions.jsonl` | Successful rows with session/turn identity, both hashes, configuration, category, eight probabilities, reported usage/provider cost metadata and elapsed seconds. Require the pinned returned model identity, valid category, finite probabilities summing to one, the selected category matching their maximum, and finite confidence in [0, 1]. Preserve `response_model`, confidence and native `input_tokens`/`output_tokens` usage without inventing cost. No invented rationale, usage or cost. |
| `summary.json` | Requested, available, pending and attempt counts; preview/execute mode and input size. Pending turns remain in the denominator. These derived files can be regenerated by running with `--resume` without `--execute`. |

The runner makes serial calls with a 60-second timeout (`--timeout`), no automatic retries, redirects, environment proxies or fallback models. Requests go directly to TypeSafe under the account agreement described above. Any HTTP, transport or invalid-answer failure stops further calls and returns a nonzero exit status. Resume requires identical source bytes, target selection, timeout, prompt and provider configuration; it revalidates saved responses and skips successes. Use a new output directory when any of these change. A process lock prevents two runners from using the same output directory. Output files remain local; no live collector, database, checkpoint or existing label is modified.

Predictions use the existing comparison format. To compare against an already prepared, hash-verified supervised dataset's test split:

```sh
uv run --project cronjob/classifier --locked classifier compare \
  --dataset .agentboard/classifier-run/dataset \
  --prediction jev=.agentboard/jev/direct-all-turns/predictions.jsonl \
  --output .agentboard/jev/comparison.json
```

That command evaluates only the dataset's test targets; it reports missing coverage and source/text mismatches. GPT labels measure reference agreement, not human accuracy. Jev's complete text differs from the encoder's 8,192-token truncation, and returned probabilities are not locally calibrated. Preserve raw usage/cost metadata when comparing costs; failed/interrupted requests can have unknown usage. Archive selected completed or partial runs with `classifier record` and pinned source references as described below; the recorder includes this runner's code.

[Synthetic regressions](tests/test_jev.py) cover preview/no-network behavior, predecessor selection, source preservation, HTTP request shape, strict response validation, provider-specific credentials, rejection of old Gateway runs/options, confidence, interruption, resume and failures. They make no live provider calls. Repository live integration tests retain the [private-harness policy](../../docs/testing.md#private-model-tests); the separately authorized hosted attempt above is dataset experiment execution.

**Direct-route verification, 2026-10-04:** 43 focused Jev regressions and 120 full classifier tests passed. Five optional checks skipped in the independent environment; the archive adapter then passed separately with AgentBoard available. Backend: 639 passed, six live tests deselected; frontend: 54 passed. Lint, formatting, CLI help, documentation links and whitespace checks passed. Optional ML/Spark checks and live TypeSafe execution were not run; this establishes the offline adapter, not provider access or privacy enforcement.

## Inputs and label provenance

Use the existing archive's `inputs/turns.json` array, or JSONL with the same fields. Export a pinned artifact with `agentboard experiments read ID inputs/turns.json`; save its reference JSON alongside the experiment. Resolve references through the [archive reader](../../docs/experiment-storage.md#filesystem-commands-and-producer-api), rather than treating a moving navigation pointer as an immutable input.

| Input | Transformation and output |
| --- | --- |
| `index`, `original_codex_session_id`, `original_codex_turn_id` | Unique integer index and nonempty identities become `session_id` / `turn_id`; duplicates fail. |
| `messages[].role/content`, `previous_turn_index` | Ordered user/assistant string messages from target and immediate predecessor become compact JSON text: `{"target":[...],"previous":[...]}`. Target comes first so the default right truncation retains it preferentially. Missing predecessor and unsupported message shapes fail; null predecessor becomes an empty list. Empty targets remain eligible. |
| `classification_input_sha256` | Retained as `input_sha256`, the upstream producer's asserted full-input hash. This workflow validates its shape and joins on it; it does not reinterpret or recompute the historical hash algorithm. |
| Derived text | UTF-8 SHA-256 becomes `text_sha256`; inference verifies it. Metadata/event IDs are excluded from model text. Original source bytes, including unknown fields, remain copied in the dataset directory. |
| Optional `group_id` | Conversation-family grouping, defaulting to original session ID. Supply a shared family ID for known resumed/forked sessions. Cross-session predecessor links also join groups. Unrecorded relationships and near duplicates cannot be inferred automatically. |
| Reference labels | JSONL rows with `session_id`, `turn_id`, `input_sha256`, `category`, `label_source: {"kind":"human","name":"review-batch-id"}`. Optional `text_sha256` additionally binds the exact rendered text. Categories use the [existing taxonomy](../../docs/session-purpose.md#categories). |

Synthetic reference-label example (hash is illustrative):

```json
{"session_id":"synthetic-session","turn_id":"synthetic-turn","input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","category":"coding","label_source":{"kind":"human","name":"synthetic-review"}}
```

Rendered text hashes and split assignments are Calculated; classifier predictions are Model-generated. The tool trusts the declared label provenance; it cannot verify human review. Missing/invalid labels are preserved in `pending.jsonl` with reasons and excluded from supervised splits. Unknown targets, duplicates and mismatched source/text hashes fail. Do not manually reuse hashes after changing source inputs. GPT reference labels require `--allow-gpt-labels`; they measure reference agreement, not human accuracy. Keep independently reviewed test labels when claiming accuracy.

`import-gpt` converts saved `independent-turn-v1` or `turn-comparison-v1` results to the same row shape, including one explicit model/effort/execution configuration. Batched files require `--model`; independent files already identify it. Invalid categories, missing reasons and prompt-compliance errors remain explicit invalid rows. The converter does not invoke GPT. Preserve the original result file alongside the conversion.

## Splitting and artifacts

[Preparation](classifier/data.py) joins labels by original identity and input hash, then computes connected components of sessions, explicit families, predecessor links and identical full rendered texts. Unlabeled rows can connect groups too. A seeded search over 512 group permutations scores row-count and class-ratio deviations from 70/15/15. Each candidate can grow training by whole groups to cover every observed category, keeping at least one group in each holdout and resizing validation/test proportionally. If no sampled candidate works, an exact check for two removable holdout groups determines whether a valid partition exists. No eligible row is dropped; input order does not affect assignment. At least three independent groups and two observed categories are required. Preparation fails when no nonempty isolated split can retain every category in training.

The fixed split files are `train.jsonl`, `validation.jsonl` and `test.jsonl`. `manifest.json` records seed, requested/actual ratios, eight-category support, missing-category warnings, preprocessing/taxonomy versions and SHA-256 file inventories. Ratios are approximate because groups are indivisible and training coverage takes priority. For example, ten single-row groups with all eight classes plus two repeated classes can use 8/1/1. Rare categories may be absent from validation/test. Never change splits to improve a test score; create a new experiment and report the change. New manifests record `split_algorithm: coverage-adaptive-groups-v2`. Existing datasets and model artifacts still load unchanged; preparing a new dataset may produce different assignments, so retain previously frozen splits.

New runs require unused output paths. Completed directory artifacts carry a verified manifest; interrupted output without a manifest cannot be loaded. Source dataset hashes bind embedding caches and trained models to one split. Vector metadata also pins each split's row order, shape and file bytes. Model bundles contain saved tokenizer/weights or native LightGBM text plus the embedding encoder, so inference does not require the original model path or vector cache. Saved encoder/BERT weights use safetensors, and vector loads disable pickle; source Sentence Transformer weights use that library's loading defaults. Source model fingerprints identify initial weights; retain those initial directories separately if exact retraining is required. Seeds aid repeatability but do not guarantee identical training across hardware/library versions.

## Commands

Commands below run from the repository root. Complete [worktree setup](../../docs/development.md#automatic-worktree-data-setup) first. Install the optional packages only for model work:

```sh
uv sync --project cronjob/classifier --locked --extra dev --extra ml
mkdir -p .agentboard/classifier-run
```

Provide existing local Hugging Face BERT-like and Sentence Transformer model directories. BERT weights must use safetensors; model directories must contain regular files, not symlinks. Network downloads, remote model code, providers and experiment uploads are disabled in the model adapters. Native LightGBM also requires its platform's OpenMP runtime; on macOS, install `libomp` with Homebrew if absent (`brew install libomp`), as described in the [LightGBM Python installation guide](https://github.com/lightgbm-org/LightGBM/blob/main/python-package/README.rst). Preparation, GPT conversion and comparison need no ML extra.

Put complete `turns.json`, `labels.jsonl`, and original GPT result files in the ignored working directory. For GPT pseudo-labels, convert one selected teacher and use the converted file as `--labels` with `--allow-gpt-labels`:

```sh
uv run --project cronjob/classifier classifier import-gpt \
  --results .agentboard/classifier-run/gpt-original.json \
  --output .agentboard/classifier-run/gpt.jsonl

uv run --project cronjob/classifier classifier prepare \
  --turns .agentboard/classifier-run/turns.json \
  --labels .agentboard/classifier-run/labels.jsonl \
  --output .agentboard/classifier-run/dataset --seed 42

# Edit each script's dataclass defaults before running; there are no training flags.
uv run --project cronjob/classifier --extra ml python cronjob/classifier/train_bert.py
uv run --project cronjob/classifier --extra ml python cronjob/classifier/train_lightgbm.py

uv run --project cronjob/classifier --extra ml classifier predict \
  --model .agentboard/classifier-run/bert \
  --inputs .agentboard/classifier-run/dataset/test.jsonl \
  --output .agentboard/classifier-run/bert-test

uv run --project cronjob/classifier --extra ml classifier predict \
  --model .agentboard/classifier-run/lightgbm \
  --inputs .agentboard/classifier-run/dataset/test.jsonl \
  --output .agentboard/classifier-run/lightgbm-test

uv run --project cronjob/classifier classifier compare \
  --dataset .agentboard/classifier-run/dataset \
  --prediction bert=.agentboard/classifier-run/bert-test/predictions.jsonl \
  --prediction lightgbm=.agentboard/classifier-run/lightgbm-test/predictions.jsonl \
  --prediction gpt=.agentboard/classifier-run/gpt.jsonl \
  --output .agentboard/classifier-run/comparison.json

uv run --project cronjob/classifier --with . classifier record --directory .agentboard/classifier-run
```

`record` is an optional adapter: `--with .` supplies the existing AgentBoard package for that invocation only. It uses the configured external data home and unchanged filesystem recorder, copying the selected workspace and this classifier project's code, README and lockfile. It does not copy or modify application code. Run it from the source checkout; do not include `.venv` or package caches in the selected evidence directory. Pass `--references pinned-references.json` to bind original archived inputs as immutable dependencies. Keep every required source/result in the selected workspace or pinned references. Recording is explicit, local and overwrite-free; it is not automatic publication. Failed copies retain recoverable archive staging. Working artifacts remain private in ignored `.agentboard/`; durable evidence belongs outside checkouts, per the [storage workflow](../../docs/experiment-storage.md#filesystem-commands-and-producer-api).

For new unlabeled inference, render a complete input cohort (including referenced predecessors), then pass the resulting JSONL to either saved model:

```sh
uv run --project cronjob/classifier classifier inputs --turns /absolute/path/to/new-turns.json \
  --output .agentboard/classifier-run/new-inputs.jsonl
```

## Model configuration

Edit the dataclass defaults near the top of each script, then execute that script from the repository root. Relative paths resolve against the current working directory. Both scripts accept a config instance through `train(config)` for programmatic runs, and importing either script does not start training or load ML packages.

| Script / dataclass | Controlled settings |
| --- | --- |
| [train_bert.py](train_bert.py) / `BertConfig` | Dataset/checkpoint/output paths, seed, epochs, batch size, token limit, device, truncation side, gradient clipping, mismatched-head loading, native AdamW options in `optimizer_params`, and Hugging Face model configuration overrides in `model_config` (e.g. dropout). Unknown model configuration fields fail. |
| [train_lightgbm.py](train_lightgbm.py) / `LightGBMConfig` | Dataset/encoder/cache/output paths, embedding batch size, device, token limit, truncation side, normalization, prompt, cache reuse, boosting rounds, patience, and native tree parameters in `parameters` (including seed, learning rate, leaves, regularization and threads). |

Each artifact manifest saves the dataclass values in `run_config`; model metadata also records the applied optimizer/tree and embedding settings. Paths become strings and tuples become JSON arrays. Label order, local-only model loading, BERT's validation macro-F1 selection and LightGBM's multiclass/log-loss contract are fixed workflow rules. Dataclass dictionaries cannot override the label/local-loading or objective/class-count/metric contracts. Model architecture and unprovided native options retain the selected checkpoint/library defaults. DART is rejected through `boosting`, `boosting_type` or `boost` before loading data or creating embeddings: [LightGBM disables early stopping for DART](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.early_stopping.html). LightGBM training also refuses to seal a model unless a positive validation best iteration was selected.

`train_lightgbm.py` creates embeddings and fits trees in one run by default. Set `reuse_embeddings=True` to use an existing verified cache; the dataset, encoder fingerprint, prompt, normalization, token limit and truncation side must match the dataclass. Use new output directories for another run. Inference restores these settings from the saved model rather than current script defaults.

Python inference through `classifier.models.predict(...)` returns a [PredictionResult](classifier/contracts.py) with `.predictions` and `.metadata` fields. It leaves input rows unchanged; archive recording likewise copies caller options before selecting filesystem mode. These in-memory interfaces preserve existing JSON artifact formats, with no dataset migration. File readers reject nonobject rows, and configuration validation rejects invalid counts before model work. The CLI reports expected input/file errors and missing optional packages; unrelated import and implementation errors retain their original exceptions. The [maintenance contract](../../docs/specification.md#12-verification-and-maintenance) defines the adopted style scope.

For example, in Python with `cronjob/classifier` on the import path:

```python
from pathlib import Path
from train_bert import BertConfig, train

config = BertConfig(
    dataset=Path(".agentboard/classifier-run/dataset"),
    model=Path("/absolute/path/to/local-bert"),
    output=Path(".agentboard/classifier-run/bert-lr-study"),
    epochs=5,
    batch_size=16,
    model_config={"hidden_dropout_prob": 0.2},
)
config.optimizer_params["lr"] = 1e-5
train(config)
```

## Training and comparison semantics

| Path | Fitting, selection and saved evidence |
| --- | --- |
| BERT-like | Fine-tunes encoder and classification head with AdamW, cross-entropy and gradient clipping. Chooses the best epoch by validation macro-F1 over the fixed eight categories; ties retain the earlier epoch. Saves label mapping, best weights/tokenizer, seed, learning rate, epoch history, package versions and truncation counts. CPU is default; `BertConfig.device` selects another device. |
| Embeddings + LightGBM | Frozen encoder; default settings use no added prompt and normalized float32 dense vectors. Encodes splits independently; no fitted scaler or test-label use. Native multiclass LightGBM trains on train vectors, early-stops on validation log loss, and saves best iteration, parameters, validation metrics and encoder. Single-thread deterministic tree fitting is the default; overrides are recorded. |
| Inference | Reloads verified artifacts, uses saved token length, truncation side, embedding prompt and normalization, emits identities, both hashes, category, eight probabilities and model/dataset fingerprints. `timing.json` records batch size, device, truncation and wall seconds including load. These timings are not a controlled benchmark. |
| GPT | Reuses explicitly converted saved outputs. Model/effort/execution combinations remain separate. Existing GPT prompts/context may differ from supervised token limits; report those differences before attributing accuracy differences solely to architecture. |

Model adapters truncate at their configured token limit (including special tokens) and report example counts and maximum observed token length. With the default right truncation, a target longer than the limit loses its tail and predecessor text may be dropped entirely. Left truncation instead loses the beginning, potentially including the target; it is recorded when explicitly configured. No sliding windows or learned pooling of chunks are implemented. Raw sources and full rendered text remain unchanged. Max lengths cannot exceed the selected model's positional/configured capacity.

The `compare` utility loads the persisted test set and applies the [shared evaluation functions](classifier/evaluation.py). Joins use identity plus source hash, and text hash when supplied. It reports each classifier's available/pending counts, changed/invalid/missing targets, outside-subset predictions, accuracy, macro-F1, weighted-F1, per-class precision/recall/support and confusion matrix (rows=true, columns=predicted). Macro-F1 includes all eight categories, with zero for undefined class metrics. An empty denominator yields null aggregate scores.

Available-only scores may use different subsets. **Use paired scores to compare models:** they cover the reported common set of available IDs across every supplied configuration. Missing coverage is never silently counted as correct or removed from the requested denominator. Mixed/duplicate configurations and duplicate identities fail. Supervised predictions from a different dataset split fail the comparison CLI. With any GPT reference labels the report is marked `reference_agreement`; a teacher compared with its own labels is not independent evidence. Saved GPT results with only upstream hashes cannot certify identical tokenized context. No confidence calibration, latency/cost ranking or significance claim is produced.

## Verification and limits

[Offline regressions](tests/test_classifier_training.py) cover source retention, reproducible/grouped splits, missing/stale/pseudo labels, historical GPT adapters, metrics/coverage, script/utility isolation, dataclass configuration and archive restoration. With the optional packages installed, the same file includes a tiny randomly initialized BERT and synthetic embedding/LightGBM round trip; it downloads no weights and contacts no model provider. This establishes adapter behavior, not useful classification quality.

[Boundary regressions](tests/test_classifier_contracts.py) cover malformed row/message input, batch-size validation and accurate CLI exception handling. Training/reload and archive tests also check that caller rows/options remain unchanged.

Run the [routine checks](../../docs/testing.md#routine-checks-before-handoff). The independent tests are outside the backend suite. Run `uv run --project cronjob/classifier --extra dev pytest cronjob/classifier/tests -q` for the core checks; archive and ML tests skip when their optional dependencies are absent. Include the ML packages and existing archive adapter to exercise every test:

```sh
uv run --project cronjob/classifier --locked --extra dev --extra ml --extra encoder --with . \
  pytest cronjob/classifier/tests -q
uv run --project cronjob/classifier --locked --extra dev ruff check cronjob/classifier
uv run --project cronjob/classifier --locked --extra dev ruff format --check cronjob/classifier
```

Codex/LLM tests remain restricted to the [isolated private harness](../../docs/testing.md#private-model-tests). The user explicitly authorized the separate local Spark encoder harness; its dated results belong in the [execution record](../../docs/turn-classifier-run.md#verification). Human adjudication and additional GPT performance measurements remain separate experiment execution.

**Verified 2026-09-26 after the style refactor:**

| Check | Result |
| --- | --- |
| Full classifier suite in its locked Python 3.13 environment, with `PYTHONPATH=backend:cronjob/classifier`, `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` | 42 passed, including split feasibility, DART rejection, nondefault dataclass settings, synthetic training/reload, cache validation, archive restoration and boundary errors. Isolation tests run without site packages or AgentBoard. No pretrained weights or provider calls used. |
| Synthetic before/after comparison | Rendered rows, seeded split assignments and the complete comparison report matched the pre-refactor baseline for 24 synthetic turns. This checks that cohort, not every possible input. |
| Application routine suites | Backend: 626 passed, 6 live tests deselected. Frontend: 54 passed. Includes the existing curation changes in this worktree. |
| Both projects' Ruff and offline lock checks, scoped Ruff/Prettier formatting, local documentation links and `git diff --check` | Passed. The classifier refactor adds no application runtime dependency. |

The first ML test attempt could not load LightGBM because this macOS host lacked a discoverable OpenMP runtime. The successful run set `DYLD_LIBRARY_PATH` to `cronjob/classifier/.venv/lib/python3.13/site-packages/torch/lib` (an absolute path on this host), using the installed PyTorch library for that process; no system library was installed.

Browser, live-provider and CLI schema-upgrade checks were not run: no UI, provider integration or Codex schema changed. The older BERT/LightGBM real-data comparison remains pending; the Spark encoder has its own execution record.

API contracts follow the primary [Transformers auto-model documentation](https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/auto), [Sentence Transformer model API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html) and [LightGBM training API](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html). Dependency versions are locked in the repository; a later model architecture may require a deliberate dependency update.

# Supervised turn-purpose classifiers

**Implemented 2026-09-26; real-data execution pending.** Train a BERT-like encoder with [train_bert.py](train_bert.py), or LightGBM over frozen embeddings with [train_lightgbm.py](train_lightgbm.py). Each script owns its configuration dataclass and training loop. [Specification §16](../../docs/specification.md#16-supervised-classifier-comparison) defines requirements; this page describes the implemented workflow.

This project has its own dependencies, lockfile and tests. Preparation, training, inference and comparison run independently of AgentBoard; optional archive recording uses its existing recorder API. The [utility CLI](classifier/cli.py) currently provides preparation, inference, saved-GPT comparison and recording commands. It is a convenience for those operations, with no training subcommands; each training script runs directly.

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

Run the [routine checks](../../docs/testing.md#routine-checks-before-handoff). The independent tests are outside the backend suite. Run `uv run --project cronjob/classifier --extra dev pytest cronjob/classifier/tests -q` for the core checks; archive and ML tests skip when their optional dependencies are absent. Include the ML packages and existing archive adapter to exercise every test:

```sh
uv run --project cronjob/classifier --locked --extra dev --extra ml --with . \
  pytest cronjob/classifier/tests -q
```

Live model tests remain restricted to the [isolated private harness](../../docs/testing.md#private-model-tests). Real-data training, human adjudication, hyperparameter studies and final GPT performance measurements are separate experiment execution. The pipeline does not claim to have run them.

**Verified 2026-09-26 after the split-feasibility and DART fixes:**

| Check | Result |
| --- | --- |
| Independent environment: `uv sync --project cronjob/classifier --locked --extra dev --offline`, then its Python runs `pytest cronjob/classifier/tests -q` | 25 passed; optional archive and ML tests skipped. Installed `classifier --help` works; AgentBoard is absent from this environment. |
| Full classifier tests in the existing Python 3.12 ML environment, with `PYTHONPATH=cronjob/classifier` and Hugging Face offline mode | 27 passed, including flexible split coverage and infeasibility, DART aliases rejected before work, nondefault dataclass settings, synthetic training/reload, embedding cache validation and archive restoration. On this macOS host, `DYLD_LIBRARY_PATH` points to that environment's PyTorch OpenMP library. No pretrained weights or provider calls used. |
| Application routine suites | Backend: 563 passed, 5 live tests deselected. Frontend: 46 passed. |
| Both projects' Ruff and offline lock checks, local documentation links and `git diff --check` | Passed. `git diff --exit-code -- backend pyproject.toml uv.lock` confirms application code, tests and root dependencies are unchanged. |

Browser, live-provider and CLI schema-upgrade checks were not run: no UI, provider integration or Codex schema changed. Real-data training and final performance measurements remain pending.

API contracts follow the primary [Transformers auto-model documentation](https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/auto), [Sentence Transformer model API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html) and [LightGBM training API](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html). Dependency versions are locked in the repository; a later model architecture may require a deliberate dependency update.

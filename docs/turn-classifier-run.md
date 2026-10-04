# ModernBERT Spark execution

**2026-09-29: Base and Large comparisons complete.** The [accepted plan](turn-classifier-plan.md) owns scope and trade-offs. Both validation-selected models were evaluated, archived and served on Spark. The services were stopped when checked on 4 October; incremental batch execution below used the saved checkpoints. Large improved agreement on the frozen validation and reused test partitions; repeat-seed variability and the previously inspected test set limit the conclusion.

The [Base vs Large report](turn-classifier-comparison.md) owns comparative metrics, all ten loss curves, fixed-checkpoint train/validation diagnostics, seed variability and fit analysis. Its report archive is `e7232cc9-d861-4894-b03d-de3cfb1b52da`; this page owns operational settings, preparation evidence and launch commands.

## Frozen data and measured truncation

All 1,431 Astra `xhigh` independent answers joined uniquely to 247 sessions. Preparation recomputed the historical complete-input SHA-256 from preceding/target role-content pairs, verified label hashes and immediate predecessors, and retained all three empty targets. No new Astra calls were made.

Raw headers were available for every selected session. The audit read `parent_thread_id` / `forked_from_id` relationships and grouped substantial exact shared targets (at least 1,024 content characters) and matching 2,048-character conversation prefixes. This produced 239 groups. Generic short replies do not connect groups. This conservative audit does not establish the absence of near duplicates.

Groups are ordered by latest included target timestamp in UTC, then stable group identity. The 70/15/15 allocation uses group counts and never optimizes label balance. Every session belongs to exactly one partition.

| Partition | Groups | Sessions | Turns | Truncated | Empty targets | Median original tokens | Maximum original tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 167 | 173 | 1,011 | 4 | 3 | 746 | 48,972 |
| Validation | 36 | 36 | 173 | 0 | 0 | 537 | 2,530 |
| Test | 36 | 38 | 247 | 0 | 0 | 556 | 2,798 |

| Category | Train | Validation | Test |
| --- | ---: | ---: | ---: |
| writing | 151 | 29 | 14 |
| coding | 334 | 59 | 79 |
| bug-fixing | 145 | 9 | 15 |
| research | 94 | 14 | 25 |
| analysis | 20 | 7 | 31 |
| creative-media | 4 | 1 | 1 |
| guidance | 213 | 40 | 56 |
| other | 50 | 14 | 26 |

Training spans November 17, 2025–September 16, 2026; validation August 13–September 19, 2026; test September 19–24, 2026 (UTC). Keeping whole sessions causes overlap at both boundaries. No session crosses a boundary, but this is not strict nonoverlapping time-window evaluation.

[Shared preprocessing](../cronjob/classifier/classifier/turn_encoder.py) serializes target first, then predecessor, using section/role markers and explicit empty markers. Each section preserves message order. Right truncation retains at most 8,192 tokens, including special tokens. Every row records full source/text hashes, retained-token hash, original/used/dropped counts and the preprocessing version. Token hashes use UTF-8 JSON arrays with default separators and a trailing newline; padding is excluded.

Only four inputs (0.28%) exceed the limit: two coding and two bug-fixing training turns. The largest loses 40,780 tokens. The holdout contains no truncated or empty target, so its scores cannot measure quality on those cases. `creative-media` has one validation and one test example; conclusions for that class remain weak. Astra labels remain inferred references, not human ground truth.

## Completed Base comparison

[Training](../cronjob/classifier/train_modernbert.py) uses ModernBERT-base revision `8949b909ec900327062f0ebf497f51aef5e6f0c8`, a new eight-class head, BF16 autocast, SDPA attention and gradient checkpointing. Microbatch 2 accumulates to effective batch 16; the final smaller batch is normalized by its actual example count. AdamW uses weight decay 0.01, linear decay and 10% warmup.

The comparison tries learning rates `2e-5`, `1e-5`, `5e-5` with seed 42, then repeats the winning rate with seeds 43 and 44. Each run has at most five epochs and two-check validation patience. Selection maximizes fixed-eight-class validation macro-F1, breaking ties by lower loss. Test evaluation occurs only after the selected checkpoint hash is written to `selection.json`.

The smoke run used the longest training input and a short input, performed a finite optimizer update, validated, saved and reloaded weights successfully. That step took 3.53 seconds and peak GPU allocation was approximately 3.85 GiB. This worst-length two-example check is not an estimate of average training throughput.

The five-run comparison selected learning rate `5e-5`, seed 42, epoch 5. The [comparison report](turn-classifier-comparison.md) records its validation/test metrics and later fixed-checkpoint diagnosis. Training-majority and TF-IDF/logistic test agreement were 31.98% and 40.89%.

The selected checkpoint hash is `6b1377cd02d3db9dd3a16b3d37298108e0d23780e14188659cdf3065540d0172`. The finalized archive ID is `4ee47359-3685-42e7-80d6-f53e1aafa827`. Comparative inference measurements are in the report.

Spark uses [Dockerfile.spark](../cronjob/classifier/Dockerfile.spark), based on the pinned NVIDIA 26.02 ARM64 PyTorch image. The resulting local image is `agentboard-turn-encoder:20260929`; its ID and installed package snapshot are retained with the run. PyTorch reports `2.11.0a0+eb65b36914.nv26.02`; Transformers is `4.57.6`. Base weights and input artifacts are hash-pinned. Training and serving use local files with the Hugging Face offline settings enabled.

The container `agentboard-encoder-train-01` served the selected Base model after training. Its code is in a separate read-only snapshot; later workspace edits do not change the service. The private workspace on Spark is `/home/coral/agentboard-turn-encoder-v1/`. `evidence/train-01/` holds progress, epoch histories, checkpoints, environment pins and original source inputs. `evidence/dataset/` holds the frozen split and token cache. No transcript, label row or checkpoint is committed to Git.

The first dataset snapshot stored source copies under logical names. A complete `train-01/original-inputs/` copy preserves the original manifest filenames for direct preparation replay. The current preparation script preserves that layout itself; a synthetic restoration regression checks identical reconstructed split files. This correction does not change training features, labels or assignments.

Optional `--archive-home` uses the existing filesystem recorder. Spark enables it and archives both successful and failed attempts, their source/code snapshots and checkpoints outside Git. The standalone trainer otherwise needs no AgentBoard import. Ordinary exceptions retain `failure.txt`; abrupt process/host termination can leave working artifacts without a finalized archive. Restarting requires a new output directory; optimizer-state resume is not implemented.

Inspect without changing the job:

```sh
ssh Coral-s-Spark 'cat /home/coral/agentboard-turn-encoder-v1/evidence/train-01/progress.json'
ssh Coral-s-Spark 'docker logs --tail 20 agentboard-encoder-train-01'
```

The exercised entry points, inside the image with the code and evidence mounted, are:

```sh
python prepare_turn_encoder.py --sources /evidence/inputs/sources.json \
  --model /evidence/base-model --output /evidence/dataset
python train_modernbert.py --dataset /evidence/dataset --model /evidence/base-model \
  --output /evidence/train-01 --archive-home /evidence/archive --mode train
```

These paths already exist; do not rerun preparation/training into them. New experiments require new paths. `download_modernbert.py --output NEW_PATH` explicitly downloads only public model files, resolving the revision before download and recording file checksums. Preparation/training never download a replacement model.

The implemented final report compares the encoder, training-majority and training-only TF-IDF/logistic baselines on the exact test partition. It includes fixed-eight-class metrics, confusion matrices, per-class support, truncated/complete/empty/host slices, 1,000 group-bootstrap samples and single-item inference timings. Baselines use decoded retained tokens. Confidence calibration is not attempted on this small validation cohort; scores are uncalibrated. Historical LLM comparisons and real HTTP throughput measurements are not produced by this first launcher.

## ModernBERT-large follow-up

The downloaded checkpoint is `answerdotai/ModernBERT-large`, revision `45bb4654a4d5aaff24dd11d4781fa46d39bf8c13`, with 28 layers and hidden size 1,024. New preparation uses `--match-dataset /evidence/dataset` and verifies identical ordered session/turn/group membership, labels, source/text hashes, token IDs and lengths in every partition. The sealed Large dataset is `evidence/dataset-large-01/`; the original Base dataset stays unchanged. Inputs, taxonomy, truncation, effective batch 16, learning-rate candidates, seeds, weight decay, warmup and epoch/patience limits stay the same.

[The benchmark](../cronjob/classifier/benchmark_modernbert.py) compares independent disposable models with one warm-up update and six timed updates on 96 shuffled training turns. It additionally tests one optimizer update on 16 copies of the longest retained input (8,192 tokens), without changing dataset rows or retaining trained weights. Initial predictions on four training examples check attention-backend consistency; this is a limited numerical smoke check, not proof of identical learning trajectories.

| Configuration | Mean seconds/update | Turns/second | Conservative peak memory | Result |
| --- | ---: | ---: | ---: | --- |
| SDPA, checkpointing, microbatch 2 | 5.32 | 3.01 | 18.34 GB | Passed |
| SDPA, no checkpointing, up to 16 examples / 32,768 padded tokens | — | — | GPU allocation cap reached | Rejected |
| FlashAttention 2, no checkpointing, up to 16 examples / 32,768 padded tokens | 1.45 | 11.06 | 59.20 GB | Selected |
| FlashAttention 2, no checkpointing, up to 16 examples / 65,536 padded tokens | — | — | GPU allocation cap reached | Rejected |

The selected setting was **3.68× faster** than conservative Large training in this short sample. It uses BF16 autocast with FP32 parameters, fused AdamW, dynamic padding and length sorting only inside each effective batch. Microbatches are split by both example count and `max_sequence_length × batch_size`; loss weighting preserves each example's contribution. No additional input truncation occurs. Initial SDPA/FlashAttention softmax scores differed by at most 0.00943, below the predeclared 0.02 smoke tolerance; bitwise reproducibility across backends is not claimed.

The memory allowance is interpreted as decimal **80,000,000,000 bytes** (74.51 GiB). All new Spark containers have that Docker memory limit and the same memory-plus-swap limit, allowing no additional swap. The PyTorch CUDA allocator is capped at 60,000,000,000 bytes for runtime/host headroom. The selected stress case peaked at 45.69 GB CUDA allocation, 57.29 GB reserved and 59.20 GB reserved plus process RSS. The last quantity is a conservative estimate on unified memory and may double-count shared allocations. Training also records cgroup usage and checks the budget after physical batches and optimizer steps. These samples and allocator limits are not an exact measurement of every driver allocation.

Benchmark outcomes, including rejected cases and their logs, are retained in `evidence/profile-large-01/`. The original benchmark code is in `code-large/`; the full run uses the separate `code-large-train-01/` snapshot. The new runtime restores the attention backend from each checkpoint manifest; older Base manifests default to SDPA.

The container `agentboard-encoder-large-train-01` served the selected Large checkpoint after training; completed artifacts are in `evidence/large-train-01/`. Its smoke checkpoint reloaded with zero score difference. All five full runs completed five epochs without an out-of-memory failure. Their recorded training/validation/checkpoint time totals **2,036.72 seconds (33.95 minutes)**, excluding initial loading, the smoke test, final reporting and archiving. Maximum CUDA allocation was **32.08 GB**, reserved memory **45.76 GB**, and the conservative reserved-plus-RSS estimate **48.65 GB**, below the 80 GB allowance.

The output includes the performance profile and its original code, preflight test log, image ID and package snapshot. Archiving succeeded with ID `a4648866-3edd-42dc-8fb8-d32803320051`. Validation selected learning rate `5e-5`, seed 43, epoch 5, checkpoint hash `b32dd71138af35cd4f62f11e2e38239108595907ac57736a3767c1f25f5f69bd`.

The [comparison report](turn-classifier-comparison.md#loss-curves-and-fit-diagnosis) interprets the quality improvement, late-epoch loss increase and seed instability. Both services returned HTTP 200 for readiness. A synthetic request to Large's classification route returned HTTP 200, category `coding` and the selected checkpoint hash.

Read current progress without changing the job:

```sh
ssh Coral-s-Spark 'cat /home/coral/agentboard-turn-encoder-v1/evidence/large-train-01/progress.json'
ssh Coral-s-Spark 'docker logs --tail 10 agentboard-encoder-large-train-01'
```

Preparation and profiling commands, already exercised in isolated containers:

```sh
python download_modernbert.py --model-id answerdotai/ModernBERT-large --output /evidence/large-model
python prepare_turn_encoder.py --sources /evidence/inputs/sources.json \
  --model /evidence/large-model --output /evidence/dataset-large-01 \
  --match-dataset /evidence/dataset
python benchmark_modernbert.py --dataset /evidence/dataset-large-01 \
  --model /evidence/large-model --output /evidence/profile-large-01
```

The full run uses these selected settings (the output must be a new directory):

```sh
python train_modernbert.py --dataset /evidence/dataset-large-01 --model /evidence/large-model \
  --output /evidence/large-train-01 --archive-home /evidence/archive --mode train \
  --microbatch 16 --attention flash_attention_2 --no-gradient-checkpointing \
  --sort-microbatches --max-padded-tokens 32768 --fused-optimizer \
  --memory-limit-bytes 80000000000 --cuda-memory-limit-bytes 60000000000 \
  --evaluation-policy reused-holdout-comparison
```

Validation alone selects checkpoints and learning rates. Since the existing test results informed follow-up discussion, any repeated test score is a **retrospective comparison**, explicitly labeled in the report; a fresh later cohort is needed for a new untouched holdout estimate. Regularization, class weighting, chunking and new labels are not part of this run. The measured generalization gap and scarce classes remain limitations.

## Dedicated service

[The service](../cronjob/classifier/classifier/encoder_service.py) loads one pinned checkpoint and reuses the training serializer/tokenizer. It accepts up to 16 items, a 2 MiB request body and one concurrent inference request; overload returns 429. Invalid inputs and failed inference are explicit errors. Routine logs and validation errors omit transcript bodies. No Codex process, provider fallback or generated explanation is involved.

On 29 September, Base was ready on Spark loopback port 8766 and Large on port 8767. Both containers were stopped and neither port was listening on 4 October; the incremental batch jobs did not restart these services. Both publish the matching container port. Startup verifies the selection/checkpoint hash and artifact/version contracts. Routes are `/healthz`, `/readyz`, `/v1/model` and `POST /v1/turn-classifications`, as specified in the [API contract](turn-classifier-plan.md#7-dedicated-inference-service-on-spark).

**Post-run rule, clarified 4 October:** stop this experiment's Spark services after execution and verification, preserving its artifacts. Persistent serving requires a separate user request. Check container state, GPU compute processes and ports 8766/8767 before handoff; see the [cleanup requirement](specification.md#turn-purpose-encoder-v1). The post-run check confirmed both service containers and all four incremental-job containers exited with restart policy `no`, both ports closed, and no GPU compute processes.

The launcher can also be used independently once `selection.json` exists:

```sh
python serve_turn_encoder.py --selection /evidence/train-01/selection.json --port 8766
```

From the connected machine, an SSH tunnel keeps inference on Spark:

```sh
ssh -N -L 8766:127.0.0.1:8766 Coral-s-Spark
```

Identical input IDs are required offline and through the service. Identical batching agrees within `1e-5` absolute score in the smoke check. BF16 single versus padded batches showed a maximum 0.00418 absolute score difference on the synthetic smoke examples; the regression allows 0.01 for that comparison and verifies their categories. Near-tied predictions may change with batch shape; uncalibrated scores are not verified correctness probabilities.

## Incremental inference through 3 October 2026

**Completed 2026-10-04.** The expanded dataset contains 1,915 turns from 311 eligible user sessions. All 1,431 prior full input hashes are unchanged. The 484 added turns span 67 sessions: 381 local and 103 Spark turns. Of these, 456 belong to 64 sessions absent from the earlier cohort and 28 extend three previously seen sessions. Every added turn has recorded terminal evidence and nonempty conversational text. None has the same complete classifier-input hash as an earlier target; related-session and near-duplicate independence is not established.

Selection retains the earlier CLI/editor user-session policy, excluding internal/AgentBoard sources and unsupported thread origins. Complete imported files must precede `2026-10-04T00:00:00+01:00`; 18 files containing later activity, including seven spanning the cutoff, were excluded by the import. The prior dataset remains pinned, and the 87 added eligible source files are retained whole. Target plus immediate-predecessor role/content serialization reproduces every earlier input hash. The immutable expanded dataset ID is `65f0ce5a-3609-43ce-a709-c4fdc6cb0f4f`.

Both original selected checkpoints classified all 484 added turns in isolated Spark containers with networking disabled, BF16 autocast, physical batch 2, and the existing 8,192-token right truncation. No training or checkpoint selection occurred. Saved input hashes, retained-token hashes and truncation metadata match between models for every target.

| Measurement | Base | Large |
| --- | ---: | ---: |
| Completed predictions | 484 | 484 |
| Truncated inputs | 2 | 2 |
| Model loading | 9.42 s | 13.18 s |
| Loading plus input preparation/batch inference/output writing | 22.96 s | 28.09 s |
| Peak CUDA allocation | 2.37 GB | 2.20 GB |

These single-run timings exclude container startup and initial artifact verification; they are not HTTP latency or repeated throughput benchmarks. The largest original input has 8,992 tokens and loses 800. Large uses FlashAttention 2 and Base uses SDPA, as saved with their checkpoints.

The encoders agree with each other on **320/484 turns (66.12%)** and disagree on 164. The user explicitly authorized the external transcript transfer, and all eight historical GPT model/effort configurations also classified the same 484 targets through ChatGPT OAuth. Each GPT call receives one full target plus its immediate predecessor, using the existing prompt and taxonomy. GPT inputs are untruncated. This run uses Codex CLI 0.160.0, with up to six concurrent calls per configuration; CLI/runtime and provider revisions are not controlled across historical runs.

| Configuration | Matches with Astra / 484 | Astra agreement | Eight-class macro-F1 |
| --- | ---: | ---: | ---: |
| ModernBERT Base, frozen | 301 | 62.19% | 0.5000 |
| ModernBERT Large, frozen | 350 | 72.31% | 0.6014 |
| GPT-5.6 Luna, low | 347 | 71.69% | 0.6159 |
| GPT-5.6 Luna, max | 392 | 80.99% | 0.7766 |
| GPT-5.6 Sol, xhigh | 408 | 84.30% | 0.7244 |
| GPT-5.6 Terra, low | 400 | 82.64% | 0.8324 |
| GPT-6 Luna, low | 360 | 74.38% | 0.6288 |
| GPT-6 Luna, max | 404 | 83.47% | 0.7257 |
| GPT-6 Sol, xhigh | 421 | 86.98% | 0.8215 |

The reference is GPT-6 Astra xhigh, independently run on all 484 targets. Scores measure agreement with model-generated labels; human adjudication was not performed. Macro-F1 equally weights all eight classes, with zero division set to zero. GPT-6 Sol has the highest overall agreement among the compared configurations; GPT-5.6 Terra has the highest macro-F1. Only two reference targets are creative-media, so rare-class results have little support.

Large exceeds Base agreement by 49 turns (10.12 percentage points): 89 match Astra only with Large, 40 only with Base, 261 with both and 94 with neither. On the 456 turns from new sessions, agreement is 331/456 (72.59%) for Large and 283/456 (62.06%) for Base; on the 28 extending earlier sessions it is 19/28 and 18/28. Of the two truncated inputs, Large matches one and Base matches neither; this slice is too small to characterize truncation quality.

All 3,872 GPT classifications succeeded in 3,873 attempts. One GPT-6 Luna max call timed out after WebSocket connection errors and succeeded on an identical-input retry. Both attempts are retained. Call timing includes CLI startup and transport under concurrent execution and is not a serial model benchmark. The existing 1,431 labels per configuration remain preserved in their original archives; [combined coverage](experiment-storage.md#dataset-and-classification-coverage) is complete for all 1,915 targets.

Verification checks all 4,840 prediction identities and input hashes, every GPT prompt/response contract, and encoder checkpoint hashes, scores and input lengths. The initial Base launch failed before inference because its module path was wrong; its complete log is retained with the successful corrected run and Large run. Prior labels, checkpoints and training partitions remain unchanged. Spark execution archive `7aef9872-52ea-4cf1-aca3-59762f2fbd0b` pins both original checkpoint archives. All 32 files were also verified from the host account after correcting this run's container-created ownership.

Final comparison archive `0f727fbe-dfea-44a8-8e97-6302c635271c` pins all eight GPT runs, prior labels, the expanded dataset and encoder evidence. Its standalone analysis regenerated all 11 result files byte-for-byte. The archive includes consolidated labels, reference answers, disagreements, per-class metrics, cohort slices, usage and execution evidence. Earlier encoder-only report `29e13b4a-ce61-401c-a308-995ef60c017a` remains immutable; its pending-GPT status records the state before transfer approval.

## Verification

- Offline backend after the inherited-metadata importer fix: `pytest -m 'not e2e' -q` — 639 passed, six live tests deselected.
- Frontend: `node --test frontend/tests/*.test.cjs` — 54 passed.
- Independent classifier: `PYTHONPATH=cronjob/classifier .venv/bin/pytest cronjob/classifier/tests -q` — 79 passed; three local skips (older optional ML round trip, new PyTorch accumulation regression and explicit Spark GPU test).
- Base Spark checks: real 8,192-token smoke training/save/reload and 17 synthetic direct-service/tokenizer tests passed. Selected-checkpoint HTTP readiness was confirmed in the original run.
- Large Spark: real smoke training/save/reload passed with zero prediction difference; 40 tests passed for batching, uneven gradient accumulation, source/split integrity, tokenizer boundaries and direct-service parity. Tests used `TURN_ENCODER_SMOKE_MODEL=/evidence/large-preflight-01/smoke` and the three focused files `test_encoder_batches.py`, `test_turn_encoder.py`, `test_spark_encoder.py`. Two larger benchmark candidates hit the intentional CUDA allocation cap and were rejected; the selected setting passed.
- Python lint, repository formatting and `git diff --check` passed. The locked developer setup preserved the existing dev database.

The September training completion check confirmed all five Large manifests, the frozen selection, final report, successful archive, both readiness routes and one synthetic selected-model HTTP classification. The October incremental analysis passed five synthetic metric-parity cases and a synthetic end-to-end report/consolidation check before real-result verification and deterministic replay. Documentation links and `git diff --check` passed; application tests were not repeated for this results-only update. HTTP throughput has not been benchmarked. Browser and private integration tests were not run: no app UI or Codex integration changed. The separately authorized classifier experiments use frozen Spark inference and ChatGPT OAuth as recorded above.

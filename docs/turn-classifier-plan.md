# Turn-purpose encoder v1 plan

**Base and Large comparisons complete — 2026-09-29.** Fine-tune a BERT-like encoder to classify individual completed turns, using saved GPT-6 Astra labels. Train and serve it on the owner's local DGX Spark. This document owns the accepted experiment workflow; the [specification](specification.md#turn-purpose-encoder-v1) owns product requirements and acceptance.

The user authorized implementation and training on 2026-09-29. The Base and subsequent Large comparisons, reports, archives and dedicated Spark deployments are complete. Large used the authorized 80 GB memory allowance for faster training. The [execution record](turn-classifier-run.md) owns actual settings, measured preparation/results, commands and current limitations. HTTP throughput benchmarking remains unperformed.

## 1. Decisions and scope

| Item | V1 decision |
| --- | --- |
| Prediction unit | One recorded target turn, including its human inputs and recorded assistant text. |
| Supervision | All usable saved GPT-6 Astra `xhigh`, independent, turn labels across the experiment's splits. |
| Categories | The eight IDs and definitions in the existing [purpose taxonomy](session-purpose.md#categories), `session-purpose-v1`. |
| Input scope | The complete normalized target turn and immediately preceding turn, before the encoder's explicit truncation. |
| Initial model | `answerdotai/ModernBERT-base` with its standard sequence-classification head. |
| Authorized follow-up | `answerdotai/ModernBERT-large`, preserving the frozen data and optimizer comparison; benchmark speed within 80 GB. |
| Length policy | One sequence capped at 8,192 tokens, including markers and special tokens. No chunking or aggregation in v1. |
| Data split | Whole sessions stay together; earlier groups train, middle groups validate, latest groups test. |
| Execution | Training and a dedicated encoder inference service on Spark. The user authorized private dataset copies to this owned local machine. |
| Model testing | A dedicated encoder/service test harness. Existing Codex/LLM integration tests retain their private-endpoint policy. |

The experiment should establish agreement with Astra and measured serving performance. It does not replace the existing session classifier automatically. UI integration, writes to production classifications, generated explanations, additional Astra labeling, chunk aggregation and model searches beyond the authorized Large comparison remain follow-up scope.

## 2. Verified starting evidence

Read-only checks on 2026-09-29 verified the selected dataset and reference artifact/manifest hashes, unique session/turn identities, and label-to-input hash matches: **1,431 labeled turns from 247 sessions**. All turns have `started_at`; three targets contain no recorded conversational text and remain in the dataset. The reference configuration is GPT-6 Astra `xhigh`, independent.

| Saved category | Turns |
| --- | ---: |
| `writing` | 194 |
| `coding` | 472 |
| `bug-fixing` | 169 |
| `research` | 133 |
| `analysis` | 58 |
| `creative-media` | 6 |
| `guidance` | 309 |
| `other` | 90 |
| **Total** | **1,431** |

These are Model-generated labels, not human adjudications. The exact historical rubric and input-building code were inspected. They classify the target's intended outcome; preceding context only resolves references and short follow-ups. Other models' answers and later turns are excluded.

Resolve the private machine archive through `~/.agentboard/data/experiments/sync/current-classification.json`: `dataset` selects the turn array, `reference_answers` selects Astra's answers, and `comparison_reference` identifies the model configuration. This navigation is mutable; freeze its pinned references and verify their manifest/artifact hashes before use. Resolve the reference's source-result dependency and retain the archived classification prompt. Never execute archived scripts merely to read results.

Record exact source references in private run artifacts, not repository documentation. A changed navigation file must not silently change a prepared experiment. Original archives, saved answers and prior experiment manifests remain immutable. Tokenizer lengths, truncation and Spark resources were subsequently measured; see the [execution record](turn-classifier-run.md). Language distribution remains unmeasured.

## 3. Prepare and split the dataset

### Source mapping and validation

| Source field | Use |
| --- | --- |
| `original_codex_session_id`, `original_codex_turn_id` | Join turns to answers; retain identity for grouping and provenance, never as model features. |
| `classification_input_sha256` | Verify the complete original LLM input, including preceding context, before deriving encoder input. |
| `messages[].role`, `messages[].content` | Preserve normalized conversational text and message order. |
| `previous_turn_index` | Resolve the immediately preceding turn. Null means no preceding context. A non-null unresolved or cross-session reference is an error. |
| `started_at` | Parse and normalize RFC 3339 timestamps to UTC for chronological grouping. Missing/invalid values are reported, never replaced with zero or file modification time. |
| `answers[].category` | One supervised class from the unchanged eight-category taxonomy. |
| `answers[].reason` | Retain for audit only; never include in training or inference input. |

Require exactly one usable Astra answer per selected target. Reject duplicate/conflicting answers, unsupported categories and mismatched input hashes before training. Preserve empty targets explicitly; distinguish an empty message list from a missing or malformed field. Report invalid records and resolve them rather than silently changing the experiment denominator. Preserve all source text and raw rollout events; model input preparation does not rewrite the archives.

### Chronological session groups

1. Group by original session identity. Audit recorded fork relationships and substantial shared conversation prefixes; keep confirmed related/duplicate conversations in the same group. Short generic replies alone do not establish that unrelated sessions are duplicates. Record the audit method and unresolved cases.
2. Order groups by their **latest included target `started_at`**, normalized to UTC, with a stable identity-based tie break. This keeps recently active sessions in later partitions. Source identities are ordering metadata only.
3. Initially allocate approximately **70% / 15% / 15% of groups** to train/validation/test. Ratios refer to groups, not turns; actual turn and category counts depend on session size. Keep all turns and predecessor context from a group in that partition.
4. A session spanning a date boundary remains whole in the partition selected by its latest target timestamp. Its earlier turns move with it. Report minimum/maximum timestamps and temporal overlap: retaining complete sessions does not guarantee that every test turn is later than every training turn. A stricter time embargo would require a separately documented dataset policy.
5. Freeze the split manifest before fitting models or selecting hyperparameters. Record group membership, cutoffs, ordering/tie rules, source hashes and per-split counts. Assert pairwise-disjoint session identities and grouping keys. Do not reshuffle to improve test class balance or results.

Class weighting, vocabulary fitting for baselines and other learned transformations use training data only. Validation controls model selection and optional calibration. Test data was reserved for the first frozen Base comparison. The authorized Large follow-up keeps that partition for a retrospective comparison, explicitly marked `reused-holdout-comparison`; it is no longer an untouched holdout. Validation still selects checkpoints. A fresh later cohort is needed to establish an unbiased follow-up estimate.

## 4. Encoder input and truncation

Use a shared, versioned preprocessing function in dataset preparation and serving. It accepts only the selected target and preceding-turn role/content pairs. It preserves recorded assistant text, including reasoning summaries already present in those messages. Tool bodies, injected context, nontext media and later turns remain outside this historical input selection.

**Implemented v1 serialization:** write target messages first, followed by preceding-turn messages, with explicit target/context and role markers. Preserve chronological message order within each section. Mark an absent preceding turn and an empty target explicitly. Use ordinary text markers with the existing tokenizer; v1 does not require a new token vocabulary. This changes presentation order from the LLM input while preserving its selected text before truncation.

Tokenize with a pinned tokenizer revision and **right truncation at 8,192 tokens**, including all markers and special tokens. This retains the beginning of the target first. If the target fits, remaining space holds the beginning of the preceding context. If the target itself exceeds the budget, its ending and all preceding context are omitted. The same deterministic rule applies to every split and every inference request.

The [ModernBERT configuration](https://huggingface.co/answerdotai/ModernBERT-base/blob/main/config.json) specifies 8,192 positions. Its [standard sequence classifier](https://huggingface.co/docs/transformers/model_doc/modernbert#modernbertforsequenceclassification), together with [tokenizer truncation](https://huggingface.co/docs/transformers/main_classes/tokenizer), supports one retained sequence per example. Batch padding is separate and uses an attention mask; padding must not count as retained evidence. This token budget is unrelated to the existing session classifier's character cap.

Persist the following metadata for prepared examples and service predictions:

| Field | Meaning |
| --- | --- |
| Complete source-input hash | The verified original classifier input, before encoder serialization or truncation. |
| Serialized-input hash | Exact full encoder text under the recorded template version. |
| Effective-input hash | Hash of the unpadded retained token-ID sequence under a documented canonical encoding. |
| Model/tokenizer revisions and preprocessing version | Reproduce the model's interpretation and input transformation. |
| `max_tokens`, truncation side | `8192`, `right` for this configuration. |
| `original_token_count` | Full formatted input length including special tokens, before truncation. |
| `used_token_count` | Tokens actually presented to the model, excluding padding. |
| `dropped_token_count`, `truncated` | `original - used`; true exactly when the difference is positive. |

**Synthetic arithmetic example:** 12,000 original tokens become 8,192 used tokens and 3,808 dropped tokens. This is not a measured dataset example. Preserve full inputs and labels alongside derived metadata so later experiments can use a different length policy without recovering deleted text.

## 5. Spark environment and training

### Preparation

Use the existing Spark connection. Check GPU availability, current workloads, available memory/disk, driver/CUDA versions and ARM64 package compatibility before installation or execution. Select an isolated, pinned environment or container following [NVIDIA's Spark PyTorch guidance](https://build.nvidia.com/spark/pytorch-fine-tune/instructions). Record image digest or dependency lock, Python/PyTorch/Transformers versions, attention implementation, code revision and model/tokenizer revisions. Verify copied private artifacts against their source hashes.

Complete the [worktree setup](development.md#automatic-worktree-data-setup) before code development. Reconcile the implementation branch with the repository's experiment-storage tooling before adding producers; the planning checkout predates that tooling. Preserve existing dev databases and checkpoints. Training uses the pinned experiment artifacts, not the live collector or shared baseline as a writable dataset. Do not interrupt existing Spark jobs or repurpose existing services.

### Model and bounded first experiment

Fine-tune ModernBERT-base and a new eight-class head using cross-entropy against Astra's hard category labels. Each turn supplies one sequence and one loss. Saved reasons and other classifiers' predictions remain outside the features. Use standard model/Trainer or PyTorch training facilities; there is no chunk grouping, aggregation layer or generative decoder.

Implemented initial settings; the execution record pins the actual environment and microbatch:

| Setting | Initial proposal |
| --- | --- |
| Maximum sequence length | 8,192 throughout training, validation, test and serving. |
| Optimizer | AdamW; weight decay `0.01`. |
| Learning rate | Start with `2e-5`; bounded comparison with `1e-5` and `5e-5`. |
| Duration | At most five epochs per run; validate each epoch and stop after two checks without improvement. |
| Batch | Target effective batch size 16; choose microbatch size and gradient accumulation from measured memory. |
| Schedule | Linear decay with 10% warmup. |
| Precision | BF16 only after confirming supported, stable execution; record any alternative explicitly. |
| Memory controls | Dynamic padding; gradient checkpointing if needed. Do not silently lower the input limit to fit memory. |
| Selection | Best validation macro-F1 over the fixed eight classes; use lower validation loss for ties. |
| Repeatability | One fixed seed for the three learning-rate candidates; two additional seeds for the selected configuration, at most five full runs. |

First run a small smoke test using training/validation examples only: finite loss/gradients, a working optimizer step, valid labels, checkpoint save/reload and a measured throughput/memory estimate. A smoke test is not quality evidence. Estimate total runtime from that measurement; no training duration or accuracy is promised before it exists.

Use ordinary cross-entropy for the first comparison. Consider training-only class weights later if validation shows a material benefit; that is an additional recorded experiment, not an unreported change during the five-run plan. Save every run's configuration, curves, selected checkpoint and termination reason. Choose the serving checkpoint using validation only. Confidence calibration, if supported by validation sample size, also uses validation only and is stored with the checkpoint; otherwise publish raw model scores as uncalibrated.

For Large, first benchmark SDPA versus FlashAttention 2, larger physical batches, disabling gradient checkpointing and fused AdamW. Cap the container at 80,000,000,000 bytes without extra swap and the PyTorch GPU allocator at 60,000,000,000 bytes, leaving headroom for host/runtime allocations. Record allocator peaks, process RSS and container memory. Keep effective batch 16: sort lengths only inside each already selected optimizer batch, then split it by maximum examples and padded-token budget. Weight each microbatch loss by its actual example count, including the final partial optimizer batch. This changes execution order and floating-point behavior, but does not drop turns, change optimizer-batch membership or shorten inputs. Preserve failed benchmark evidence and the Base service.

## 6. Evaluation and interpretation

Freeze the selected model, preprocessing and any calibration before evaluating test predictions. Evaluate a training-majority baseline and a TF-IDF/linear-classifier baseline using the same split and retained input text; fit them using training data only. Existing saved LLM comparisons may be recomputed on the exact held-out identities and full-input hashes. Do not compare a new test-set score with an old whole-cohort score as if they used the same denominator, and do not make fresh LLM calls for this v1 experiment.

Report:

- Category agreement with Astra, eight-class macro-F1, per-category precision/recall/F1 and support, and the full confusion matrix. Define zero-division handling; mark absent classes unsupported rather than implying measured success.
- Results separately for truncated and complete inputs, empty targets, and each source host when sample counts permit. Report original/used/dropped-token distributions and truncation frequency by split and category.
- Confidence reliability when calibration is attempted, including its validation procedure and sample size. A softmax score by itself is not established correctness probability.
- Uncertainty from resampling whole session groups rather than independent turns, and validation variation across seeds. Very small categories remain inconclusive even with a confidence interval.
- Spark cold-start time, warm end-to-end latency (median/p95), throughput and peak memory under recorded batch sizes, concurrency, precision and input-length distributions. Separate preprocessing/HTTP overhead from model execution when useful. Historical OAuth experiment timing is not a controlled service-latency comparator.

Keep per-turn predictions and discrepancies private, with split membership and input/checkpoint hashes. Preserve reference labels when the encoder disagrees. The three empty targets remain in the denominator and receive separate analysis. No automatic relabeling, exclusion of inconvenient long examples or synthetic replacement of rare held-out classes is permitted.

No numerical quality or latency threshold has been agreed. Completion means delivering reproducible results, including negative findings; it does not imply production suitability. The report should explain whether the encoder improves on the simple baselines and whether its measured quality/performance trade-off warrants adoption.

## 7. Dedicated inference service on Spark

Implement a separate Python service that loads the selected encoder checkpoint once, calls `eval()` and serves with gradients disabled. Keep heavy training/encoder dependencies optional and separate from the ordinary AgentBoard installation. Reuse exactly the training preprocessing module, taxonomy mapping and saved tokenizer; startup fails if required versions/artifacts are inconsistent.

Implemented API; Base and Large use separate ports when serving. After experiment execution and verification, stop the run's Spark services and retain all checkpoints, logs and archives. Verify that its containers and GPU processes have exited and its inference ports are closed before handoff. Persistent serving requires a separate user request; the [execution record](turn-classifier-run.md#dedicated-service) owns current deployment status.

| Route | Contract |
| --- | --- |
| `POST /v1/turn-classifications` | Accept an `items` array containing one or more requests. Each has an opaque `request_id`, `target_turn` role/content pairs and `preceding_turn_context` role/content pairs. Return results in request order. |
| `GET /healthz` | Process health only. |
| `GET /readyz` | Whether the pinned model and tokenizer are loaded and ready. |
| `GET /v1/model` | Model/checkpoint/tokenizer identity, taxonomy, preprocessing version, token cap and score-calibration status. |

Each classification result includes `request_id`, the selected category, eight category scores, calibrated/uncalibrated status, model/checkpoint provenance, input hashes and the truncation metadata above. It does not invent a natural-language reason. The existing session result's required `reason` means it cannot simply be substituted into that contract; any later AgentBoard adapter needs an explicit turn-level contract.

Batch by retained sequence length where useful, preserving request identity and output order. Bound request bytes, batch size and concurrency; reject malformed roles/content and surface overload/model errors. Empty but well-formed targets are valid inputs. No training occurs during requests and no failure silently invokes another model. Keep transcript bodies out of routine service logs.

Choose an unused Spark port and connection route during setup, using the existing local access arrangement. Record them in the deployment manifest. Leave the private LLM endpoint and the AgentBoard collector/dev endpoints intact. Service tests call this encoder directly, with no Codex process or login dependency; the existing [Codex/LLM test policy](testing.md#private-model-tests) continues to govern those separate integrations.

## 8. Implementation sequence and artifacts

| Stage | Work and completion evidence |
| --- | --- |
| 1. Prepare | Confirm implementation revision, worktree setup and Spark environment; pin dataset/reference/model/tokenizer sources. |
| 2. Validate data | Verify joins, hashes, categories, predecessor links, dates and duplicate/fork grouping; save the source manifest and audit report. |
| 3. Freeze inputs | Save chronological split assignments, shared preprocessing, token-length/truncation audit and synthetic regressions. |
| 4. Train | Pass the smoke test; execute the bounded training matrix; save configurations, logs, curves, checkpoints and validation selection. |
| 5. Evaluate | Freeze the candidate; run final test/baseline comparisons and produce the metrics, uncertainty and error analysis. |
| 6. Serve | Launch the pinned Spark service; verify offline/service prediction parity and measure serving behavior. |
| 7. Cleanup | Finalize the evidence, stop the experiment's Spark services, and verify container/GPU exit and closed inference ports. |
| 8. Deliver | Supply the checkpoint/tokenizer/config bundle, preparation/training/evaluation/serving entry points, deployment instructions and report. |

Share pure input validation and serialization between the experiment scripts and service. Keep source resolution/splitting, model training, metric reporting and HTTP serving as separate responsibilities. Actual filenames and dependency pins should follow the implementation branch's conventions. Document runnable commands only after the entry points exist and have been exercised.

Record experiments through the existing durable archive tooling on the chosen implementation branch. Pin inputs and dependencies, retain all attempted runs and checkpoint hashes, and finalize new result bundles without rewriting historical evidence. Private datasets, predictions, checkpoints and manifests remain outside Git; committed tests use synthetic text. Repository documentation contains aggregate results and limitations only.

## 9. Verification and acceptance

During implementation, add focused regressions for source-hash/label joins, session isolation, deterministic date ordering and boundary handling, predecessor validation, and exclusion of labels/IDs from features. Test token limits just below/at/above 8,192, special-token accounting, empty target/context, oversized targets, removal of preceding context, and padding-independent token counts/hashes.

Check checkpoint save/reload and label mapping, identical offline/service token IDs, single/batch prediction consistency within documented numerical tolerance, output ordering and service readiness/error behavior. GPU execution and the dedicated Spark HTTP checks belong to the encoder harness; they are not evidence that Codex or an LLM endpoint works.

Run the [routine repository checks](testing.md#routine-checks-before-handoff) after code changes, plus the focused encoder tests and Spark checks. Browser verification applies only if a later change touches the app UI, using the in-app browser. Schema checks apply only if Codex/app-server integration changes. Report commands, results and skipped/blocked checks with their reasons.

Before handoff, verify that the report is reproducible from pinned artifacts, every example has split/input provenance, checkpoint selection uses validation only, test reuse is disclosed, and the deployed service identifies the selected checkpoint and length policy. Dataset correctness, model quality and serving performance must be reported separately.

## 10. Trade-offs, limitations and follow-ups

- **Simpler first version:** one bounded sequence per turn supports standard encoder training and serving. It avoids custom chunk aggregation, but measured memory/latency still depend on sequence lengths and batch configuration.
- **Information loss:** truncation can discard corrections, conclusions or changes of intent. The target-first proposal protects its opening request, but long targets lose their ending; a short follow-up may lose the preceding evidence needed to interpret it.
- **Unequal reference context:** Astra saw the full selected input. The encoder's label may depend on discarded evidence. Agreement therefore measures both the model and this preprocessing trade-off. It is not an equal-context comparison on truncated examples.
- **Reference limitations:** Astra's inferred labels may be wrong or inconsistent. Learning them can reproduce those errors; neither agreement nor confidence calibration establishes human-verified correctness.
- **Small, uneven dataset:** 247 sessions provide limited independent evidence, and `creative-media` has only six examples in the entire cohort. Chronological splits can omit a category from training or evaluation. Report that gap; do not change the taxonomy or split to conceal it.
- **Chronology and duplication:** grouping prevents the same session crossing splits, but unrecognized forks/shared text can still leak. Whole sessions spanning cutoffs also cause timestamp overlap, which must be disclosed.
- **Input coverage:** this is a text classifier for completed recorded turns. Excluded media/tool bodies, missing source evidence and other languages may change performance. Generalization to new projects, dates or hosts remains unestablished until tested.
- **Output scope:** category scores replace neither explanatory reasons nor the existing session contract. UI and production integration need a subsequent, explicit design.

If the v1 results justify further work, compare alternative truncation policies, a longer-context encoder or chunk aggregation as separately versioned experiments. Additional labeled data, human review and rare-category coverage may matter more than a more complex model. These options are deferred, not promised v1 features.

## 11. Documentation verification

The category counts above sum to 1,431. The [execution record](turn-classifier-run.md#verification) records automated checks, the real Spark smoke run, current training state and remaining acceptance work. Documentation and smoke checks do not establish final model quality.

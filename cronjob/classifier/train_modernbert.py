"""Bounded Spark fine-tuning, validation-only selection and a frozen test report."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from classifier.artifacts import (
    config_dict,
    environment,
    manifest_hash,
    new_directory,
    read_json,
    read_rows,
    seal,
    verify,
    write_json,
    write_rows,
)
from classifier.encoder_batches import configure_memory, memory_usage, microbatches, validate_memory_limits
from classifier.encoder_runtime import Encoder, load_model, probabilities, tensor_batch
from classifier.evaluation import metrics
from classifier.taxonomy import CATEGORIES, TAXONOMY_VERSION
from classifier.turn_encoder import PREPROCESSING


@dataclass
class Config:
    dataset: Path
    model: Path
    output: Path
    archive_home: Path | None = None
    microbatch: int = 2
    effective_batch: int = 16
    epochs: int = 5
    patience: int = 2
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    device: str = "cuda"
    attention: str = "sdpa"
    gradient_checkpointing: bool = True
    sort_microbatches: bool = False
    max_padded_tokens: int | None = None
    fused_optimizer: bool = False
    memory_limit_bytes: int | None = None
    cuda_memory_limit_bytes: int | None = None
    evaluation_policy: str = "original-holdout"


def progress(path: Path, **value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)
    print(json.dumps(value, allow_nan=False), flush=True)


def scored(rows, values):
    predicted = [CATEGORIES[max(range(len(CATEGORIES)), key=p.__getitem__)] for p in values]
    return metrics([r["category"] for r in rows], predicted)


@dataclass(frozen=True)
class TrainingStep:
    loss_sum: float
    gradient_norm: float
    memory: dict[str, int]
    physical_batches: int


def train_step(model: Any, tokenizer: Any, block: list[dict], optimizer: Any, config: Config) -> TrainingStep:
    import torch

    optimizer.zero_grad(set_to_none=True)
    total_loss, peak_memory, physical_batches = 0.0, {}, 0
    for batch in microbatches(block, config.microbatch, config.max_padded_tokens, config.sort_microbatches):
        labels = torch.tensor([CATEGORIES.index(r["category"]) for r in batch], device=config.device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=config.device.startswith("cuda")):
            loss = model(**tensor_batch(tokenizer, batch, config.device), labels=labels).loss
        if not torch.isfinite(loss):
            raise ValueError("Non-finite training loss")
        (loss * len(batch) / len(block)).backward()
        total_loss += loss.item() * len(batch)
        physical_batches += 1
        if config.memory_limit_bytes is not None:
            sample = memory_usage(config.memory_limit_bytes)
            peak_memory = {k: max(v, peak_memory.get(k, 0)) for k, v in sample.items()}
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
    optimizer.step()
    sample = memory_usage(config.memory_limit_bytes)
    peak_memory = {k: max(v, peak_memory.get(k, 0)) for k, v in sample.items()}
    return TrainingStep(total_loss, norm.item(), peak_memory, physical_batches)


def train_one(config: Config, lr: float, seed: int, *, smoke: bool = False):
    import torch
    from transformers import get_linear_schedule_with_warmup, set_seed

    set_seed(seed)
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise ValueError("This Spark configuration requires CUDA with BF16 support")
    configure_memory(config.memory_limit_bytes, config.cuda_memory_limit_bytes)
    training = read_rows(config.dataset / "train.jsonl")
    validation = read_rows(config.dataset / "validation.jsonl")
    if smoke:
        # Exercise the longest training input and dynamic padding, never the test set.
        training = [
            max(training, key=lambda r: r["used_token_count"]),
            min(training, key=lambda r: r["used_token_count"]),
        ]
        validation = validation[:2]
    output = new_directory(config.output / ("smoke" if smoke else f"lr-{lr:g}-seed-{seed}"))
    model, tokenizer = load_model(config.model, config.device, fresh=True, attention=config.attention)
    if config.gradient_checkpointing:
        model.gradient_checkpointing_enable()
    torch.cuda.reset_peak_memory_stats()
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=lr,
        weight_decay=config.weight_decay,
        **({"fused": True} if config.fused_optimizer else {}),
    )
    epochs = 1 if smoke else config.epochs
    steps_per_epoch = math.ceil(len(training) / config.effective_batch)
    total_steps = steps_per_epoch * epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, math.ceil(total_steps * config.warmup_ratio) if not smoke else 0, total_steps
    )
    generator = torch.Generator().manual_seed(seed)
    best, stale, history, step = (-1.0, -math.inf), 0, [], 0
    run_memory, physical_batches = {}, 0
    started = time.perf_counter()
    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(len(training), generator=generator).tolist()
        ordered = [training[i] for i in order]
        train_loss = 0.0
        for offset in range(0, len(ordered), config.effective_batch):
            block = ordered[offset : offset + config.effective_batch]
            result = train_step(model, tokenizer, block, optimizer, config)
            train_loss += result.loss_sum
            physical_batches += result.physical_batches
            run_memory = {k: max(v, run_memory.get(k, 0)) for k, v in result.memory.items()}
            scheduler.step()
            step += 1
            if step == 1 or step % 5 == 0:
                progress(
                    config.output / "progress.json",
                    phase="smoke" if smoke else "training",
                    run=output.name,
                    epoch=epoch,
                    optimizer_step=step,
                    total_planned_steps=total_steps,
                    recent_loss=result.loss_sum / len(block),
                    gradient_norm=result.gradient_norm,
                    elapsed_seconds=time.perf_counter() - started,
                    peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
                    memory=run_memory,
                    physical_batches=physical_batches,
                )
        values = probabilities(model, tokenizer, validation, config.device, config.microbatch)
        score = scored(validation, values)
        validation_loss = -sum(
            math.log(max(p[CATEGORIES.index(r["category"])], 1e-30))
            for r, p in zip(validation, values, strict=True)
        ) / len(validation)
        entry = {
            "epoch": epoch,
            "train_loss": train_loss / len(training),
            "validation_loss": validation_loss,
            "validation": score,
        }
        history.append(entry)
        progress(output / "history.json", epochs=history)
        key = (score["macro_f1"], -validation_loss)
        if key > best:
            best, stale, best_epoch = key, 0, epoch
            model.save_pretrained(output / "model", safe_serialization=True)
            tokenizer.save_pretrained(output / "model")
        else:
            stale += 1
        if stale >= config.patience:
            break
    elapsed, peak = time.perf_counter() - started, torch.cuda.max_memory_allocated()
    del model, optimizer, scheduler
    torch.cuda.empty_cache()
    # The smoke run checks actual saved-weight reload and matching predictions.
    if smoke:
        reloaded, saved_tokenizer = load_model(output / "model", config.device, attention=config.attention)
        restored = probabilities(reloaded, saved_tokenizer, validation, config.device, config.microbatch)
        difference = max(
            abs(a - b)
            for before, after in zip(values, restored, strict=True)
            for a, b in zip(before, after, strict=True)
        )
        if difference > 1e-5:
            raise ValueError("Smoke save/reload prediction mismatch")
        del reloaded
        torch.cuda.empty_cache()
    return seal(
        output,
        "turn-encoder-model",
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
        environment=environment(),
        base_model=read_json(config.model / "source.json"),
        dataset_manifest_sha256=manifest_hash(config.dataset),
        configuration=config_dict(config),
        lr=lr,
        seed=seed,
        precision="bf16-autocast",
        attention=config.attention,
        gradient_checkpointing=config.gradient_checkpointing,
        best_epoch=best_epoch,
        validation_macro_f1=best[0],
        validation_loss=-best[1],
        training_seconds=elapsed,
        peak_gpu_allocated_bytes=peak,
        memory=run_memory,
        physical_batches=physical_batches,
        termination="early-stopping" if stale >= config.patience else "epoch-limit",
        smoke=smoke,
        smoke_reload_max_difference=difference if smoke else None,
    )


def report(config: Config, selected: Path):
    """Called only after validation selection has been persisted. No model changes here."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    rows = read_rows(config.dataset / "test.jsonl")
    train = read_rows(config.dataset / "train.jsonl")
    started = time.perf_counter()
    encoder = Encoder(selected)
    load_seconds = time.perf_counter() - started
    items = [
        {
            "request_id": str(i),
            "target_turn": r["target_turn"],
            "preceding_turn_context": r["preceding_turn_context"],
        }
        for i, r in enumerate(rows)
    ]
    predictions, timings = [], []
    # Warm-up does not inspect reference labels.
    encoder.predict(items[:1])
    for item in items:
        tick = time.perf_counter()
        predictions.extend(encoder.predict([item]))
        timings.append(time.perf_counter() - tick)
    for row, prediction in zip(rows, predictions, strict=True):
        if (
            row["input_sha256"] != prediction["input_sha256"]
            or row["effective_input_sha256"] != prediction["effective_input_sha256"]
        ):
            raise ValueError("Offline/service preprocessing mismatch")
    output = new_directory(config.output / "evaluation")
    write_rows(
        output / "predictions.jsonl",
        [
            dict(p, session_id=r["session_id"], turn_id=r["turn_id"], group_id=r["group_id"])
            for p, r in zip(predictions, rows, strict=True)
        ],
    )
    truth = [r["category"] for r in rows]
    guesses = [p["category"] for p in predictions]
    majority = Counter(r["category"] for r in train).most_common(1)[0][0]
    baseline = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=50000),
        LogisticRegression(C=1.0, max_iter=1000, random_state=42),
    )

    # Decode exactly the retained unpadded tokens for the text baseline.
    def text(part):
        return [encoder.tokenizer.decode(r["input_ids"], skip_special_tokens=True) for r in part]

    baseline.fit(text(train), [r["category"] for r in train])
    linear = baseline.predict(text(rows)).tolist()
    slices = {}
    subsets = {
        "truncated": [i for i, r in enumerate(rows) if r["truncated"]],
        "complete": [i for i, r in enumerate(rows) if not r["truncated"]],
        "empty_target": [i for i, r in enumerate(rows) if not r["target_turn"]],
    }
    for host in sorted({r["source_host"] for r in rows}):
        subsets[f"host:{host}"] = [i for i, r in enumerate(rows) if r["source_host"] == host]
    for name, indices in subsets.items():
        slices[name] = metrics([truth[i] for i in indices], [guesses[i] for i in indices])
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[row["group_id"]].append(i)
    rng, samples = random.Random(42), []
    for _ in range(1000):
        ids = [i for group in rng.choices(list(groups), k=len(groups)) for i in groups[group]]
        samples.append(metrics([truth[i] for i in ids], [guesses[i] for i in ids])["macro_f1"])
    samples.sort()
    timings.sort()
    result = {
        "evaluation_policy": config.evaluation_policy,
        "interpretation": "agreement with saved Astra, not human accuracy",
        "encoder": metrics(truth, guesses),
        "majority": metrics(truth, [majority] * len(rows)),
        "tfidf_logistic": metrics(truth, linear),
        "slices": slices,
        "session_group_bootstrap_macro_f1_95pct": [samples[24], samples[974]],
        "timing": {
            "batch_size": 1,
            "concurrency": 1,
            "cold_load_seconds": load_seconds,
            "warm_median_seconds": timings[len(timings) // 2],
            "warm_p95_seconds": timings[round((len(timings) - 1) * 0.95)],
            "turns_per_second": len(rows) / sum(timings),
            "includes_http": False,
        },
        "calibration": "not attempted: small chronological validation cohort; raw softmax only",
        "unsupported_test_categories": [c for c in CATEGORIES if c not in truth],
    }
    write_rows(
        output / "baseline-predictions.jsonl",
        [
            {
                "session_id": r["session_id"],
                "turn_id": r["turn_id"],
                "input_sha256": r["input_sha256"],
                "majority": majority,
                "tfidf_logistic": guess,
            }
            for r, guess in zip(rows, linear, strict=True)
        ],
    )
    write_json(output / "report.json", result)
    seal(
        output,
        "turn-encoder-evaluation",
        checkpoint_sha256=manifest_hash(selected),
        dataset_manifest_sha256=manifest_hash(config.dataset),
    )
    return result


def archive_run(config: Config, outcome: str):
    from agentboard.experiments.archive import Archive

    archive = Archive(str(config.archive_home))
    recorder = archive.begin(
        "agentboard",
        "modernbert-large-turn-v1"
        if read_json(config.model / "source.json")["model_id"].endswith("-large")
        else "modernbert-turn-v1",
        metadata={
            "configuration": config_dict(config),
            "environment": environment(),
            "code": {"snapshot": "code/", "entrypoint": "train_modernbert.py"},
            "source_provenance": read_json(config.dataset / "sources/sources.json"),
        },
    )
    for directory, prefix in ((config.output, "outputs"), (config.dataset, "inputs/dataset")):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                recorder.add_file(path, f"{prefix}/{path.relative_to(directory).as_posix()}")
    code = Path(__file__).parent
    for path in sorted(code.rglob("*.py")):
        if "__pycache__" not in path.parts and ".venv" not in path.parts:
            recorder.add_file(path, f"code/{path.relative_to(code).as_posix()}")
    for name in ("Dockerfile.spark", "pyproject.toml", "uv.lock"):
        recorder.add_file(code / name, f"code/{name}")
    recorder.finalize(outcome=outcome)
    print({"archive_id": recorder.id, "outcome": outcome}, flush=True)


def validate_config(config: Config) -> None:
    if any(
        type(v) is not int or v <= 0
        for v in (config.microbatch, config.effective_batch, config.epochs, config.patience)
    ):
        raise ValueError("Batch sizes, epochs and patience must be positive integers")
    if config.microbatch > config.effective_batch or config.epochs > 5:
        raise ValueError("Microbatch cannot exceed effective batch; v1 is bounded to five epochs")
    if config.attention not in ("sdpa", "flash_attention_2"):
        raise ValueError("Unsupported encoder attention backend")
    if config.max_padded_tokens is not None and (
        type(config.max_padded_tokens) is not int or config.max_padded_tokens < 8192
    ):
        raise ValueError("The physical token budget must retain complete 8192-token inputs")
    if config.evaluation_policy not in ("original-holdout", "reused-holdout-comparison"):
        raise ValueError("Unsupported evaluation policy")
    validate_memory_limits(config.memory_limit_bytes, config.cuda_memory_limit_bytes)


def run(config: Config, mode: str) -> None:
    validate_config(config)
    meta = verify(config.dataset, "turn-encoder-dataset")
    if meta["preprocessing"] != PREPROCESSING or meta["base_model"] != read_json(
        config.model / "source.json"
    ):
        raise ValueError("Dataset/model preprocessing pin differs")
    new_directory(config.output)
    write_json(config.output / "configuration.json", config_dict(config))
    outcome = "failed"
    try:
        smoke = train_one(config, 2e-5, 42, smoke=True)
        write_json(config.output / "smoke-result.json", smoke)
        if mode == "smoke":
            outcome = "succeeded"
            progress(config.output / "progress.json", phase="smoke-complete")
            return
        candidates = []
        for lr in (2e-5, 1e-5, 5e-5):
            meta = train_one(config, lr, 42)
            candidates.append((meta["validation_macro_f1"], -meta["validation_loss"], lr, 42))
        best_lr = max(candidates)[2]
        for seed in (43, 44):
            meta = train_one(config, best_lr, seed)
            candidates.append((meta["validation_macro_f1"], -meta["validation_loss"], best_lr, seed))
        best = max(candidates)
        selected = config.output / f"lr-{best[2]:g}-seed-{best[3]}"
        write_json(
            config.output / "selection.json",
            {
                "criterion": "validation_macro_f1_then_lower_loss",
                "candidates": candidates,
                "selected_path": str(selected),
                "checkpoint_sha256": manifest_hash(selected),
            },
        )
        progress(config.output / "progress.json", phase="evaluating-frozen-selection", selected=str(selected))
        result = report(config, selected)
        outcome = "succeeded"
        progress(
            config.output / "progress.json",
            phase="complete",
            selected=str(selected),
            test_macro_f1=result["encoder"]["macro_f1"],
            test_agreement=result["encoder"]["accuracy"],
        )
    except BaseException:
        (config.output / "failure.txt").write_text(traceback.format_exc())
        progress(config.output / "progress.json", phase="failed", evidence=str(config.output / "failure.txt"))
        raise
    finally:
        if config.archive_home is not None:
            archive_run(config, outcome)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "train"), default="smoke")
    parser.add_argument("--microbatch", type=int, default=2)
    parser.add_argument("--archive-home", type=Path)
    parser.add_argument("--attention", choices=("sdpa", "flash_attention_2"), default="sdpa")
    parser.add_argument("--gradient-checkpointing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sort-microbatches", action="store_true")
    parser.add_argument("--max-padded-tokens", type=int)
    parser.add_argument("--fused-optimizer", action="store_true")
    parser.add_argument("--memory-limit-bytes", type=int)
    parser.add_argument("--cuda-memory-limit-bytes", type=int)
    parser.add_argument(
        "--evaluation-policy",
        choices=("original-holdout", "reused-holdout-comparison"),
        default="original-holdout",
    )
    args = parser.parse_args()
    run(
        Config(
            dataset=args.dataset,
            model=args.model,
            output=args.output,
            microbatch=args.microbatch,
            archive_home=args.archive_home,
            attention=args.attention,
            gradient_checkpointing=args.gradient_checkpointing,
            sort_microbatches=args.sort_microbatches,
            max_padded_tokens=args.max_padded_tokens,
            fused_optimizer=args.fused_optimizer,
            memory_limit_bytes=args.memory_limit_bytes,
            cuda_memory_limit_bytes=args.cuda_memory_limit_bytes,
            evaluation_policy=args.evaluation_policy,
        ),
        args.mode,
    )

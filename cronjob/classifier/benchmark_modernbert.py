"""Benchmark memory/speed on disposable models using training inputs only."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from classifier.artifacts import (
    config_dict,
    environment,
    manifest_hash,
    new_directory,
    read_rows,
    verify,
    write_json,
)
from classifier.encoder_batches import configure_memory
from classifier.encoder_runtime import load_model, probabilities
from train_modernbert import Config, train_step

CASES = {
    "sdpa-checkpointed-micro2": {"microbatch": 2},
    "sdpa-micro16-32k": {
        "microbatch": 16,
        "max_padded_tokens": 32768,
        "gradient_checkpointing": False,
        "sort_microbatches": True,
        "fused_optimizer": True,
    },
    "flash2-micro16-32k": {
        "microbatch": 16,
        "max_padded_tokens": 32768,
        "gradient_checkpointing": False,
        "sort_microbatches": True,
        "fused_optimizer": True,
        "attention": "flash_attention_2",
    },
    "flash2-micro16-64k": {
        "microbatch": 16,
        "max_padded_tokens": 65536,
        "gradient_checkpointing": False,
        "sort_microbatches": True,
        "fused_optimizer": True,
        "attention": "flash_attention_2",
    },
}


def benchmark_case(case: str, dataset: Path, model_path: Path, output: Path) -> None:
    import torch
    from transformers import set_seed

    config = Config(
        dataset,
        model_path,
        output,
        memory_limit_bytes=80_000_000_000,
        cuda_memory_limit_bytes=60_000_000_000,
        **CASES[case],
    )
    set_seed(42)
    configure_memory(config.memory_limit_bytes, config.cuda_memory_limit_bytes)
    model, tokenizer = load_model(model_path, "cuda", fresh=True, attention=config.attention)
    if config.gradient_checkpointing:
        model.gradient_checkpointing_enable()
    rows = read_rows(dataset / "train.jsonl")
    initial_probabilities = probabilities(model, tokenizer, rows[:4], "cuda", batch_size=2)
    random.Random(42).shuffle(rows)
    blocks = [rows[start : start + 16] for start in range(0, 112, 16)]
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=2e-5, weight_decay=0.01, **({"fused": True} if config.fused_optimizer else {})
    )
    torch.cuda.reset_peak_memory_stats()
    train_step(model, tokenizer, blocks[0], optimizer, config)  # Warm-up, excluded from timing.
    times, memory = [], {}
    for block in blocks[1:]:
        torch.cuda.synchronize()
        start = time.perf_counter()
        result = train_step(model, tokenizer, block, optimizer, config)
        torch.cuda.synchronize()
        times.append(time.perf_counter() - start)
        memory = {k: max(v, memory.get(k, 0)) for k, v in result.memory.items()}
    # Stress the upper bound without changing the actual dataset or retaining weights.
    longest = max(rows, key=lambda r: len(r["input_ids"]))
    stress = train_step(model, tokenizer, [longest] * 16, optimizer, config)
    memory = {k: max(v, memory.get(k, 0)) for k, v in stress.memory.items()}
    result = {
        "case": case,
        "status": "passed",
        "configuration": config_dict(config),
        "seconds_per_optimizer_step": sum(times) / len(times),
        "individual_step_seconds": times,
        "turns_per_second": 96 / sum(times),
        "memory": memory,
        "initial_probabilities": initial_probabilities,
        "environment": environment(),
        "stress": "one update on 16 copies of the longest retained training input; disposable weights",
        "dataset_manifest_sha256": manifest_hash(dataset),
    }
    write_json(output / f"{case}.json", result)
    print(
        json.dumps({k: v for k, v in result.items() if k not in ("initial_probabilities", "configuration")})
    )


def profile(dataset: Path, model: Path, output: Path) -> None:
    verify(dataset, "turn-encoder-dataset")
    new_directory(output)
    candidates = []
    reference = None
    for case in CASES:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--dataset",
            str(dataset),
            "--model",
            str(model),
            "--output",
            str(output),
            "--case",
            case,
        ]
        with (output / f"{case}.log").open("x") as log:
            finished = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        if finished.returncode:
            result = {"case": case, "status": "failed", "exit_code": finished.returncode}
        else:
            result = json.loads((output / f"{case}.json").read_text())
            values = result.pop("initial_probabilities")
            if reference is None:
                reference = values
            difference = max(
                abs(a - b)
                for first, second in zip(reference, values, strict=True)
                for a, b in zip(first, second, strict=True)
            )
            result["max_backend_score_difference"] = difference
            if difference > 0.02:
                result["status"] = "backend-parity-failed"
        candidates.append(result)
        print(
            json.dumps({k: v for k, v in result.items() if k not in ("configuration", "environment")}),
            flush=True,
        )
    passed = [c for c in candidates if c["status"] == "passed"]
    if not passed:
        write_json(output / "summary.json", {"candidates": candidates, "selected": None})
        raise ValueError("No measured configuration passed memory and numerical checks")
    best = min(passed, key=lambda c: c["seconds_per_optimizer_step"])
    write_json(
        output / "summary.json",
        {
            "candidates": candidates,
            "selected": best["case"],
            "selected_options": CASES[best["case"]],
            "memory_limit_bytes": 80_000_000_000,
            "cuda_memory_limit_bytes": 60_000_000_000,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    if args.case:
        benchmark_case(args.case, args.dataset, args.model, args.output)
    else:
        profile(args.dataset, args.model, args.output)

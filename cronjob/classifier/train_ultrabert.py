"""Fine-tune pinned local UltraBERT at one fixed learning rate across three seeds."""

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

from classifier.artifacts import read_json
from classifier.ultrabert import verify_source
from train_modernbert import Config, run


@dataclass
class UltraBertConfig(Config):
    learning_rates: tuple[float, ...] = (1e-5,)
    warmup_ratio: float = 0.05
    weight_decay: float = 0.1
    adam_beta2: float = 0.98
    adam_epsilon: float = 1e-6
    optimizer_recipe: str = "recommended"
    evaluation_policy: str = "reused-holdout-comparison"


def recipe(config: UltraBertConfig, name: str) -> UltraBertConfig:
    if name == "baseline":
        return replace(
            config,
            optimizer_recipe=name,
            warmup_ratio=0.1,
            weight_decay=0.01,
            adam_beta1=0.9,
            adam_beta2=0.999,
            adam_epsilon=1e-8,
        )
    if name == "recommended":
        return replace(
            config,
            optimizer_recipe=name,
            warmup_ratio=0.05,
            weight_decay=0.1,
            adam_beta1=0.9,
            adam_beta2=0.98,
            adam_epsilon=1e-6,
        )
    raise ValueError("Unknown UltraBERT optimizer recipe")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "model", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--recipe", choices=("baseline", "recommended"), default="recommended")
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--mode", choices=("smoke", "train"), default="smoke")
    parser.add_argument("--microbatch", type=int, default=2)
    parser.add_argument("--attention", choices=("sdpa", "flash_attention_2"), default="sdpa")
    parser.add_argument("--gradient-checkpointing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sort-microbatches", action="store_true")
    parser.add_argument("--max-padded-tokens", type=int)
    parser.add_argument("--fused-optimizer", action="store_true")
    parser.add_argument("--memory-limit-bytes", type=int)
    parser.add_argument("--cuda-memory-limit-bytes", type=int)
    parser.add_argument("--archive-home", type=Path)
    args = parser.parse_args(argv)
    config = recipe(
        UltraBertConfig(
            dataset=args.dataset,
            model=args.model,
            output=args.output,
            learning_rates=(args.learning_rate,),
            microbatch=args.microbatch,
            attention=args.attention,
            gradient_checkpointing=args.gradient_checkpointing,
            sort_microbatches=args.sort_microbatches,
            max_padded_tokens=args.max_padded_tokens,
            fused_optimizer=args.fused_optimizer,
            memory_limit_bytes=args.memory_limit_bytes,
            cuda_memory_limit_bytes=args.cuda_memory_limit_bytes,
            archive_home=args.archive_home,
        ),
        args.recipe,
    )
    verify_source(config.model, read_json(config.model / "source.json"))
    run(config, args.mode)


if __name__ == "__main__":
    main()

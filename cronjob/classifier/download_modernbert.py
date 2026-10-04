"""Explicit public-weight download; never sends training data to Hugging Face."""

import argparse
from pathlib import Path

from classifier.artifacts import inventory, new_directory, write_json

MODELS = ("answerdotai/ModernBERT-base", "answerdotai/ModernBERT-large")


def download(output: Path, model_id: str = MODELS[0]):
    from huggingface_hub import HfApi, hf_hub_download

    if model_id not in MODELS:
        raise ValueError("Select a supported ModernBERT checkpoint")
    revision = HfApi().model_info(model_id).sha
    new_directory(output)
    for name in (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "model.safetensors",
    ):
        hf_hub_download(model_id, name, revision=revision, local_dir=output)
    write_json(
        output / "source.json",
        {
            "model_id": model_id,
            "revision": revision,
            "files": [f for f in inventory(output) if not f["path"].startswith(".cache/")],
        },
    )
    print({"model_id": model_id, "revision": revision}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", choices=MODELS, default=MODELS[0])
    args = parser.parse_args()
    download(args.output, args.model_id)

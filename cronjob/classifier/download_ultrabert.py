"""Explicit public download; training data is never sent to Hugging Face."""

import argparse
import shutil
import urllib.request
from pathlib import Path

from classifier.artifacts import inventory, new_directory, write_json
from classifier.ultrabert import FILES, MODELS


def download(output: Path, model_id: str = "dmis-lab/UltraBERT-380M"):
    if model_id not in MODELS:
        raise ValueError("Select a supported UltraBERT checkpoint")
    new_directory(output)
    revision = MODELS[model_id]
    for name in FILES:
        temporary = output / f"{name}.part"
        url = f"https://huggingface.co/{model_id}/resolve/{revision}/{name}"
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("xb") as stream:
            shutil.copyfileobj(response, stream)
        temporary.replace(output / name)
    write_json(
        output / "source.json", {"model_id": model_id, "revision": revision, "files": inventory(output)}
    )
    print({"model_id": model_id, "revision": revision}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", choices=MODELS, default="dmis-lab/UltraBERT-380M")
    args = parser.parse_args()
    download(args.output, args.model_id)

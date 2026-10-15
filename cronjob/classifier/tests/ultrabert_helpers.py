"""Synthetic pinned file inventories; never download or execute model code."""

from classifier.artifacts import inventory, write_json
from classifier.ultrabert import FILES, MODELS


def model_source(path, model_id="dmis-lab/UltraBERT-380M"):
    path.mkdir()
    for name in FILES:
        (path / name).write_bytes(b"synthetic test content")
    source = {"model_id": model_id, "revision": MODELS[model_id], "files": inventory(path)}
    write_json(path / "source.json", source)
    return source

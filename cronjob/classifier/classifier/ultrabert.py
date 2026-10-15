"""Allowlisted public UltraBERT revisions and verification before local code execution."""

from pathlib import Path

from .artifacts import sha256

MODELS = {
    "dmis-lab/UltraBERT-150M": "f821af159246ab4ac84669d42533281366bc55ab",
    "dmis-lab/UltraBERT-380M": "7789071ca161cb99e03ec1006655d24ad8c1b0e1",
}
CODE_FILES = ("configuration_ultrabert.py", "modeling_ultrabert.py")
FILES = (
    "config.json",
    *CODE_FILES,
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "README.md",
)


def verify_source(path: Path, source: dict, *, code_only: bool = False) -> None:
    model_id = source.get("model_id")
    if model_id not in MODELS or source.get("revision") != MODELS[model_id]:
        raise ValueError("Expected an allowlisted pinned UltraBERT source")
    entries = source.get("files", [])
    names = [entry["path"] for entry in entries]
    if len(names) != len(FILES) or set(names) != set(FILES):
        raise ValueError("UltraBERT source inventory differs")
    for entry in entries:
        if code_only and entry["path"] not in CODE_FILES:
            continue
        local = path / entry["path"]
        if local.is_symlink() or not local.is_file() or sha256(local) != entry["sha256"]:
            raise ValueError("UltraBERT local source checksum differs")

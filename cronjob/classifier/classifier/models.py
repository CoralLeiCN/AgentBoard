"""Local-only model adapters. Optional ML packages are imported only when requested."""

import math
import time
from pathlib import Path

from .artifacts import (
    environment,
    fingerprint,
    manifest_hash,
    read_rows,
    verify,
)
from .data import PREPROCESSING, SPLITS, validate_inputs
from .taxonomy import CATEGORIES, TAXONOMY_VERSION


def positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def validate_truncation(value):
    if value not in ("left", "right"):
        raise ValueError("truncation_side must be left or right")


def local_model(path):
    path = Path(path)
    if not path.is_dir():
        raise ValueError("Provide an existing local model directory; automatic downloads are disabled")
    return path


def compatible(manifest):
    if (manifest.get("preprocessing") != PREPROCESSING or manifest.get("categories") != list(CATEGORIES)
            or manifest.get("taxonomy") != TAXONOMY_VERSION):
        raise ValueError("Incompatible taxonomy or preprocessing")


def dataset(path):
    meta = verify(path, "dataset")
    compatible(meta)
    return meta


def training_rows(path):
    dataset(path)
    train, validation = [validate_inputs(read_rows(Path(path) / f"{s}.jsonl")) for s in SPLITS[:2]]
    if not train or not validation or len({r["category"] for r in train}) < 2:
        raise ValueError("Need nonempty training/validation with at least two training classes")
    return train, validation


def batches(rows, size):
    positive(size, "batch size")
    for offset in range(0, len(rows), size):
        yield rows[offset:offset + size]


def token_lengths(tokenizer, rows, max_length, *, prompt=""):
    lengths = []
    for batch in batches(rows, 64):
        lengths.extend(len(ids) for ids in tokenizer([prompt + r["text"] for r in batch], truncation=False,
                                                    padding=False)["input_ids"])
    return {"examples": len(rows), "truncated": sum(n > max_length for n in lengths),
            "max_tokens_observed": max(lengths, default=0), "max_length": max_length}


def common_metadata(data_path, source, seed, **config):
    return {"taxonomy": TAXONOMY_VERSION, "categories": list(CATEGORIES), "preprocessing": PREPROCESSING,
            "dataset_manifest_sha256": manifest_hash(data_path), "source_model_sha256": fingerprint(source),
            "seed": seed, "configuration": config, "environment": environment()}


def bert_logits(model, tokenizer, rows, *, batch_size, max_length, device):
    import torch

    probabilities = []
    model.eval()
    with torch.no_grad():
        for batch in batches(rows, batch_size):
            tokens = tokenizer([r["text"] for r in batch], padding=True, truncation=True,
                               max_length=max_length, return_tensors="pt").to(device)
            probabilities.extend(model(**tokens).logits.softmax(dim=-1).cpu().tolist())
    return probabilities


def load_encoder(source, device, max_length, *, truncation_side="right"):
    validate_truncation(truncation_side)
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(str(local_model(source)), device=device, local_files_only=True,
                                  trust_remote_code=False)
    if max_length > encoder.max_seq_length:
        raise ValueError("max_length exceeds the embedding model's configured capacity")
    encoder.max_seq_length = max_length
    encoder.tokenizer.truncation_side = truncation_side
    # Freeze the encoder; encode() also uses inference mode. No prompt is added implicitly.
    encoder.default_prompt_name = None
    return encoder


def encode(encoder, rows, batch_size, *, normalize_embeddings=True, prompt=""):
    import numpy as np

    result = np.asarray(encoder.encode([r["text"] for r in rows], batch_size=batch_size,
                                      show_progress_bar=False, convert_to_numpy=True,
                                      normalize_embeddings=normalize_embeddings, prompt=prompt), dtype=np.float32)
    if result.ndim != 2 or len(result) != len(rows) or not np.isfinite(result).all():
        raise ValueError("Encoder returned invalid vectors")
    return result


def predict(model_path, rows, *, batch_size=32, device="cpu"):
    positive(batch_size, "batch size")
    validate_inputs(rows)
    meta = verify(model_path, "model")
    compatible(meta)
    config = {"family": meta["family"], "artifact_sha256": manifest_hash(model_path),
              "dataset_manifest_sha256": meta["dataset_manifest_sha256"]}
    started = time.perf_counter()
    max_length = meta["configuration"]["max_length"]
    if meta["family"] == "bert":
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        source = str(Path(model_path) / "model")
        tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True, trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(source, local_files_only=True,
                                                                 trust_remote_code=False, use_safetensors=True).to(device)
        if [model.config.id2label[i] for i in range(len(CATEGORIES))] != list(CATEGORIES):
            raise ValueError("Saved BERT label order differs")
        tokenizer.truncation_side = meta["configuration"].get("truncation_side", "right")
        probs = bert_logits(model, tokenizer, rows, batch_size=batch_size, max_length=max_length, device=device)
    elif meta["family"] == "lightgbm":
        import lightgbm as lgb

        encoder = load_encoder(Path(model_path) / "encoder", device, max_length,
                               truncation_side=meta["configuration"].get("truncation_side", "right"))
        tokenizer = encoder.tokenizer
        model = lgb.Booster(model_file=str(Path(model_path) / "model.txt"))
        probs = []
        for batch in batches(rows, batch_size):
            vectors = encode(encoder, batch, batch_size,
                             normalize_embeddings=meta["configuration"]["normalize_embeddings"],
                             prompt=meta["configuration"].get("prompt", ""))
            if vectors.shape[1] != meta["embedding_dimension"]:
                raise ValueError("Embedding dimension mismatch")
            probs.extend(model.predict(vectors).tolist())
    else:
        raise ValueError("Unsupported model family")
    if len(probs) != len(rows):
        raise ValueError("Prediction count mismatch")
    result = []
    for row, values in zip(rows, probs):
        if (len(values) != len(CATEGORIES) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                or not math.isclose(sum(values), 1, abs_tol=1e-5)):
            raise ValueError("Invalid model probabilities")
        result.append({**{k: row[k] for k in ("session_id", "turn_id", "input_sha256", "text_sha256")},
                       "category": CATEGORIES[max(range(len(values)), key=values.__getitem__)],
                       "probabilities": dict(zip(CATEGORIES, values)), "configuration": config})
    return result, {"configuration": config, "count": len(rows), "batch_size": batch_size, "device": device,
                    "elapsed_seconds_including_load": time.perf_counter() - started,
                    "truncation": token_lengths(tokenizer, rows, max_length,
                                               prompt=meta["configuration"].get("prompt", ""))}

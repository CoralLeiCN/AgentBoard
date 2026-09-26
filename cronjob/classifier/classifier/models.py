"""Local-only model adapters. Optional ML packages are imported only when requested."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .artifacts import environment, fingerprint, manifest_hash, read_rows, verify
from .contracts import JsonObject, PathLike, Prediction, PredictionResult, TrainingRows, TruncationSide
from .data import PREPROCESSING, SPLITS, validate_inputs
from .taxonomy import CATEGORIES, TAXONOMY_VERSION

if TYPE_CHECKING:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

T = TypeVar("T")
TOKEN_COUNT_BATCH_SIZE = 64


@dataclass
class _ModelProbabilities:
    values: list[list[float]]
    tokenizer: PreTrainedTokenizerBase


def positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def validate_truncation(value: str) -> None:
    if value not in ("left", "right"):
        raise ValueError("truncation_side must be left or right")


def local_model(path: PathLike) -> Path:
    path = Path(path)
    if not path.is_dir():
        raise ValueError("Provide an existing local model directory; automatic downloads are disabled")
    return path


def compatible(manifest: JsonObject) -> None:
    if (
        manifest.get("preprocessing") != PREPROCESSING
        or manifest.get("categories") != list(CATEGORIES)
        or manifest.get("taxonomy") != TAXONOMY_VERSION
    ):
        raise ValueError("Incompatible taxonomy or preprocessing")


def dataset(path: PathLike) -> JsonObject:
    meta = verify(path, "dataset")
    compatible(meta)
    return meta


def training_rows(path: PathLike) -> TrainingRows:
    dataset(path)
    train, validation = [validate_inputs(read_rows(Path(path) / f"{split}.jsonl")) for split in SPLITS[:2]]
    if not train or not validation or len({row["category"] for row in train}) < 2:
        raise ValueError("Need nonempty training/validation with at least two training classes")
    return TrainingRows(train=train, validation=validation)


def batches(rows: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    positive_integer(size, "batch size")
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


def token_lengths(
    tokenizer: PreTrainedTokenizerBase, rows: Sequence[JsonObject], max_length: int, *, prompt: str = ""
) -> dict[str, int]:
    lengths = []
    for batch in batches(rows, TOKEN_COUNT_BATCH_SIZE):
        lengths.extend(
            len(ids)
            for ids in tokenizer([prompt + row["text"] for row in batch], truncation=False, padding=False)[
                "input_ids"
            ]
        )
    return {
        "examples": len(rows),
        "truncated": sum(length > max_length for length in lengths),
        "max_tokens_observed": max(lengths, default=0),
        "max_length": max_length,
    }


def common_metadata(data_path: PathLike, source: PathLike, seed: int, **config: Any) -> JsonObject:
    return {
        "taxonomy": TAXONOMY_VERSION,
        "categories": list(CATEGORIES),
        "preprocessing": PREPROCESSING,
        "dataset_manifest_sha256": manifest_hash(data_path),
        "source_model_sha256": fingerprint(source),
        "seed": seed,
        "configuration": config,
        "environment": environment(),
    }


def bert_probabilities(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    rows: Sequence[JsonObject],
    *,
    batch_size: int,
    max_length: int,
    device: str,
) -> list[list[float]]:
    import torch

    probabilities = []
    model.eval()
    with torch.no_grad():
        for batch in batches(rows, batch_size):
            tokens = tokenizer(
                [row["text"] for row in batch],
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            ).to(device)
            probabilities.extend(model(**tokens).logits.softmax(dim=-1).cpu().tolist())
    return probabilities


def load_encoder(
    source: PathLike, device: str, max_length: int, *, truncation_side: TruncationSide = "right"
) -> SentenceTransformer:
    validate_truncation(truncation_side)
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(
        str(local_model(source)),
        device=device,
        local_files_only=True,
        trust_remote_code=False,
    )
    if max_length > encoder.max_seq_length:
        raise ValueError("max_length exceeds the embedding model's configured capacity")
    encoder.max_seq_length = max_length
    encoder.tokenizer.truncation_side = truncation_side
    # Freeze the encoder; encode() also uses inference mode. No prompt is added implicitly.
    encoder.default_prompt_name = None
    return encoder


def encode(
    encoder: SentenceTransformer,
    rows: Sequence[JsonObject],
    batch_size: int,
    *,
    normalize_embeddings: bool = True,
    prompt: str = "",
) -> np.ndarray:
    import numpy as np

    result = np.asarray(
        encoder.encode(
            [row["text"] for row in rows],
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=normalize_embeddings,
            prompt=prompt,
        ),
        dtype=np.float32,
    )
    if result.ndim != 2 or len(result) != len(rows) or not np.isfinite(result).all():
        raise ValueError("Encoder returned invalid vectors")
    return result


def _predict_bert(
    model_path: Path,
    rows: Sequence[JsonObject],
    metadata: JsonObject,
    batch_size: int,
    device: str,
) -> _ModelProbabilities:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    source = str(model_path / "model")
    tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        source,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
    ).to(device)
    if [model.config.id2label[i] for i in range(len(CATEGORIES))] != list(CATEGORIES):
        raise ValueError("Saved BERT label order differs")
    tokenizer.truncation_side = metadata["configuration"].get("truncation_side", "right")
    values = bert_probabilities(
        model,
        tokenizer,
        rows,
        batch_size=batch_size,
        max_length=metadata["configuration"]["max_length"],
        device=device,
    )
    return _ModelProbabilities(values=values, tokenizer=tokenizer)


def _predict_lightgbm(
    model_path: Path,
    rows: Sequence[JsonObject],
    metadata: JsonObject,
    batch_size: int,
    device: str,
) -> _ModelProbabilities:
    import lightgbm as lgb

    config = metadata["configuration"]
    encoder = load_encoder(
        model_path / "encoder",
        device,
        config["max_length"],
        truncation_side=config.get("truncation_side", "right"),
    )
    model = lgb.Booster(model_file=str(model_path / "model.txt"))
    probabilities = []
    for batch in batches(rows, batch_size):
        vectors = encode(
            encoder,
            batch,
            batch_size,
            normalize_embeddings=config["normalize_embeddings"],
            prompt=config.get("prompt", ""),
        )
        if vectors.shape[1] != metadata["embedding_dimension"]:
            raise ValueError("Embedding dimension mismatch")
        probabilities.extend(model.predict(vectors).tolist())
    return _ModelProbabilities(values=probabilities, tokenizer=encoder.tokenizer)


def _prediction_rows(
    rows: Sequence[JsonObject],
    probabilities: Sequence[Sequence[float]],
    configuration: JsonObject,
) -> list[Prediction]:
    if len(probabilities) != len(rows):
        raise ValueError("Prediction count mismatch")
    result = []
    for row, values in zip(rows, probabilities, strict=True):
        if (
            len(values) != len(CATEGORIES)
            or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values)
            or not math.isclose(sum(values), 1, abs_tol=1e-5)
        ):
            raise ValueError("Invalid model probabilities")
        result.append(
            Prediction(
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                input_sha256=row["input_sha256"],
                text_sha256=row["text_sha256"],
                category=CATEGORIES[max(range(len(values)), key=values.__getitem__)],
                probabilities=dict(zip(CATEGORIES, values, strict=True)),
                configuration=configuration,
            )
        )
    return result


def predict(
    model_path: PathLike,
    rows: list[JsonObject],
    *,
    batch_size: int = 32,
    device: str = "cpu",
) -> PredictionResult:
    """Read a saved model and return predictions and timing without modifying input rows."""
    positive_integer(batch_size, "batch size")
    validate_inputs(rows)
    metadata = verify(model_path, "model")
    compatible(metadata)
    configuration = {
        "family": metadata["family"],
        "artifact_sha256": manifest_hash(model_path),
        "dataset_manifest_sha256": metadata["dataset_manifest_sha256"],
    }
    started = time.perf_counter()
    if metadata["family"] == "bert":
        output = _predict_bert(Path(model_path), rows, metadata, batch_size, device)
    elif metadata["family"] == "lightgbm":
        output = _predict_lightgbm(Path(model_path), rows, metadata, batch_size, device)
    else:
        raise ValueError("Unsupported model family")
    predictions = _prediction_rows(rows, output.values, configuration)
    return PredictionResult(
        predictions=predictions,
        metadata={
            "configuration": configuration,
            "count": len(rows),
            "batch_size": batch_size,
            "device": device,
            "elapsed_seconds_including_load": time.perf_counter() - started,
            "truncation": token_lengths(
                output.tokenizer,
                rows,
                metadata["configuration"]["max_length"],
                prompt=metadata["configuration"].get("prompt", ""),
            ),
        },
    )

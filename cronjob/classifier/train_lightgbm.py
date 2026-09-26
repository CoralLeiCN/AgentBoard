"""Embed text and train LightGBM. Edit LightGBMConfig defaults, then run this file."""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from classifier.artifacts import (
    config_dict,
    environment,
    fingerprint,
    manifest_hash,
    new_directory,
    read_rows,
    seal,
    sha256,
    verify,
)
from classifier.contracts import JsonObject, TrainingRows, TruncationSide
from classifier.data import PREPROCESSING, SPLITS, validate_inputs
from classifier.evaluation import metrics
from classifier.models import (
    common_metadata,
    compatible,
    dataset,
    encode,
    load_encoder,
    local_model,
    positive,
    positive_integer,
    token_lengths,
    training_rows,
    validate_truncation,
)
from classifier.taxonomy import CATEGORIES, TAXONOMY_VERSION

if TYPE_CHECKING:
    import numpy as np


@dataclass
class LightGBMConfig:
    dataset: Path = Path(".agentboard/classifier-run/dataset")
    embedding_model: Path = Path(".agentboard/models/embedding")
    embeddings: Path = Path(".agentboard/classifier-run/vectors")
    output: Path = Path(".agentboard/classifier-run/lightgbm")
    reuse_embeddings: bool = False
    embedding_batch_size: int = 32
    embedding_max_length: int = 256
    embedding_device: str = "cpu"
    normalize_embeddings: bool = True
    embedding_prompt: str = ""
    truncation_side: TruncationSide = "right"
    rounds: int = 300
    patience: int = 30
    # Native LightGBM parameters. The multiclass objective, taxonomy size and
    # validation metric are fixed by this experiment's comparison contract.
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 10,
            "seed": 42,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": 1,
            "verbosity": -1,
        }
    )


def _validate_config(config: LightGBMConfig) -> None:
    positive_integer(config.embedding_batch_size, "embedding_batch_size")
    positive_integer(config.embedding_max_length, "embedding_max_length")
    positive_integer(config.rounds, "rounds")
    positive_integer(config.patience, "patience")
    validate_truncation(config.truncation_side)
    # LightGBM disables validation early stopping for DART, including these aliases.
    for name in ("boosting", "boosting_type", "boost"):
        value = config.parameters.get(name)
        if isinstance(value, str) and value.strip().lower() == "dart":
            raise ValueError("DART is unsupported: validation early stopping is required")
    protected = {
        "objective",
        "application",
        "app",
        "loss",
        "num_class",
        "num_classes",
        "metric",
        "metrics",
        "metric_types",
    }
    if protected.intersection(config.parameters):
        raise ValueError("parameters cannot override the multiclass evaluation contract")
    if "learning_rate" in config.parameters:
        positive(config.parameters["learning_rate"], "learning_rate")
    if "num_leaves" in config.parameters and config.parameters["num_leaves"] < 2:
        raise ValueError("num_leaves must be at least two")


def embed(config: LightGBMConfig) -> JsonObject:
    _validate_config(config)
    data_path, source, output = config.dataset, config.embedding_model, config.embeddings
    batch_size, max_length, device = (
        config.embedding_batch_size,
        config.embedding_max_length,
        config.embedding_device,
    )
    dataset(data_path)
    source = local_model(source)
    import numpy as np

    encoder = load_encoder(source, device, max_length, truncation_side=config.truncation_side)
    metadata = common_metadata(
        data_path,
        source,
        config.parameters.get("seed", 0),
        batch_size=batch_size,
        max_length=max_length,
        device=device,
        normalize_embeddings=config.normalize_embeddings,
        prompt=config.embedding_prompt,
        truncation_side=config.truncation_side,
    )
    metadata["run_config"] = config_dict(config)
    output = new_directory(output)
    metadata["splits"], metadata["truncation"] = {}, {}
    for split in SPLITS:
        rows = validate_inputs(read_rows(Path(data_path) / f"{split}.jsonl"))
        if not rows:
            raise ValueError("Embedding requires nonempty splits")
        vectors = encode(
            encoder,
            rows,
            batch_size,
            normalize_embeddings=config.normalize_embeddings,
            prompt=config.embedding_prompt,
        )
        np.save(output / f"{split}.npy", vectors, allow_pickle=False)
        metadata["splits"][split] = {
            "rows_sha256": sha256(Path(data_path) / f"{split}.jsonl"),
            "shape": list(vectors.shape),
        }
        metadata["truncation"][split] = token_lengths(
            encoder.tokenizer, rows, max_length, prompt=config.embedding_prompt
        )
    encoder.save(str(output / "encoder"), create_model_card=False, safe_serialization=True)
    return seal(output, "embeddings", **metadata)


@dataclass
class _TrainingEmbeddings:
    train: np.ndarray
    validation: np.ndarray
    metadata: JsonObject


def _load_training_embeddings(config: LightGBMConfig, rows: TrainingRows) -> _TrainingEmbeddings:
    """Verify cache provenance and load only training/validation vectors."""
    data_path, vectors_path = config.dataset, config.embeddings
    vector_meta = verify(vectors_path, "embeddings")
    compatible(vector_meta)
    if vector_meta["dataset_manifest_sha256"] != manifest_hash(data_path):
        raise ValueError("Embeddings belong to a different dataset")
    expected = {
        "max_length": config.embedding_max_length,
        "normalize_embeddings": config.normalize_embeddings,
        "prompt": config.embedding_prompt,
        "truncation_side": config.truncation_side,
    }
    if any(vector_meta["configuration"].get(k) != v for k, v in expected.items()):
        raise ValueError("Embedding cache settings differ from configuration")
    if vector_meta["source_model_sha256"] != fingerprint(local_model(config.embedding_model)):
        raise ValueError("Embedding cache belongs to a different encoder")
    import numpy as np

    arrays = []
    for split, split_rows in (("train", rows.train), ("validation", rows.validation)):
        if vector_meta["splits"][split]["rows_sha256"] != sha256(Path(data_path) / f"{split}.jsonl"):
            raise ValueError("Embedding row order/hash mismatch")
        vectors = np.load(Path(vectors_path) / f"{split}.npy", allow_pickle=False)
        if (
            vectors.ndim != 2
            or len(vectors) != len(split_rows)
            or not np.isfinite(vectors).all()
            or list(vectors.shape) != vector_meta["splits"][split]["shape"]
        ):
            raise ValueError("Invalid embedding array")
        arrays.append(vectors)
    if arrays[0].shape[1] != arrays[1].shape[1]:
        raise ValueError("Embedding dimensions differ")
    return _TrainingEmbeddings(train=arrays[0], validation=arrays[1], metadata=vector_meta)


def train(config: LightGBMConfig) -> JsonObject:
    """Create or verify frozen embeddings, then fit trees without reading test labels."""
    _validate_config(config)
    if config.output.exists():
        raise ValueError("Model output directory already exists")
    data_path, vectors_path, output = config.dataset, config.embeddings, config.output
    rounds, patience = config.rounds, config.patience
    if not config.reuse_embeddings:
        embed(config)
    rows = training_rows(data_path)
    vectors = _load_training_embeddings(config, rows)
    vector_meta = vectors.metadata
    import lightgbm as lgb
    import numpy as np

    params = {
        **config.parameters,
        "objective": "multiclass",
        "num_class": len(CATEGORIES),
        "metric": "multi_logloss",
    }
    train_set = lgb.Dataset(vectors.train, label=[CATEGORIES.index(r["category"]) for r in rows.train])
    valid_set = lgb.Dataset(
        vectors.validation,
        label=[CATEGORIES.index(r["category"]) for r in rows.validation],
        reference=train_set,
    )
    output = new_directory(output)
    started = time.perf_counter()
    history = {}
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=rounds,
        valid_sets=[valid_set],
        valid_names=["validation"],
        callbacks=[lgb.early_stopping(patience, verbose=False), lgb.record_evaluation(history)],
    )
    if booster.best_iteration <= 0:
        raise ValueError("Training did not select a validation best iteration; incomplete artifacts retained")
    booster.save_model(str(output / "model.txt"), num_iteration=booster.best_iteration)
    shutil.copytree(Path(vectors_path) / "encoder", output / "encoder")
    probabilities = booster.predict(vectors.validation, num_iteration=booster.best_iteration)
    validation_metrics = metrics(
        [r["category"] for r in rows.validation],
        [CATEGORIES[int(i)] for i in np.argmax(probabilities, axis=1)],
    )
    return seal(
        output,
        "model",
        family="lightgbm",
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
        preprocessing=PREPROCESSING,
        dataset_manifest_sha256=manifest_hash(data_path),
        embeddings_manifest_sha256=manifest_hash(vectors_path),
        environment=environment(),
        seed=params.get("seed", 0),
        run_config=config_dict(config),
        source_model_sha256=vector_meta["source_model_sha256"],
        configuration={
            **params,
            "rounds": rounds,
            "patience": patience,
            "max_length": vector_meta["configuration"]["max_length"],
            "selection": "validation_multi_logloss",
            "normalize_embeddings": config.normalize_embeddings,
            "prompt": config.embedding_prompt,
            "truncation_side": config.truncation_side,
        },
        embedding_dimension=vectors.train.shape[1],
        best_iteration=booster.best_iteration,
        history=history,
        validation=validation_metrics,
        truncation=vector_meta["truncation"],
        training_seconds=time.perf_counter() - started,
    )


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit(
            "Edit LightGBMConfig in this script; training does not accept command-line parameters"
        )
    train(LightGBMConfig())

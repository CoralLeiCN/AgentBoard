"""Shared in-memory contracts; persisted JSON is validated at its read/use boundary."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

# Manifests and source rows carry extensible producer metadata; do not discard unknown fields.
JsonObject = dict[str, Any]
PathLike = str | Path
RowIdentity = tuple[str, str]
TruncationSide = Literal["left", "right"]


class FileEntry(TypedDict):
    path: str
    bytes: int
    sha256: str


class Prediction(TypedDict):
    session_id: str
    turn_id: str
    input_sha256: str
    text_sha256: str
    category: str
    probabilities: dict[str, float]
    configuration: JsonObject


@dataclass
class PredictionResult:
    predictions: list[Prediction]
    metadata: JsonObject


@dataclass
class TrainingRows:
    train: list[JsonObject]
    validation: list[JsonObject]

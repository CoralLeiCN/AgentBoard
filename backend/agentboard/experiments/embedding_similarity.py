"""Configurable local/remote embeddings and saved-vector cosine comparison."""

import hashlib
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
    from transformers import PreTrainedTokenizerBase

VERSION = "user-input-embedding-cosine-v2"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
COSINE_BLOCK_SIZE = 128


@dataclass(frozen=True)
class LocalEmbeddingConfig:
    model: str
    revision: str
    batch_size: int = 32
    device: Literal["cpu"] = field(default="cpu", init=False)
    provider: Literal["local"] = field(default="local", init=False)


@dataclass(frozen=True)
class RemoteEmbeddingConfig:
    model: str
    base_url: str
    batch_size: int = 32
    api_key_env: str | None = None
    timeout_seconds: float = 60
    provider: Literal["remote"] = field(default="remote", init=False)


EmbeddingConfig = LocalEmbeddingConfig | RemoteEmbeddingConfig


class InputEmbeddingMetadata(TypedDict):
    chunks: int
    input_chars: int
    truncated: bool | None
    client_truncated: NotRequired[bool]


@dataclass
class EmbeddingResult:
    vectors: dict[str, list[float]]
    inputs: dict[str, InputEmbeddingMetadata]
    metadata: dict[str, Any]


def embedding_config(recipe: Mapping[str, Any]) -> EmbeddingConfig:
    """Validate untrusted recipe settings once, before passing a typed provider config."""
    allowed = {
        "embedding_provider",
        "embedding_model",
        "embedding_revision",
        "embedding_batch_size",
        "embedding_base_url",
        "embedding_api_key_env",
        "embedding_timeout_seconds",
    }
    if any(key.startswith("embedding_") and key not in allowed for key in recipe):
        raise ValueError("Unknown embedding option; credentials must use embedding_api_key_env")
    if "similarity_method" in recipe:
        raise ValueError("similarity_method was removed; similarity always uses embedding cosine")
    provider = recipe.get("embedding_provider", "local")
    if provider not in ("local", "remote"):
        raise ValueError("Embedding provider must be local or remote")
    batch_size = recipe.get("embedding_batch_size", 32)
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("Embedding batch size must be between 1 and 128")
    if provider == "remote":
        if "embedding_revision" in recipe:
            raise ValueError("Embedding revision is only supported for local models")
        model = recipe.get("embedding_model")
        if not isinstance(model, str) or not model.strip() or model != model.strip() or len(model) > 256:
            raise ValueError("Remote embeddings require an explicit model ID")
        base_url = recipe.get("embedding_base_url")
        try:
            url = urlsplit(base_url.strip() if isinstance(base_url, str) else "")
            port = url.port  # Validate malformed ports before opening a connection.
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or any(ch.isspace() for ch in base_url)
                or (port is not None and port == 0)
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Embedding base URL must be HTTP(S), without credentials, query or fragment"
            ) from exc
        base_url = urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))
        if url.path.rstrip("/").endswith("/embeddings"):
            raise ValueError("Use the API base URL (for example https://host/v1), not /embeddings")
        key_env = recipe.get("embedding_api_key_env")
        if key_env is not None and (
            not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env)
        ):
            raise ValueError("Embedding API key must name an environment variable")
        timeout = recipe.get("embedding_timeout_seconds", 60)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ValueError("Embedding timeout must be greater than 0 and at most 600 seconds")
        return RemoteEmbeddingConfig(model, base_url, batch_size, key_env, timeout)
    if any(
        key in recipe for key in ("embedding_base_url", "embedding_api_key_env", "embedding_timeout_seconds")
    ):
        raise ValueError("Remote endpoint options require embedding_provider=remote")
    model = recipe.get("embedding_model", DEFAULT_MODEL)
    revision = recipe.get("embedding_revision", DEFAULT_REVISION if model == DEFAULT_MODEL else None)
    if not isinstance(model, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", model):
        raise ValueError("Embedding model must be an explicit Hugging Face namespace/model ID")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Embedding revision must be a pinned 40-character commit SHA")
    return LocalEmbeddingConfig(model, revision, batch_size)


def load_encoder(config: LocalEmbeddingConfig) -> tuple["SentenceTransformer", dict[str, str]]:
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        if exc.name != "sentence_transformers":
            raise
        raise ValueError(
            "Install the optional embeddings extra: uv sync --extra dev --extra embeddings"
        ) from exc
    try:
        model = SentenceTransformer(
            config.model,
            revision=config.revision,
            device=config.device,
            local_files_only=True,
            trust_remote_code=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "A required local embedding model file is missing; cache the pinned revision first"
        ) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(
            "Unable to load the pinned local embedding model; check model files and runtime compatibility"
        ) from exc
    packages = {name: version(name) for name in ("sentence-transformers", "transformers", "torch", "numpy")}
    return model, packages


def fit_chunks(text: str, tokenizer: "PreTrainedTokenizerBase", limit: int) -> list[tuple[str, int]]:
    """Split without dropping normalized characters; every chunk fits including special tokens."""
    if type(limit) is not int or limit < 4 or limit > 1_000_000:
        raise ValueError("Embedding model must declare a finite supported token window")
    pending, result = [text], []
    while pending:
        chunk = pending.pop()
        ids = tokenizer(
            chunk,
            add_special_tokens=True,
            truncation=False,
            return_attention_mask=False,
            return_token_type_ids=False,
        )["input_ids"]
        if len(ids) <= limit:
            result.append((chunk, len(ids)))
        else:
            if len(chunk) < 2:
                raise ValueError("A single input character exceeds the embedding model token window")
            # Prefer nearby whitespace; retain both sides exactly, including the separator.
            middle = len(chunk) // 2
            boundary = chunk.rfind(" ", max(1, middle // 2), middle + 1)
            split = boundary + 1 if boundary >= 0 else middle
            pending.extend((chunk[split:], chunk[:split]))
    return result


def unit_vector(vector: Iterable[float]) -> list[float]:
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        raise ValueError("Embedding vectors must be numeric") from exc
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError("Embedding vectors must be nonempty and finite")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding vectors must have a finite nonzero norm")
    return [x / norm for x in values]


def encode_inputs(inputs: Mapping[str, str], config: EmbeddingConfig) -> EmbeddingResult:
    if isinstance(config, RemoteEmbeddingConfig):
        return encode_remote(inputs, config)
    return encode_local(inputs, config)


def encode_local(inputs: Mapping[str, str], config: LocalEmbeddingConfig) -> EmbeddingResult:
    model, packages = load_encoder(config)
    chunks, assignments = [], []
    per_input = {}
    for key, text in inputs.items():
        parts = fit_chunks(text, model.tokenizer, model.max_seq_length)
        per_input[key] = {"chunks": len(parts), "input_chars": len(text), "truncated": False}
        for chunk, weight in parts:
            chunks.append(chunk)
            assignments.append((key, weight))
    sums, dimension, calls = {}, None, 0
    for offset in range(0, len(chunks), config.batch_size):
        batch = chunks[offset : offset + config.batch_size]
        vectors = model.encode(
            batch,
            batch_size=config.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            precision="float32",
            prompt="",
        )
        calls += 1
        if len(vectors) != len(batch):
            raise ValueError("Embedding model returned the wrong number of vectors")
        for vector, (key, weight) in zip(vectors, assignments[offset : offset + len(batch)], strict=True):
            values = unit_vector(vector)
            dimension = dimension or len(values)
            if len(values) != dimension:
                raise ValueError("Embedding dimensions differ")
            current = sums.setdefault(key, [0.0] * dimension)
            for i, value in enumerate(values):
                current[i] += weight * value
    output = {key: unit_vector(values) for key, values in sums.items()}
    return EmbeddingResult(
        output,
        per_input,
        {
            "version": VERSION,
            "method": "embedding-cosine",
            "metric": "cosine",
            "embedding_origin": "model_generated",
            **asdict(config),
            "packages": packages,
            "dimensions": dimension,
            "max_seq_length": model.max_seq_length,
            "encode_batches": calls,
            "chunking": "contiguous-token-fitted-v1",
            "pooling": "token-count-weighted-unit-mean",
            "prompt": "",
            "truncated": False,
        },
    )


def encode_remote(inputs: Mapping[str, str], config: RemoteEmbeddingConfig) -> EmbeddingResult:
    """OpenAI-compatible embeddings API; send only complete normalized user inputs."""
    try:
        import httpx
    except ImportError as exc:
        raise ValueError("Install remote embeddings: uv sync --extra remote-embeddings") from exc
    key_env = config.api_key_env
    key = os.environ.get(key_env, "") if key_env else ""
    if key_env and not key.strip():
        raise ValueError("The configured embedding API key environment variable is unset or empty")
    headers = {"Authorization": f"Bearer {key}"} if key_env else {}
    keys, output, dimension, reported_models, calls = list(inputs), {}, None, [], 0
    # No inherited proxies, redirects, retries, or alternate providers. Never persist credentials or response bodies.
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=config.timeout_seconds) as client:
        for offset in range(0, len(keys), config.batch_size):
            batch = keys[offset : offset + config.batch_size]
            try:
                response = client.post(
                    config.base_url + "/embeddings",
                    headers=headers,
                    json={
                        "model": config.model,
                        "input": [inputs[k] for k in batch],
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ValueError(
                    f"Remote embedding request failed (HTTP {exc.response.status_code}); no fallback attempted"
                ) from None
            except httpx.HTTPError:
                raise ValueError(
                    "Remote embedding request failed or timed out; no fallback attempted"
                ) from None
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("Remote embedding response is not valid JSON") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise ValueError("Remote embedding response has the wrong number of vectors")
            reported = payload.get("model")
            if reported is not None and (not isinstance(reported, str) or not reported.strip()):
                raise ValueError("Remote embedding response has an invalid model ID")
            if reported_models and reported != reported_models[0]:
                raise ValueError("Remote embedding response model changed between batches")
            reported_models.append(reported)
            seen = set()
            for item in data:
                index = item.get("index") if isinstance(item, dict) else None
                if type(index) is not int or index not in range(len(batch)) or index in seen:
                    raise ValueError("Remote embedding response has invalid or duplicate indexes")
                seen.add(index)
                vector = item.get("embedding")
                if not isinstance(vector, list) or any(type(v) not in (int, float) for v in vector):
                    raise ValueError("Remote embedding response must contain numeric float arrays")
                values = unit_vector(vector)
                dimension = dimension or len(values)
                if len(values) != dimension:
                    raise ValueError("Embedding dimensions differ")
                output[batch[index]] = values
            calls += 1
    per_input = {
        key: {"chunks": 1, "input_chars": len(text), "truncated": None, "client_truncated": False}
        for key, text in inputs.items()
    }
    return EmbeddingResult(
        output,
        per_input,
        {
            "version": VERSION,
            "method": "embedding-cosine",
            "metric": "cosine",
            "embedding_origin": "model_generated",
            **asdict(config),
            "packages": {name: version(name) for name in ("httpx", "numpy")},
            "dimensions": dimension,
            "encode_batches": calls,
            "reported_model": reported_models[0] if reported_models else None,
            "chunking": "none-full-input",
            "pooling": "none",
            "client_truncated": False,
            "truncated": None,
            "server_truncation": "unknown",
        },
    )


def cosine_scores(vectors: Mapping[str, list[float]], threshold: float) -> list[tuple[str, str, float]]:
    """Calculate in blocks, avoiding an N×N resident matrix; no model is invoked here."""
    import numpy as np

    keys = sorted(vectors)
    if not keys:
        return []
    values = [unit_vector(vectors[key]) for key in keys]
    if len({len(v) for v in values}) != 1:
        raise ValueError("Embedding dimensions differ")
    matrix = np.asarray(values, dtype=np.float64)
    scores = []
    for start in range(0, len(keys), COSINE_BLOCK_SIZE):
        block = np.clip(matrix[start : start + COSINE_BLOCK_SIZE] @ matrix.T, -1.0, 1.0)
        for i, row in enumerate(block):
            left = start + i
            for right in np.flatnonzero(row[left + 1 :] >= threshold) + left + 1:
                scores.append((keys[left], keys[int(right)], float(row[right])))
    return scores


def vector_sha256(vector: Iterable[float]) -> str:
    # Hex floats preserve the exact saved Python values independent of JSON formatting.
    return hashlib.sha256("\n".join(float(x).hex() for x in vector).encode()).hexdigest()

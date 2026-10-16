"""Load an immutable encoder checkpoint once, with the training input contract."""

from __future__ import annotations

from pathlib import Path

from .artifacts import manifest_hash, read_json, sha256, verify
from .taxonomy import CATEGORIES, TAXONOMY_VERSION
from .turn_encoder import MAX_TOKENS, PREPROCESSING, tokenize
from .ultrabert import MODELS as ULTRABERT_MODELS
from .ultrabert import verify_source as verify_ultrabert_source


def load_model(
    path: Path, device: str, *, fresh: bool = False, attention: str = "sdpa", base_model: dict | None = None
):
    if attention not in ("sdpa", "flash_attention_2"):
        raise ValueError("Unsupported encoder attention backend")
    if fresh:
        base_model = read_json(path / "source.json")
    ultrabert = base_model is not None and base_model.get("model_id") in ULTRABERT_MODELS
    if ultrabert:
        verify_ultrabert_source(path, base_model, code_only=not fresh)
    elif fresh:
        for entry in base_model["files"]:
            if sha256(path / entry["path"]) != entry["sha256"]:
                raise ValueError("Base model file checksum mismatch")
    from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=ultrabert)
    config = AutoConfig.from_pretrained(path, local_files_only=True, trust_remote_code=ultrabert)
    if ultrabert:
        # The pinned RoPE configs have no max_position_embeddings field.
        # Our shared tokenizer/input contract enforces the 8192-token limit.
        if config.model_type != "ultrabert":
            raise ValueError("Expected the pinned UltraBERT model type")
    elif config.model_type != "modernbert" or config.max_position_embeddings != MAX_TOKENS:
        raise ValueError("Expected ModernBERT with 8192 positions")
    if fresh:
        config.num_labels = len(CATEGORIES)
        config.id2label = dict(enumerate(CATEGORIES))
        config.label2id = {c: i for i, c in enumerate(CATEGORIES)}
        config.problem_type = "single_label_classification"
    elif [config.id2label[i] for i in range(len(CATEGORIES))] != list(CATEGORIES):
        raise ValueError("Checkpoint label mapping differs")
    config.reference_compile = False
    model = AutoModelForSequenceClassification.from_pretrained(
        path,
        config=config,
        local_files_only=True,
        trust_remote_code=ultrabert,
        use_safetensors=True,
        attn_implementation=attention,
    ).to(device)
    tokenizer.truncation_side = "right"
    return model, tokenizer


def tensor_batch(tokenizer, rows: list[dict], device: str):
    return tokenizer.pad({"input_ids": [r["input_ids"] for r in rows]}, padding=True, return_tensors="pt").to(
        device
    )


def probabilities(model, tokenizer, rows: list[dict], device: str, batch_size: int = 2):
    import torch

    model.eval()
    outputs = []
    with (
        torch.inference_mode(),
        torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.startswith("cuda")),
    ):
        for offset in range(0, len(rows), batch_size):
            logits = model(**tensor_batch(tokenizer, rows[offset : offset + batch_size], device)).logits
            if not torch.isfinite(logits).all():
                raise ValueError("Non-finite model output")
            outputs.extend(logits.float().softmax(dim=-1).cpu().tolist())
    return outputs


class Encoder:
    def __init__(self, path: Path, device: str = "cuda"):
        import importlib.metadata

        meta = verify(path, "turn-encoder-model")
        if (
            meta["preprocessing"] != PREPROCESSING
            or meta["categories"] != list(CATEGORIES)
            or meta["taxonomy"] != TAXONOMY_VERSION
        ):
            raise ValueError("Checkpoint input contract differs")
        if importlib.metadata.version("transformers") != meta["environment"]["transformers"]:
            raise ValueError("Transformers version differs from training")
        self.model, self.tokenizer = load_model(
            path / "model", device, attention=meta.get("attention", "sdpa"), base_model=meta["base_model"]
        )
        self.model.eval()
        self.device = device
        self.metadata = {
            "checkpoint_sha256": manifest_hash(path),
            "base_model": meta["base_model"],
            "taxonomy": TAXONOMY_VERSION,
            "categories": list(CATEGORIES),
            "preprocessing": PREPROCESSING,
            "max_tokens": MAX_TOKENS,
            "truncation_side": "right",
            "calibrated": False,
            "score_interpretation": "uncalibrated softmax, not verified correctness probability",
            "precision": "bf16-autocast" if device.startswith("cuda") else "fp32",
            "attention": meta.get("attention", "sdpa"),
        }

    def predict(self, items: list[dict]) -> list[dict]:
        prepared = [tokenize(self.tokenizer, i["target_turn"], i["preceding_turn_context"]) for i in items]
        values = probabilities(self.model, self.tokenizer, prepared, self.device)
        result = []
        for item, row, scores in zip(items, prepared, values, strict=True):
            result.append(
                {
                    "request_id": item["request_id"],
                    "category": CATEGORIES[max(range(len(CATEGORIES)), key=scores.__getitem__)],
                    "scores": dict(zip(CATEGORIES, scores, strict=True)),
                    **{k: v for k, v in row.items() if k != "input_ids"},
                    **self.metadata,
                }
            )
        return result

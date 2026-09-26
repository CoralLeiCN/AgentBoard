"""Fine-tune the BERT classifier. Edit BertConfig defaults below, then run this file."""

import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from classifier.artifacts import config_dict, new_directory, seal
from classifier.evaluation import metrics
from classifier.models import (
    batches,
    bert_logits,
    common_metadata,
    local_model,
    positive,
    token_lengths,
    training_rows,
    validate_truncation,
)
from classifier.taxonomy import CATEGORIES


@dataclass
class BertConfig:
    dataset: Path = Path(".agentboard/classifier-run/dataset")
    model: Path = Path(".agentboard/models/bert")
    output: Path = Path(".agentboard/classifier-run/bert")
    seed: int = 42
    epochs: int = 3
    batch_size: int = 8
    max_length: int = 512
    device: str = "cpu"
    truncation_side: str = "right"
    gradient_clip_norm: float = 1.0
    ignore_mismatched_sizes: bool = True
    # Native AdamW options, including any additional supported optimizer parameters.
    optimizer_params: dict = field(default_factory=lambda: {
        "lr": 2e-5, "weight_decay": 0.01, "betas": (0.9, 0.999), "eps": 1e-8,
    })
    # Existing Hugging Face configuration fields, e.g. hidden_dropout_prob.
    model_config: dict = field(default_factory=dict)


def train(config: BertConfig):
    """Train from an explicit configuration; importing the script never starts work."""
    for value, name in ((config.epochs, "epochs"), (config.batch_size, "batch size"),
                        (config.max_length, "max length"), (config.gradient_clip_norm, "gradient clip norm")):
        positive(value, name)
    for name in ("epochs", "batch_size", "max_length"):
        if type(getattr(config, name)) is not int:
            raise ValueError(f"{name} must be an integer")
    validate_truncation(config.truncation_side)
    for name in ("lr", "eps"):
        if name in config.optimizer_params:
            positive(config.optimizer_params[name], name)
    decay = config.optimizer_params.get("weight_decay", 0.01)
    if not math.isfinite(decay) or decay < 0:
        raise ValueError("weight_decay must be nonnegative and finite")
    protected = {"num_labels", "id2label", "label2id", "problem_type", "trust_remote_code",
                 "local_files_only", "return_unused_kwargs"}
    if protected.intersection(config.model_config):
        raise ValueError("model_config cannot override the label or local-loading contract")
    if config.output.exists():
        raise ValueError("Model output directory already exists")
    data_path, source, output = config.dataset, config.model, config.output
    seed, epochs, batch_size = config.seed, config.epochs, config.batch_size
    max_length, device = config.max_length, config.device
    train, validation = training_rows(data_path)
    source = local_model(source)
    import torch
    from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer, set_seed

    set_seed(seed)
    tokenizer = AutoTokenizer.from_pretrained(str(source), local_files_only=True, trust_remote_code=False)
    model_config, unused = AutoConfig.from_pretrained(
        str(source), local_files_only=True, trust_remote_code=False, return_unused_kwargs=True,
        num_labels=len(CATEGORIES), id2label=dict(enumerate(CATEGORIES)),
        label2id={c: i for i, c in enumerate(CATEGORIES)}, problem_type="single_label_classification",
        **config.model_config)
    if unused:
        raise ValueError(f"Unknown model_config fields: {sorted(unused)}")
    model = AutoModelForSequenceClassification.from_pretrained(
        str(source), config=model_config, local_files_only=True, trust_remote_code=False,
        use_safetensors=True, ignore_mismatched_sizes=config.ignore_mismatched_sizes).to(device)
    if max_length > getattr(model.config, "max_position_embeddings", max_length):
        raise ValueError("max_length exceeds model positional capacity")
    tokenizer.truncation_side = config.truncation_side
    output = new_directory(output)
    metadata = common_metadata(data_path, source, seed, epochs=epochs, batch_size=batch_size,
                               max_length=max_length, device=device, selection="validation_macro_f1",
                               truncation_side=config.truncation_side, optimizer="AdamW",
                               gradient_clip_norm=config.gradient_clip_norm,
                               model_config=config.model_config,
                               ignore_mismatched_sizes=config.ignore_mismatched_sizes)
    metadata["run_config"] = config_dict(config)
    metadata["truncation"] = {s: token_lengths(tokenizer, rows, max_length)
                              for s, rows in (("train", train), ("validation", validation))}
    optimizer = torch.optim.AdamW(model.parameters(), **config.optimizer_params)
    metadata["configuration"]["optimizer_params"] = optimizer.defaults
    generator = torch.Generator().manual_seed(seed)
    best, history = -1.0, []
    started = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        order = torch.randperm(len(train), generator=generator).tolist()
        for batch in batches([train[i] for i in order], batch_size):
            tokens = tokenizer([r["text"] for r in batch], padding=True, truncation=True,
                               max_length=max_length, return_tensors="pt").to(device)
            labels = torch.tensor([CATEGORIES.index(r["category"]) for r in batch], device=device)
            optimizer.zero_grad()
            loss = model(**tokens, labels=labels).loss
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss; incomplete artifacts retained")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip_norm)
            optimizer.step()
            total_loss += loss.item() * len(batch)
        probabilities = bert_logits(model, tokenizer, validation, batch_size=batch_size,
                                    max_length=max_length, device=device)
        score = metrics([r["category"] for r in validation],
                        [CATEGORIES[max(range(len(CATEGORIES)), key=p.__getitem__)] for p in probabilities])
        history.append({"epoch": epoch + 1, "train_loss": total_loss / len(train), "validation": score})
        if score["macro_f1"] > best:
            best = score["macro_f1"]
            metadata["best_epoch"] = epoch + 1
            metadata["validation"] = score
            model.save_pretrained(str(output / "model"), safe_serialization=True)
            tokenizer.save_pretrained(str(output / "model"))
    metadata.update(history=history, training_seconds=time.perf_counter() - started, family="bert")
    return seal(output, "model", **metadata)


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("Edit BertConfig in this script; training does not accept command-line parameters")
    train(BertConfig())

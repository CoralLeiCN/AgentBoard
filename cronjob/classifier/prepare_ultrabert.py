"""Retokenize the frozen ModernBERT cohort without changing its split or labels."""

import argparse
import shutil
from pathlib import Path

from classifier.artifacts import (
    digest,
    manifest_hash,
    new_directory,
    read_json,
    read_rows,
    seal,
    verify,
    write_rows,
)
from classifier.taxonomy import CATEGORIES, TAXONOMY_VERSION
from classifier.turn_encoder import PREPROCESSING, tokenize, truncation_summary
from classifier.ultrabert import verify_source


def prepare(source: Path, model: Path, output: Path):
    original = verify(source, "turn-encoder-dataset")
    if (
        original.get("preprocessing") != PREPROCESSING
        or original.get("taxonomy") != TAXONOMY_VERSION
        or original.get("categories") != list(CATEGORIES)
    ):
        raise ValueError("Frozen encoder input contract differs")
    verify_source(model, read_json(model / "source.json"))
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=True)
    new_directory(output)
    summaries = {}
    keys = ("session_id", "turn_id", "group_id", "category", "input_sha256", "text_sha256")
    for split in ("train", "validation", "test"):
        rows = read_rows(source / f"{split}.jsonl")
        updated = []
        for row in rows:
            tokens = tokenize(tokenizer, row["target_turn"], row["preceding_turn_context"])
            if tokens["input_sha256"] != row["input_sha256"] or tokens["text_sha256"] != row["text_sha256"]:
                raise ValueError("Frozen source text or input hash changed")
            updated.append(dict(row, **tokens))
        if digest([{k: r[k] for k in keys} for r in rows]) != digest(
            [{k: r[k] for k in keys} for r in updated]
        ):
            raise ValueError("Frozen cohort membership, labels or text changed")
        write_rows(output / f"{split}.jsonl", updated)
        summaries[split] = truncation_summary(updated)
    shutil.copy2(source / "split-assignment.json", output / "split-assignment.json")
    shutil.copytree(source / "sources", output / "sources")
    result = seal(
        output,
        "turn-encoder-dataset",
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
        source_pins=original["source_pins"],
        grouping=original["grouping"],
        truncation=summaries,
        base_model=read_json(model / "source.json"),
        comparison_dataset_manifest_sha256=manifest_hash(source),
        comparison_contract="Identical ordered identities, groups, labels and full text; different tokenizer",
    )
    print({"truncation": summaries}, flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "model", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.model, args.output)

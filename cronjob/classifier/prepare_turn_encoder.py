"""Prepare pinned Astra data for the chronological ModernBERT experiment."""

from __future__ import annotations

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
    sha256,
    verify,
    write_json,
    write_rows,
)
from classifier.taxonomy import CATEGORIES, TAXONOMY_VERSION
from classifier.turn_encoder import (
    PREPROCESSING,
    chronological_split,
    join_astra,
    tokenize,
    truncation_summary,
)


def prepare(sources: Path, model: Path, output: Path, match_dataset: Path | None = None) -> dict:
    from transformers import AutoTokenizer

    pins = read_json(sources)
    for item in pins["files"].values():
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or relative == Path("sources.json"):
            raise ValueError("Source paths must be relative and preserve the manifest filename")
        if sha256(sources.parent / item["path"]) != item["sha256"]:
            raise ValueError("Pinned source checksum mismatch")
    turns = read_json(sources.parent / pins["files"]["turns"]["path"])
    reference = read_json(sources.parent / pins["files"]["reference"]["path"])
    rows = join_astra(turns, reference)
    splits, grouping = chronological_split(rows, pins.get("session_relations", []))
    if match_dataset is not None:
        verify(match_dataset, "turn-encoder-dataset")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    output = new_directory(output)
    # Freeze identities and temporal assignment before any fit or token inspection.
    write_json(
        output / "split-assignment.json",
        {
            "report": grouping,
            "assignments": {
                s: [{k: r[k] for k in ("session_id", "turn_id", "group_id", "input_sha256")} for r in part]
                for s, part in splits.items()
            },
        },
    )
    summaries = {}
    for split, part in splits.items():
        prepared = [
            dict(
                row,
                **{
                    k: v
                    for k, v in tokenize(tokenizer, row["target_turn"], row["preceding_turn_context"]).items()
                    if k != "input_sha256"
                },
            )
            for row in part
        ]
        if match_dataset is not None:
            previous = read_rows(match_dataset / f"{split}.jsonl")
            keys = (
                "session_id",
                "turn_id",
                "group_id",
                "category",
                "input_sha256",
                "input_ids",
                "text_sha256",
                "effective_input_sha256",
                "original_token_count",
                "used_token_count",
            )
            if digest([{k: r[k] for k in keys} for r in previous]) != digest(
                [{k: r[k] for k in keys} for r in prepared]
            ):
                raise ValueError(
                    "Comparison dataset differs in frozen membership, labels or effective tokens"
                )
        write_rows(output / f"{split}.jsonl", prepared)
        summaries[split] = truncation_summary(prepared)
    raw = output / "sources"
    raw.mkdir(mode=0o700)
    shutil.copy2(sources, raw / "sources.json")
    for item in pins["files"].values():
        destination = raw / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources.parent / item["path"], destination)
    result = seal(
        output,
        "turn-encoder-dataset",
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
        source_pins=pins,
        grouping=grouping,
        truncation=summaries,
        base_model=read_json(model / "source.json"),
        comparison_dataset_manifest_sha256=manifest_hash(match_dataset) if match_dataset else None,
    )
    print({"split_counts": {s: grouping[s]["turns"] for s in splits}, "truncation": summaries}, flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--match-dataset", type=Path)
    args = parser.parse_args()
    prepare(args.sources, args.model, args.output, args.match_dataset)

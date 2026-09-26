"""Explicit local experiment commands, isolated from trace databases and live providers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .artifacts import (
    environment,
    inventory,
    manifest_hash,
    new_directory,
    read_json,
    read_rows,
    seal,
    write_json,
    write_rows,
)
from .contracts import JsonObject
from .data import import_gpt, prepare, turn_inputs
from .evaluation import compare

OPTIONAL_ML_MODULES = {"torch", "transformers", "sentence_transformers", "lightgbm", "numpy"}


def configure(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sub = parser.add_subparsers(dest="classifier_command", required=True)
    inputs = sub.add_parser("inputs", help="Render archived turns for batch inference")
    inputs.add_argument("--turns", type=Path, required=True)
    inputs.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--turns", type=Path, required=True)
    prepare.add_argument("--labels", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--ratios", type=float, nargs=3, default=(0.7, 0.15, 0.15))
    prepare.add_argument("--allow-gpt-labels", action="store_true")
    gpt = sub.add_parser("import-gpt", help="Convert saved GPT results to reference/prediction JSONL")
    gpt.add_argument("--results", type=Path, required=True)
    gpt.add_argument("--model")
    gpt.add_argument("--output", type=Path, required=True)
    infer = sub.add_parser("predict")
    infer.add_argument("--model", type=Path, required=True)
    infer.add_argument("--inputs", type=Path, required=True)
    infer.add_argument("--output", type=Path, required=True, help="New directory for predictions and timing")
    infer.add_argument("--batch-size", type=int, default=32)
    infer.add_argument("--device", default="cpu")
    compare = sub.add_parser("compare")
    compare.add_argument("--dataset", type=Path, required=True)
    compare.add_argument("--prediction", action="append", required=True, metavar="NAME=PATH")
    compare.add_argument("--output", type=Path, required=True)
    record = sub.add_parser("record", help="Archive an explicitly selected experiment workspace")
    record.add_argument("--directory", type=Path, required=True)
    record.add_argument("--data-home", type=Path)
    record.add_argument("--archive-config", type=Path)
    record.add_argument("--project", default="agentboard")
    record.add_argument("--experiment", default="turn-classifiers")
    record.add_argument("--references", type=Path, help="JSON array of pinned source archive references")
    return parser


def record_workspace(args: argparse.Namespace) -> JsonObject:
    try:
        from agentboard.experiments.cli import archive_settings
    except ModuleNotFoundError as exc:
        if exc.name != "agentboard":
            raise
        raise ValueError(
            "Recording requires the existing AgentBoard archive package. Run with "
            "uv run --project cronjob/classifier --with /absolute/path/to/AgentBoard classifier record ..."
        ) from exc

    archive_options = argparse.Namespace(**{**vars(args), "mode": "filesystem"})
    archive = archive_settings(archive_options)
    entries = inventory(args.directory)
    if not entries:
        raise ValueError("Cannot archive an empty workspace")
    if archive.home.is_relative_to(args.directory.resolve()):
        raise ValueError("Workspace must not contain its destination archive")
    refs = read_json(args.references) if args.references else []
    for reference in refs:
        archive.resolve(reference)
    root = Path(__file__).resolve().parents[1]
    if not (root / "uv.lock").is_file():
        raise ValueError("Record from the source checkout to retain code and lock snapshots")
    run = archive.begin(
        args.project,
        args.experiment,
        inputs=refs,
        metadata={
            "environment": environment(),
            "code": {"snapshot": "source/"},
            "configuration": {"workflow": "turn-classifiers-v1"},
        },
    )
    for entry in entries:
        run.add_file(args.directory / entry["path"], "workspace/" + entry["path"], role="experiment-evidence")
    for path in sorted(root.glob("train_*.py")):
        run.add_file(path, "source/" + path.name, role="code")
    for folder in (root / "classifier", root / "tests"):
        for path in sorted(folder.rglob("*.py")):
            run.add_file(path, "source/" + path.relative_to(root).as_posix(), role="code")
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        run.add_file(root / name, "source/" + name, role="reproduction")
    return run.finalize()


def _compare_predictions(args: argparse.Namespace) -> JsonObject:
    from .models import dataset

    dataset(args.dataset)
    predictions = {}
    for specification in args.prediction:
        name, separator, path = specification.partition("=")
        if not separator or not name or not path or name in predictions:
            raise ValueError("Use unique --prediction NAME=PATH entries")
        predictions[name] = read_rows(path)
    result = compare(read_rows(args.dataset / "test.jsonl"), predictions)
    dataset_hash = manifest_hash(args.dataset)
    for report in result["classifiers"].values():
        config = report["configuration"] or {}
        if (
            config.get("family") in ("bert", "lightgbm")
            and config.get("dataset_manifest_sha256") != dataset_hash
        ):
            raise ValueError("Supervised predictions were trained against a different dataset split")
    result["dataset_manifest_sha256"] = dataset_hash
    write_json(args.output, result)
    return result


def execute(args: argparse.Namespace) -> None:
    command = args.classifier_command
    if command == "inputs":
        rows = turn_inputs(read_rows(args.turns))
        write_rows(args.output, rows)
        result = {"count": len(rows), "output": str(args.output)}
    elif command == "import-gpt":
        rows = import_gpt(read_json(args.results), model=args.model)
        write_rows(args.output, rows)
        result = {"count": len(rows), "output": str(args.output)}
    elif command == "prepare":
        result = prepare(
            args.turns,
            args.labels,
            args.output,
            seed=args.seed,
            ratios=args.ratios,
            allow_gpt_labels=args.allow_gpt_labels,
        )
    elif command == "compare":
        result = _compare_predictions(args)
    elif command == "record":
        result = record_workspace(args)
    elif command == "predict":
        from .models import predict

        if args.output.exists():
            raise ValueError("Prediction output directory already exists")
        prediction = predict(
            args.model, read_rows(args.inputs), batch_size=args.batch_size, device=args.device
        )
        output = new_directory(args.output)
        write_rows(output / "predictions.jsonl", prediction.predictions)
        write_json(output / "timing.json", prediction.metadata)
        result = seal(output, "predictions", **prediction.metadata)
    else:
        raise ValueError("Unknown classifier utility")
    # Summaries never print private examples or target IDs.
    print(
        json.dumps(
            {
                "command": command,
                "output": "" if command == "record" else str(args.output),
                "kind": result.get("kind"),
                "count": result.get("count", result.get("labeled_count")),
                "archive_id": result.get("id"),
            },
            indent=2,
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = configure(
        argparse.ArgumentParser(
            description="Classifier data, inference and comparison utilities (training uses per-model scripts)"
        )
    )
    args = parser.parse_args(argv)
    try:
        execute(args)
    except ModuleNotFoundError as exc:
        if exc.name not in OPTIONAL_ML_MODULES:
            raise
        parser.error(
            f"Optional model dependencies unavailable: {exc}. "
            "Run uv sync --project cronjob/classifier --extra ml"
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

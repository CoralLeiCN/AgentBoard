"""Turn curation, attributed human decisions and input-only embedding similarity."""

import hashlib
import json
import math
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from . import embedding_similarity
from .archive import Archive, atomic_json, checked, encoded, identifier, locked, now, safe_name
from .coverage_analysis import CATEGORIES, calculate, identity, source_configurations
from .coverage_analysis import VERSION as COVERAGE_VERSION
from .embedding_similarity import EmbeddingConfig

VERSION = "dataset-curation-v2"
DEFAULT_REFERENCE = {
    "model": "gpt-6-sol",
    "reasoning_effort": "xhigh",
    "execution": "batched",
    "taxonomy": "session-purpose-v1",
}


class RevisionConflict(ValueError):
    pass


ReviewAction = Literal[
    "verify", "reopen_label", "restore", "keep_both", "remove_left", "remove_right", "reopen_pair"
]


class ReviewDecision(TypedDict):
    revision: int
    target: str
    action: ReviewAction
    reviewer: str
    reason: str
    category: NotRequired[str | None]


@dataclass
class InputComparison:
    pairs: list[dict[str, Any]]
    metadata: dict[str, Any]
    vectors: dict[str, list[float]]
    annotations: dict[str, dict[str, Any]]


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def input_text(target: Mapping[str, Any]) -> str:
    """Read current user messages only; never fall back to an assistant or predecessor."""
    messages = target.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Every target requires a messages list")
    texts = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Invalid target message")
        if message.get("role") == "user":
            if not isinstance(message.get("content"), str):
                raise ValueError("User message content must be text")
            normalized = unicodedata.normalize("NFKC", message["content"])
            normalized = " ".join(normalized.split())
            if normalized:
                texts.append(normalized)
    return "\n".join(texts)


def validate_threshold(threshold: float) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold):
        raise ValueError("Similarity threshold must be finite")
    if not 0.1 <= threshold <= 1:
        raise ValueError("Similarity threshold must be between 0.1 and 1")


def compare_inputs(
    rows: Sequence[Mapping[str, Any]], config: EmbeddingConfig, threshold: float = 0.85
) -> InputComparison:
    """Embed user inputs and return scores and annotations without changing the supplied rows."""
    validate_threshold(threshold)
    metadata: dict[str, Any] = {
        "threshold": threshold,
        "origin": "calculated",
        "input": "target user messages only",
    }
    inputs = {}
    annotations: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = input_text(row["target"])
        annotations[row["id"]] = {"normalized_input_sha256": digest(text) if text else None}
        if text:
            inputs[row["id"]] = text
    if not inputs:
        raise ValueError("No nonempty user inputs are available to embed")
    embeddings = embedding_similarity.encode_inputs(inputs, config)
    if set(embeddings.vectors) != set(inputs) or set(embeddings.inputs) != set(inputs):
        raise ValueError("Embedding results do not cover the selected user inputs")
    pairs = [
        {
            "id": digest([left, right]),
            "left": left,
            "right": right,
            "score": score,
            "decision": "pending",
            "review": None,
        }
        for left, right, score in embedding_similarity.cosine_scores(embeddings.vectors, threshold)
    ]
    for row in rows:
        if row["id"] in embeddings.vectors:
            annotations[row["id"]]["embedding"] = {
                **embeddings.inputs[row["id"]],
                "vector_sha256": embedding_similarity.vector_sha256(embeddings.vectors[row["id"]]),
            }
    return InputComparison(
        pairs=sorted(pairs, key=lambda p: (-p["score"], p["left"], p["right"])),
        metadata={
            **metadata,
            **embeddings.metadata,
            "preprocessing": "NFKC + collapsed whitespace; preserve case and message boundaries",
            "embedded_inputs": len(embeddings.vectors),
        },
        vectors=embeddings.vectors,
        annotations=annotations,
    )


def build_workspace(
    recipe: Mapping[str, Any], targets: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if recipe.get("format_version") != VERSION:
        raise ValueError("Unsupported curation recipe")
    reference = recipe["reference_configuration"]
    coverage = calculate({"format_version": COVERAGE_VERSION, "targets": targets, "sources": sources})
    configurations = source_configurations(sources)
    if not any(config == reference for config, _ in configurations):
        raise ValueError("Reference configuration is absent or ambiguous")
    maps = [{identity(row): (row, label) for row, label in labels} for _, labels in configurations]
    statuses = [
        {(t["session_id"], t["turn_id"]): t["reason"] for t in c["targets"]} for c in coverage["classifiers"]
    ]
    indexes = [t["index"] for t in targets if "index" in t]
    if len(set(indexes)) != len(indexes):
        raise ValueError("Duplicate target index makes predecessor context ambiguous")
    rows: list[dict[str, Any]] = []
    for target in targets:
        key = identity(target)
        results = []
        inferred = None
        for i, (config, _) in enumerate(configurations):
            saved = maps[i].get(key)
            label = saved[1] if saved else None
            status = statuses[i][key]
            result = {
                "configuration": config,
                "status": status,
                "result": deepcopy(label),
                "classification_input_sha256": saved[0]["classification_input_sha256"] if saved else None,
            }
            results.append(result)
            if config == reference and status == "available":
                inferred = {
                    "status": "inferred",
                    "category": label["category"],
                    "reason": label["reason"],
                    "configuration": config,
                }
        valid = [r["result"]["category"] for r in results if r["status"] == "available"]
        alignment = (
            "insufficient" if len(valid) < 2 else "disagreement" if len(set(valid)) > 1 else "agreement"
        )
        label = inferred or {
            "status": "unlabeled",
            "category": None,
            "reason": "Reference result unavailable",
        }
        rows.append(
            {
                "id": digest([*key, target["classification_input_sha256"]]),
                "target": deepcopy(target),
                "results": results,
                "comparison": alignment,
                "coverage_complete": len(valid) == len(results),
                "inferred_label": deepcopy(label),
                "label": deepcopy(label),
                "disposition": "active",
                "removal": None,
            }
        )
    provider_config = embedding_similarity.embedding_config(recipe)
    comparison = compare_inputs(rows, provider_config, recipe.get("similarity_threshold", 0.85))
    for row in rows:
        row.update(comparison.annotations[row["id"]])
    recipe = deepcopy(dict(recipe))
    recipe.update(
        {"embedding_" + key: value for key, value in asdict(provider_config).items() if key != "device"}
    )
    recipe["similarity_threshold"] = comparison.metadata["threshold"]
    comparison.metadata["vectors_sha256"] = digest(comparison.vectors)
    return {
        "format_version": VERSION,
        "id": str(uuid4()),
        "revision": 0,
        "created_at": now(),
        "recipe": recipe,
        "rows": rows,
        "pairs": comparison.pairs,
        "history": [],
        "similarity": comparison.metadata,
        "embedding_vectors": comparison.vectors,
    }


def summary(workspace: Mapping[str, Any]) -> dict[str, Any]:
    rows = workspace["rows"]
    active = {r["id"] for r in rows if r["disposition"] == "active"}
    return {
        "id": workspace["id"],
        "revision": workspace["revision"],
        "turns": len(rows),
        "similarity_method": workspace["similarity"]["method"],
        "similarity_threshold": workspace["similarity"]["threshold"],
        "labels": dict(Counter(r["label"]["status"] for r in rows)),
        "removed": sum(r["disposition"] != "active" for r in rows),
        "unresolved_disagreements": sum(
            r["comparison"] == "disagreement"
            and r["label"]["status"] != "verified"
            and r["disposition"] == "active"
            for r in rows
        ),
        "incomplete_coverage": sum(not r["coverage_complete"] for r in rows),
        "suggestions": len(workspace["pairs"]),
        "pending_pairs": sum(
            p["decision"] == "pending" and p["left"] in active and p["right"] in active
            for p in workspace["pairs"]
        ),
    }


def pair_active(workspace: Mapping[str, Any], pair: Mapping[str, Any]) -> bool:
    return all(
        r["disposition"] == "active" for r in workspace["rows"] if r["id"] in (pair["left"], pair["right"])
    )


def _apply_turn_decision(
    workspace: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    decision: ReviewDecision,
    review: dict[str, str],
) -> None:
    """Apply an in-memory transition; the caller owns the lock and persistence."""
    action, selected = decision["action"], decision["target"]
    if selected not in rows:
        raise ValueError("Unknown target")
    row = rows[selected]
    if action == "verify":
        category = decision.get("category")
        if category not in CATEGORIES:
            raise ValueError("Unknown category")
        row["label"] = {"status": "verified", "category": category, **review}
    elif action == "reopen_label":
        row["label"] = deepcopy(row["inferred_label"])
    else:
        row["disposition"], row["removal"] = "active", None
        # A restored turn needs fresh duplicate decisions, never a hidden old removal.
        for pair in workspace["pairs"]:
            if selected in (pair["left"], pair["right"]) and pair["decision"].startswith("remove_"):
                pair["decision"], pair["review"] = "pending", None


def _apply_pair_decision(
    workspace: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    decision: ReviewDecision,
    review: dict[str, str],
) -> None:
    """Apply an in-memory transition; the caller owns the lock and persistence."""
    action, selected = decision["action"], decision["target"]
    pair = next((p for p in workspace["pairs"] if p["id"] == selected), None)
    if pair is None:
        raise ValueError("Unknown duplicate suggestion")
    if not pair_active(workspace, pair):
        raise ValueError("Restore removed turns before changing this pair")
    if action.startswith("remove_"):
        removed, kept = (
            (pair["left"], pair["right"]) if action == "remove_left" else (pair["right"], pair["left"])
        )
        if any(r["removal"] and r["removal"]["duplicate_of"] == removed for r in rows.values()):
            raise ValueError("Restore dependent duplicates before removing their representative")
        rows[removed]["disposition"] = "removed_duplicate"
        rows[removed]["removal"] = {"duplicate_of": kept, "pair_id": pair["id"], **review}
    pair["decision"] = "pending" if action == "reopen_pair" else action
    pair["review"] = review


class CurationStore:
    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self.root = checked(archive.root, "sync/curation")

    def path(self, wid: str) -> Path:
        return checked(self.root, identifier(wid) + ".json")

    def read(self, wid: str) -> dict[str, Any]:
        value = json.loads(self.path(wid).read_bytes())
        if value.get("format_version") != VERSION or value.get("id") != wid:
            raise ValueError("Unsupported or mismatched curation workspace")
        return value

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*.json")):
            try:
                items.append(summary(self.read(path.stem)))
            except ValueError:
                items.append(
                    {"id": path.stem, "error": "Unsupported or invalid workspace; create a new workspace"}
                )
        return items

    def current_recipe(self) -> dict[str, Any]:
        pointers = json.loads(checked(self.archive.root, "sync/current-classification.json").read_bytes())
        # Verify the navigation target before deriving an exact recipe reference.
        coverage = json.loads(self.archive.resolve(pointers["coverage"]).read_bytes())
        ref = self.archive.reference(pointers["coverage"]["id"], "inputs/recipe.json")
        recipe = json.loads(self.archive.resolve(ref).read_bytes())
        if recipe.get("format_version") != COVERAGE_VERSION:
            raise ValueError("Unsupported current coverage recipe")
        available = [
            entry["configuration"]
            for entry in coverage["classifiers"]
            if all(
                entry["configuration"].get(key) == DEFAULT_REFERENCE[key]
                for key in ("model", "reasoning_effort", "taxonomy")
            )
        ]
        if DEFAULT_REFERENCE in available:
            reference = DEFAULT_REFERENCE
        elif len(available) == 1:
            reference = available[0]
        else:
            raise ValueError(
                "Current coverage has no unambiguous Sol xhigh reference; provide an explicit curation recipe"
            )
        return {
            **recipe,
            "format_version": VERSION,
            "reference_configuration": reference,
            "similarity_threshold": 0.85,
        }

    def create(self, recipe: Mapping[str, Any]) -> dict[str, Any]:
        safe_name(recipe["project"])
        safe_name(recipe["experiment"])
        refs = [recipe["dataset"], *[s["reference"] for s in recipe["results"]]]
        verified = {}
        values = [json.loads(self.archive.resolve(ref, _verified=verified).read_bytes()) for ref in refs]
        sources = [
            {"format": source["format"], "value": value}
            for source, value in zip(recipe["results"], values[1:], strict=True)
        ]
        workspace = build_workspace(recipe, values[0], sources)
        self.archive.initialize()
        self.root.mkdir(exist_ok=True, mode=0o700)
        atomic_json(self.path(workspace["id"]), workspace)
        return summary(workspace)

    def decide(self, wid: str, decision: ReviewDecision) -> dict[str, Any]:
        with locked(checked(self.root, identifier(wid) + ".lock")):
            workspace = self.read(wid)
            if decision["revision"] != workspace["revision"]:
                raise RevisionConflict("Workspace changed; refresh before submitting this decision")
            reviewer, reason = decision["reviewer"].strip(), decision["reason"].strip()
            if not reviewer or len(reviewer) > 200 or not reason or len(reason) > 2000:
                raise ValueError("Reviewer (1–200 characters) and reason (1–2000 characters) are required")
            review = {"reviewer": reviewer, "reason": reason, "at": now()}
            rows = {r["id"]: r for r in workspace["rows"]}
            action = decision["action"]
            if action in ("verify", "reopen_label", "restore"):
                _apply_turn_decision(workspace, rows, decision, review)
            elif action in ("keep_both", "remove_left", "remove_right", "reopen_pair"):
                _apply_pair_decision(workspace, rows, decision, review)
            else:
                raise ValueError("Unknown review action")
            workspace["revision"] += 1
            workspace["history"].append({**decision, **review, "revision": workspace["revision"]})
            atomic_json(self.path(wid), workspace)
            return summary(workspace)

    def export(self, wid: str, revision: int) -> dict[str, Any]:
        # Snapshot under the same lock as decisions; export does not mutate the workspace.
        with locked(checked(self.root, identifier(wid) + ".lock")):
            workspace = self.read(wid)
            if workspace["revision"] != revision:
                raise RevisionConflict("Workspace changed; refresh before exporting")
        recipe = workspace["recipe"]
        refs = [recipe["dataset"], *[s["reference"] for s in recipe["results"]]]
        verified = {}
        for ref in refs:
            self.archive.resolve(ref, _verified=verified)
        recorder = self.archive.begin(
            recipe["project"],
            recipe["experiment"][:120] + "-curated",
            kind="dataset",
            inputs=refs,
            metadata={
                "code": {"artifact": "inputs/curation.py", "version": VERSION},
                "configuration": {
                    "workspace_id": wid,
                    "revision": revision,
                    "format_version": VERSION,
                    "model_calls": 0,
                },
                "source_provenance": {"curation": summary(workspace)},
            },
        )
        recorder.add_json(recipe, "inputs/recipe.json", schema_version=VERSION)
        recorder.add_file(Path(__file__), "inputs/curation.py", role="analysis-source")
        if workspace.get("embedding_vectors"):
            recorder.add_file(
                Path(__file__).with_name("embedding_similarity.py"),
                "inputs/embedding_similarity.py",
                role="analysis-source",
            )
        recorder.add_json(workspace, "outputs/workspace.json", schema_version=VERSION)
        recorder.add_json(workspace["rows"], "outputs/all-turns.json", schema_version=VERSION)
        recorder.add_json(
            [r for r in workspace["rows"] if r["disposition"] == "active"],
            "outputs/active-turns.json",
            schema_version=VERSION,
        )
        return {**recorder.finalize(), "curation": summary(workspace)}

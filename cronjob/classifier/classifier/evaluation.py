"""Coverage-aware fixed-taxonomy metrics; no ML dependency or network."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

from .artifacts import digest
from .contracts import JsonObject
from .data import index_rows, validate_inputs, validate_label
from .taxonomy import CATEGORIES


def metrics(truth: Sequence[str], predicted: Sequence[str]) -> JsonObject:
    if len(truth) != len(predicted):
        raise ValueError("Metric arrays must have equal lengths")
    matrix = [[0] * len(CATEGORIES) for _ in CATEGORIES]
    for actual, guess in zip(truth, predicted):
        matrix[CATEGORIES.index(actual)][CATEGORIES.index(guess)] += 1
    per_class = {}
    for i, category in enumerate(CATEGORIES):
        tp, support = matrix[i][i], sum(matrix[i])
        called = sum(row[i] for row in matrix)
        precision = tp / called if called else 0.0
        recall = tp / support if support else 0.0
        per_class[category] = {
            "precision": precision,
            "recall": recall,
            "support": support,
            "f1": 2 * tp / (support + called) if support + called else 0.0,
        }
    n = len(truth)
    return {
        "count": n,
        "accuracy": sum(matrix[i][i] for i in range(len(CATEGORIES))) / n if n else None,
        "macro_f1": sum(v["f1"] for v in per_class.values()) / len(CATEGORIES) if n else None,
        "weighted_f1": sum(v["f1"] * v["support"] for v in per_class.values()) / n if n else None,
        "per_class": per_class,
        "category_order": list(CATEGORIES),
        "confusion_matrix": matrix,
        "zero_division": 0,
        "macro_policy": "all eight categories, including zero-support categories",
    }


def valid_prediction(row: JsonObject) -> bool:
    if row.get("error") or row.get("prompt_compliance_errors") or row.get("category") not in CATEGORIES:
        return False
    probabilities = row.get("probabilities")
    if probabilities is not None:
        if not isinstance(probabilities, dict) or set(probabilities) != set(CATEGORIES):
            return False
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 or v > 1
            for v in probabilities.values()
        ):
            return False
        if not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-5):
            return False
        if max(CATEGORIES, key=probabilities.get) != row["category"]:
            return False
    return True


def compare(targets: list[JsonObject], predictions: Mapping[str, list[JsonObject]]) -> JsonObject:
    validate_inputs(targets)
    target_map = index_rows(targets)
    if not targets or any(not validate_label(row) for row in targets):
        raise ValueError("Evaluation requires nonempty targets with valid reference labels")
    if not predictions:
        raise ValueError("Provide at least one named prediction file")
    available, reports = {}, {}
    configurations = set()
    for name, rows in predictions.items():
        saved = index_rows(rows)
        configs = {digest(row.get("configuration")) for row in rows}
        if len(configs) > 1 or any(
            not isinstance(row.get("configuration"), dict) or not row["configuration"] for row in rows
        ):
            raise ValueError("Prediction file must contain exactly one explicit configuration")
        if configs & configurations:
            raise ValueError("Duplicate classifier configuration")
        configurations.update(configs)
        matched, statuses = {}, []
        for key, target in target_map.items():
            row = saved.get(key)
            if row is None:
                status = "missing"
            elif row["input_sha256"] != target["input_sha256"]:
                status = "changed_input"
            elif row.get("text_sha256", target["text_sha256"]) != target["text_sha256"]:
                status = "changed_text"
            elif not valid_prediction(row):
                status = "invalid"
            else:
                status = "available"
                matched[key] = row["category"]
            statuses.append({"session_id": key[0], "turn_id": key[1], "status": status})
        keys = sorted(matched)
        available[name] = matched
        reports[name] = {
            "requested": len(targets),
            "available": len(keys),
            "pending": len(targets) - len(keys),
            "coverage": dict(Counter(row["status"] for row in statuses)),
            "targets": statuses,
            "outside_requested_subset": [list(key) for key in sorted(set(saved) - set(target_map))],
            "configuration": rows[0]["configuration"] if rows else None,
            "available_metrics": metrics(
                [target_map[k]["category"] for k in keys], [matched[k] for k in keys]
            ),
        }
    common = sorted(set.intersection(*(set(rows) for rows in available.values())))
    for name, matched in available.items():
        reports[name]["paired_metrics"] = metrics(
            [target_map[k]["category"] for k in common], [matched[k] for k in common]
        )
    kinds = sorted({r["label_source"]["kind"] for r in targets})
    sources = Counter((r["label_source"]["kind"], r["label_source"]["name"]) for r in targets)
    return {
        "reference_sources": [
            {"kind": kind, "name": name, "count": count} for (kind, name), count in sorted(sources.items())
        ],
        "reference_kinds": kinds,
        "interpretation": "accuracy" if kinds == ["human"] else "reference_agreement",
        "requested": len(targets),
        "paired_count": len(common),
        "paired_targets": [list(k) for k in common],
        "classifiers": reports,
        "note": "Compare paired metrics on identical targets; GPT reference agreement is not human accuracy.",
    }

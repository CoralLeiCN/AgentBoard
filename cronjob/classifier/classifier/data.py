"""Lossless source retention, explicit label joins and grouped deterministic splitting."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

from .artifacts import digest, new_directory, read_rows, seal, write_rows
from .contracts import JsonObject, PathLike, RowIdentity
from .taxonomy import CATEGORIES, TAXONOMY_VERSION

PREPROCESSING = "target-then-predecessor-role-content-json-v1"
SPLITS = ("train", "validation", "test")
SPLIT_ALGORITHM = "coverage-adaptive-groups-v2"
SPLIT_SEARCH_ATTEMPTS = 512


def identity(row: JsonObject) -> RowIdentity:
    key = (row.get("session_id"), row.get("turn_id"))
    if any(not isinstance(v, str) or not v.strip() for v in key):
        raise ValueError("Nonempty session_id and turn_id are required")
    return key


def input_hash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid input_sha256")
    return value


def index_rows(rows: Sequence[JsonObject]) -> dict[RowIdentity, JsonObject]:
    result = {}
    for row in rows:
        key = identity(row)
        input_hash(row.get("input_sha256"))
        if key in result:
            raise ValueError("Duplicate session/turn identity")
        result[key] = row
    return result


def messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("messages must be an ordered list")
    result = []
    for message in value:
        if (
            not isinstance(message, dict)
            or message.get("role") not in ("user", "assistant")
            or not isinstance(message.get("content"), str)
        ):
            raise ValueError("Expected user/assistant messages with string content")
        result.append({"role": message["role"], "content": message["content"]})
    return result


def turn_inputs(turns: Sequence[JsonObject]) -> list[JsonObject]:
    """Adapt archived turns. Source hash is retained, not reinterpreted as a text hash."""
    indices = {}
    for turn in turns:
        idx = turn.get("index")
        if type(idx) is not int or idx in indices:
            raise ValueError("Duplicate or invalid turn index")
        indices[idx] = turn
    rows = []
    for turn in turns:
        previous = turn.get("previous_turn_index")
        if previous is not None and (previous not in indices or previous == turn["index"]):
            raise ValueError("Missing or self-referential predecessor")
        prior = indices[previous] if previous is not None else None
        sid = turn.get("original_codex_session_id")
        # Explicit families can unite split sessions; cross-session predecessors always unite them.
        group = turn.get("group_id", sid)
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Invalid group_id")
        rendered = {
            "target": messages(turn.get("messages")),
            "previous": messages(prior.get("messages")) if prior else [],
        }
        text = json.dumps(rendered, ensure_ascii=False, separators=(",", ":"))
        rows.append(
            {
                "session_id": sid,
                "turn_id": turn.get("original_codex_turn_id"),
                "group_id": group,
                "previous_session_id": prior["original_codex_session_id"] if prior else None,
                "input_sha256": input_hash(turn.get("classification_input_sha256")),
                "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    index_rows(rows)
    return sorted(rows, key=identity)


def validate_inputs(rows: list[JsonObject]) -> list[JsonObject]:
    index_rows(rows)
    for row in rows:
        if not isinstance(row.get("text"), str) or not row["text"]:
            raise ValueError("Missing rendered input text")
        if hashlib.sha256(row["text"].encode()).hexdigest() != row.get("text_sha256"):
            raise ValueError("Rendered text hash mismatch")
    return rows


def validate_label(row: JsonObject) -> bool:
    source = row.get("label_source", {})
    return (
        row.get("category") in CATEGORIES
        and not row.get("error")
        and not row.get("prompt_compliance_errors")
        and isinstance(source, dict)
        and source.get("kind") in ("human", "gpt")
        and isinstance(source.get("name"), str)
        and bool(source["name"].strip())
    )


def connected_groups(rows: Sequence[JsonObject]) -> list[list[JsonObject]]:
    parent = {}

    def root(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    for row in rows:
        sid = "session:" + row["session_id"]
        union(sid, "family:" + row.get("group_id", row["session_id"]))
        union(sid, "text:" + row["text_sha256"])
        if row.get("previous_session_id"):
            union(sid, "session:" + row["previous_session_id"])
    groups = defaultdict(list)
    for row in rows:
        groups[root("session:" + row["session_id"])].append(row)
    return [sorted(group, key=identity) for _, group in sorted(groups.items())]


def split_rows(
    rows: Sequence[JsonObject],
    *,
    seed: int = 42,
    ratios: Sequence[float] = (0.7, 0.15, 0.15),
    grouping_rows: Sequence[JsonObject] | None = None,
) -> dict[str, list[JsonObject]]:
    if (
        len(ratios) != 3
        or any(not math.isfinite(r) or r <= 0 for r in ratios)
        or not math.isclose(sum(ratios), 1)
    ):
        raise ValueError("Three positive split ratios must sum to one")
    # Unlabeled bridge rows must still connect related labeled sessions.
    eligible = {identity(r): r for r in rows}
    groups = [
        [eligible[identity(r)] for r in g if identity(r) in eligible]
        for g in connected_groups(grouping_rows or rows)
    ]
    groups = [g for g in groups if g]
    if len(groups) < 3:
        raise ValueError("At least three independent groups are required")
    totals = Counter(r["category"] for r in rows)
    if len(totals) < 2:
        raise ValueError("Training requires at least two observed categories")
    counts = [max(1, round(len(groups) * r)) for r in ratios[1:]]
    while sum(counts) >= len(groups):
        counts[counts.index(max(counts))] -= 1
    nval, ntest = counts
    rng, best = random.Random(seed), None
    categories = [set(r["category"] for r in group) for group in groups]

    def consider(partitions: Sequence[Sequence[int]]) -> None:
        nonlocal best
        splits = [[r for i in partition for r in groups[i]] for partition in partitions]
        score = sum(abs(len(part) / len(rows) - ratio) for part, ratio in zip(splits, ratios))
        for part, ratio in zip(splits, ratios):
            support = Counter(r["category"] for r in part)
            score += sum(abs(support[c] / total - ratio) for c, total in totals.items()) / len(totals)
        if best is None or score < best[0]:
            best = (score, splits)

    # Grow training only by whole groups, reserving at least one group per holdout.
    for _ in range(SPLIT_SEARCH_ATTEMPTS):
        order = list(range(len(groups)))
        rng.shuffle(order)
        boundary = nval + ntest
        covered = set().union(*(categories[i] for i in order[boundary:]))
        while covered != set(totals) and boundary > 2:
            boundary -= 1
            covered.update(categories[order[boundary]])
        if covered != set(totals):
            continue
        validation_count = (
            nval
            if boundary == nval + ntest
            else max(1, min(boundary - 1, round(boundary * ratios[1] / (ratios[1] + ratios[2]))))
        )
        consider([order[boundary:], order[:validation_count], order[validation_count:boundary]])

    # A feasible split exists iff two holdout groups can be removed without losing
    # any training category. Check this exactly before declaring data insufficient.
    if best is None:
        support = Counter(c for group_categories in categories for c in group_categories)
        for validation, test in combinations(range(len(groups)), 2):
            if all(support[c] > (c in categories[validation]) + (c in categories[test]) for c in totals):
                consider(
                    [[i for i in range(len(groups)) if i not in (validation, test)], [validation], [test]]
                )
                break
    if best is None:
        raise ValueError("No isolated splits can retain all observed categories in training; add groups/data")
    return {name: sorted(part, key=identity) for name, part in zip(SPLITS, best[1])}


def prepare(
    turns_path: PathLike,
    labels_path: PathLike,
    output: PathLike,
    *,
    seed: int = 42,
    ratios: Sequence[float] = (0.7, 0.15, 0.15),
    allow_gpt_labels: bool = False,
) -> JsonObject:
    turns_path, labels_path = Path(turns_path), Path(labels_path)
    inputs = turn_inputs(read_rows(turns_path))
    targets = index_rows(inputs)
    labels = index_rows(read_rows(labels_path))
    if set(labels) - set(targets):
        raise ValueError("Reference labels contain unknown targets")
    eligible, pending = [], []
    for key, row in targets.items():
        label = labels.get(key)
        if label and label["input_sha256"] != row["input_sha256"]:
            raise ValueError("Stale reference label: input hash changed")
        if label and label.get("text_sha256", row["text_sha256"]) != row["text_sha256"]:
            raise ValueError("Stale reference label: rendered text changed")
        if label and validate_label(label):
            if label["label_source"]["kind"] == "gpt" and not allow_gpt_labels:
                raise ValueError("GPT reference labels require --allow-gpt-labels")
            eligible.append({**row, "category": label["category"], "label_source": label["label_source"]})
        else:
            pending.append({**row, "status": "invalid_label" if label else "missing_label"})
    splits = split_rows(eligible, seed=seed, ratios=ratios, grouping_rows=inputs)
    output = new_directory(output)
    for name, part in splits.items():
        write_rows(output / f"{name}.jsonl", part)
    write_rows(output / "pending.jsonl", pending)
    for source, name in ((turns_path, "turns"), (labels_path, "labels")):
        shutil.copyfile(source, output / f"source-{name}{source.suffix}")
    support = {
        s: {c: sum(r["category"] == c for r in part) for c in CATEGORIES} for s, part in splits.items()
    }
    return seal(
        output,
        "dataset",
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        split_algorithm=SPLIT_ALGORITHM,
        categories=list(CATEGORIES),
        seed=seed,
        requested_ratios=list(ratios),
        actual_ratios={s: len(part) / len(eligible) for s, part in splits.items()},
        source_count=len(inputs),
        labeled_count=len(eligible),
        pending_count=len(pending),
        support=support,
        warnings=[f"{s} has no {c} examples" for s in SPLITS for c in CATEGORIES if not support[s][c]],
        label_kinds=sorted({r["label_source"]["kind"] for r in eligible}),
    )


def import_gpt(value: JsonObject, *, model: str | None = None) -> list[JsonObject]:
    """Explicit adapters for saved independent-turn-v1 and turn-comparison-v1 results."""
    if "classifications" in value:
        chosen = value["model"]
        if model and model != chosen:
            raise ValueError("Requested GPT model does not match saved run")
        configuration = {
            "model": chosen,
            "reasoning_effort": value["reasoning_effort"],
            "execution": value.get("execution", "independent"),
        }
        pairs = [(row, row) for row in value["classifications"]]
    elif "turn_results" in value:
        selected = [m for m in value["experiment"]["models"] if m["model"] == model]
        if len(selected) != 1:
            raise ValueError("Select exactly one --model from the saved comparison")
        configuration = {**selected[0], "execution": "batched"}
        pairs = [(row, row["classifications"].get(model)) for row in value["turn_results"]]
    else:
        raise ValueError("Unsupported GPT results; supply an explicit conversion")
    rows = []
    for row, label in pairs:
        label = label or {}
        if (
            label.get("model", configuration["model"]) != configuration["model"]
            or label.get("reasoning_effort", configuration["reasoning_effort"])
            != configuration["reasoning_effort"]
        ):
            raise ValueError("Mixed GPT inference configurations")
        error = None
        if (
            label.get("category") not in CATEGORIES
            or not isinstance(label.get("reason"), str)
            or not label["reason"].strip()
            or label.get("prompt_compliance_errors")
        ):
            error = "invalid_or_missing_gpt_result"
        rows.append(
            {
                "session_id": row["original_codex_session_id"],
                "turn_id": row["original_codex_turn_id"],
                "input_sha256": input_hash(row["classification_input_sha256"]),
                "category": label.get("category"),
                "error": error,
                "configuration": configuration,
                "label_source": {"kind": "gpt", "name": digest(configuration)},
            }
        )
    index_rows(rows)
    return rows

"""Standalone deterministic coverage calculation; no network, model, or archive dependency.

Usage: python coverage_analysis.py analysis-input.json coverage.json
"""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

VERSION = "classification-coverage-v1"
CATEGORIES = {"writing", "coding", "bug-fixing", "research", "analysis", "creative-media", "guidance", "other"}


def identity(row):
    pair = tuple(row[k] for k in ("original_codex_session_id", "original_codex_turn_id"))
    if any(not isinstance(x, str) or not x for x in pair):
        raise ValueError("Invalid session/turn identity")
    if not re.fullmatch(r"[0-9a-f]{64}", row["classification_input_sha256"]):
        raise ValueError("Missing or invalid classifier input hash")
    return pair


def calculate(payload):
    if payload.get("format_version") != VERSION:
        raise ValueError("Unsupported coverage recipe")
    targets = payload["targets"]
    target_map = {}
    for target in targets:
        key = identity(target)
        if key in target_map:
            raise ValueError("Duplicate target identity")
        target_map[key] = target["classification_input_sha256"]
    configurations = []
    for source in payload["sources"]:
        value = source["value"]
        if source["format"] == "turn-comparison-v1":
            models = value["experiment"]["models"]
            rows = value["turn_results"]
            row_ids = [identity(row) for row in rows]
            if len(set(row_ids)) != len(row_ids):
                raise ValueError("Duplicate historical target")
            for model in models:
                config = {"model": model["model"], "reasoning_effort": model["reasoning_effort"],
                          "execution": "batched", "taxonomy": "session-purpose-v1"}
                configurations.append((config, [(r, r["classifications"][config["model"]]) for r in rows
                                                if r["classifications"].get(config["model"]) is not None]))
        elif source["format"] == "independent-turn-v1":
            config = {"model": value["model"], "reasoning_effort": value["reasoning_effort"],
                      "execution": "independent", "taxonomy": "session-purpose-v1"}
            configurations.append((config, [(r, r) for r in value["classifications"]]))
        else:
            raise ValueError("Unsupported historical results schema; provide an explicit saved conversion")
    if not configurations:
        raise ValueError("Coverage requires at least one classifier configuration")
    results, config_ids, pending_any = [], set(), set()
    for configuration, rows in configurations:
        cid = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        if cid in config_ids:
            raise ValueError("Duplicate classifier configuration; resolve overlapping runs explicitly")
        config_ids.add(cid)
        labels = {}
        for row, label in rows:
            key = identity(row)
            if key in labels:
                raise ValueError("Duplicate historical target")
            if label["reasoning_effort"] != configuration["reasoning_effort"]:
                raise ValueError("Row inference configuration disagrees with run")
            if "model" in label and label["model"] != configuration["model"]:
                raise ValueError("Row model disagrees with run")
            labels[key] = (row["classification_input_sha256"], label)
        counts, statuses = Counter(), []
        for key, target_hash in target_map.items():
            saved = labels.get(key)
            if saved is None:
                status = "missing"
            elif saved[0] != target_hash:
                status = "changed_input"
            elif (saved[1].get("category") not in CATEGORIES or not isinstance(saved[1].get("reason"), str)
                  or not saved[1]["reason"].strip() or saved[1].get("prompt_compliance_errors")):
                status = "invalid_result"
            else:
                status = "available"
            counts[status] += 1
            if status != "available":
                pending_any.add(key)
            statuses.append({"session_id": key[0], "turn_id": key[1], "input_sha256": target_hash,
                             "status": "available" if status == "available" else "pending", "reason": status})
        outside = sorted(set(labels) - set(target_map))
        results.append({"configuration_id": cid, "configuration": configuration,
                        "requested_targets": len(target_map), "available": counts["available"],
                        "pending": len(target_map) - counts["available"], "reasons": dict(sorted(counts.items())),
                        "outside_requested_subset": [{"session_id": k[0], "turn_id": k[1]} for k in outside],
                        "targets": statuses})
    return {"format_version": VERSION, "target_count": len(target_map),
            "session_count": len({k[0] for k in target_map}), "configuration_count": len(results),
            "targets_pending_any_configuration": len(pending_any),
            "targets_available_all_configurations": len(target_map) - len(pending_any),
            "join": "session_id + turn_id + full classifier input SHA-256, separately per model/configuration",
            "duplicate_policy": "reject", "unmatched_policy": "report pending or outside-requested-subset",
            "classifiers": results}


def output_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_bytes())
    Path(sys.argv[2]).write_bytes(output_bytes(calculate(payload)))

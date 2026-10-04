"""Astra turn distillation: verified sources, chronological groups and shared tokens."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .artifacts import digest
from .data import identity, messages
from .taxonomy import CATEGORIES

PREPROCESSING = "target-first-markers-right-8192-v1"
MAX_TOKENS = 8192
SPLITS = ("train", "validation", "test")
TEACHER = {"execution": "independent", "model": "gpt-6-astra", "reasoning_effort": "xhigh"}


def source_hash(target: list, preceding: list) -> str:
    """Exact historical JSON insertion order, spaces and UTF-8 encoding."""
    value = {"preceding_turn_context": messages(preceding), "target_turn": messages(target)}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def serialize(target: list, preceding: list) -> str:
    sections = []
    for title, items in (("TARGET TURN", target), ("PRECEDING TURN CONTEXT", preceding)):
        body = "\n".join(f"[{m['role']}]\n{m['content']}" for m in messages(items))
        sections.append(f"[{title}]\n{body or '[EMPTY]'}")
    return "\n\n".join(sections)


def tokenize(tokenizer: Any, target: list, preceding: list) -> dict:
    """One sequence; tokenizer truncation preserves its required special tokens."""
    text = serialize(target, preceding)
    tokenizer.truncation_side = "right"
    full = tokenizer(text, truncation=False, padding=False)["input_ids"]
    kept = tokenizer(text, truncation=True, max_length=MAX_TOKENS, padding=False)["input_ids"]
    if len(kept) > MAX_TOKENS or len(kept) > len(full):
        raise ValueError("Tokenizer violated the sequence limit")
    return {
        "input_ids": kept,
        "input_sha256": source_hash(target, preceding),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        # Canonical UTF-8 JSON array: sorted keys, default separators, terminal LF.
        "effective_input_sha256": digest(kept),
        "preprocessing": PREPROCESSING,
        "max_tokens": MAX_TOKENS,
        "truncation_side": "right",
        "original_token_count": len(full),
        "used_token_count": len(kept),
        "dropped_token_count": len(full) - len(kept),
        "truncated": len(full) > len(kept),
    }


def utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("A timestamp is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a time zone")
    return result.astimezone(timezone.utc)


def join_astra(turns: list[dict], reference: dict) -> list[dict]:
    if reference.get("reference_configuration") != TEACHER:
        raise ValueError("Expected the independent GPT-6 Astra xhigh reference")
    indices, answers, keys = {}, {}, set()
    for turn in turns:
        idx = turn.get("index")
        key = identity(
            {
                "session_id": turn.get("original_codex_session_id"),
                "turn_id": turn.get("original_codex_turn_id"),
            }
        )
        if type(idx) is not int or idx in indices or key in keys:
            raise ValueError("Duplicate or invalid target")
        indices[idx], keys = turn, keys | {key}
    for answer in reference["answers"]:
        key = (answer["original_codex_session_id"], answer["original_codex_turn_id"])
        if key in answers or answer.get("category") not in CATEGORIES:
            raise ValueError("Duplicate or invalid Astra answer")
        answers[key] = answer
    if set(answers) != keys:
        raise ValueError("Reference must cover every target exactly once")
    rows = []
    sessions = defaultdict(list)
    for turn in turns:
        sessions[turn["original_codex_session_id"]].append(turn)
    for group in sessions.values():
        group.sort(key=lambda t: (utc(t["started_at"]), t["index"]))
        for position, turn in enumerate(group):
            idx = turn.get("previous_turn_index")
            expected = group[position - 1]["index"] if position else None
            if idx != expected or (idx is not None and idx not in indices):
                raise ValueError("Predecessor must be the immediate earlier turn in this session")
            prior = indices[idx] if idx is not None else None
            target, preceding = messages(turn["messages"]), messages(prior["messages"]) if prior else []
            key = (turn["original_codex_session_id"], turn["original_codex_turn_id"])
            checksum = source_hash(target, preceding)
            answer = answers[key]
            if (
                checksum != turn["classification_input_sha256"]
                or checksum != answer["classification_input_sha256"]
            ):
                raise ValueError("Complete historical input hash does not match")
            rows.append(
                {
                    "session_id": key[0],
                    "turn_id": key[1],
                    "index": turn["index"],
                    "started_at": utc(turn["started_at"]).isoformat(),
                    "source_host": turn.get("source_host", "unknown"),
                    "target_turn": target,
                    "preceding_turn_context": preceding,
                    "category": answer["category"],
                    "input_sha256": checksum,
                }
            )
    return sorted(rows, key=identity)


def chronological_split(rows: list[dict], relations: list[list[str]] = ()) -> tuple[dict, dict]:
    """Keep sessions/forks and substantive identical prefixes together; never stratify."""
    parent = {r["session_id"]: r["session_id"] for r in rows}

    def root(sid):
        parent.setdefault(sid, sid)
        while parent[sid] != sid:
            parent[sid] = parent[parent[sid]]
            sid = parent[sid]
        return sid

    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    for a, b in relations:
        union(a, b)
    # Conservative exact evidence, not generic replies or arbitrary embeddings.
    # Also audit the first 2,048 characters of each chronological conversation.
    sessions, evidence, duplicate_edges = defaultdict(list), {}, []
    for row in rows:
        sessions[row["session_id"]].append(row)
    for sid, group in sorted(sessions.items()):
        group.sort(key=lambda r: (utc(r["started_at"]), r["index"]))
        conversation = "\n".join(serialize(r["target_turn"], []) for r in group)
        candidates = [conversation[:2048]] if len(conversation) >= 2048 else []
        for row in group:
            raw = json.dumps(row["target_turn"], ensure_ascii=False)
            if sum(len(m["content"]) for m in row["target_turn"]) >= 1024:
                candidates.append(raw)
        for value in candidates:
            checksum = hashlib.sha256(value.encode()).hexdigest()
            if checksum in evidence and evidence[checksum] != sid:
                union(sid, evidence[checksum])
                duplicate_edges.append([sid, evidence[checksum], checksum])
            evidence[checksum] = sid
    groups = defaultdict(list)
    for row in rows:
        groups[root(row["session_id"])].append(dict(row, group_id=root(row["session_id"])))
    ordered = sorted(groups, key=lambda g: (max(utc(r["started_at"]) for r in groups[g]), g))
    n = len(ordered)
    if n < 3:
        raise ValueError("Need at least three independent chronological groups")
    holdout = max(1, round(n * 0.15))
    bounds = (0, n - 2 * holdout, n - holdout, n)
    splits, report = (
        {},
        {
            "algorithm": "latest-session-utc-70-15-15-v1",
            "groups": n,
            "recorded_relations": len(relations),
            "duplicate_edges": duplicate_edges,
            "audit_limit": "Exact substantive targets and 2048-character session prefixes; near duplicates may remain",
        },
    )
    for name, lo, hi in zip(SPLITS, bounds, bounds[1:]):
        selected = ordered[lo:hi]
        splits[name] = [r for g in selected for r in groups[g]]
        report[name] = {
            "groups": len(selected),
            "sessions": len({r["session_id"] for r in splits[name]}),
            "turns": len(splits[name]),
            "categories": {c: sum(r["category"] == c for r in splits[name]) for c in CATEGORIES},
            "min_started_at": min(r["started_at"] for r in splits[name]),
            "max_started_at": max(r["started_at"] for r in splits[name]),
        }
    report["temporal_overlap"] = {
        f"{a}_{b}": report[b]["min_started_at"] <= report[a]["max_started_at"]
        for a, b in zip(SPLITS, SPLITS[1:])
    }
    for a, b in (("train", "validation"), ("train", "test"), ("validation", "test")):
        if {r["session_id"] for r in splits[a]} & {r["session_id"] for r in splits[b]}:
            raise ValueError("Session leakage")
    return splits, report


def truncation_summary(rows: list[dict]) -> dict:
    result = {
        "count": len(rows),
        "truncated": sum(r["truncated"] for r in rows),
        "empty_targets": sum(not r["target_turn"] for r in rows),
        "by_category": dict(Counter(r["category"] for r in rows if r["truncated"])),
    }
    for field in ("original_token_count", "used_token_count", "dropped_token_count"):
        values = sorted(r[field] for r in rows)
        result[field] = {str(p): values[round((len(values) - 1) * p / 100)] for p in (0, 50, 90, 95, 100)}
    return result

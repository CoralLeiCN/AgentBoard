"""Usage analysis of one immutable archived rollout, independent of timing sources.

Per-response records take precedence over cumulative mirrors. Legacy-only logs use
cumulative deltas. No app-server schema is assumed to describe rollout JSONL.
"""

import json
from collections import Counter
from decimal import Decimal

from .pricing import catalog, price

VERSION = "codex-usage-v3"
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def token_counts(value):
    if not isinstance(value, dict):
        raise ValueError("Missing token usage object")
    result = {key: value.get(key) for key in FIELDS}
    for key, count in result.items():
        if count is None and key not in ("input_tokens", "output_tokens"):
            continue
        if type(count) is not int or count < 0:
            raise ValueError("Token counts must be nonnegative integers")
    if (result["cached_input_tokens"] or 0) + (result["cache_write_input_tokens"] or 0) > result["input_tokens"]:
        raise ValueError("Cache counts exceed input tokens")
    if (result["reasoning_output_tokens"] or 0) > result["output_tokens"]:
        raise ValueError("Reasoning count exceeds output tokens")
    total = result["input_tokens"] + result["output_tokens"]
    if result["total_tokens"] not in (None, total):
        raise ValueError("Total tokens disagree with input plus output")
    result["total_tokens"] = total
    return result


def totals(rows):
    # Unknown optional breakdowns remain unknown; do not silently turn them into zero.
    return {
        key: sum(row["tokens"][key] for row in rows)
        if rows and all(row["tokens"][key] is not None for row in rows) else None
        for key in FIELDS
    }


def breakdown(tokens):
    """Non-overlapping input split and reasoning's share of all recorded tokens."""
    input_tokens, cached = tokens["input_tokens"], tokens["cached_input_tokens"]
    reasoning, total = tokens["reasoning_output_tokens"], tokens["total_tokens"]
    return {
        "uncached_input_tokens": input_tokens - cached
        if input_tokens is not None and cached is not None else None,
        "reasoning_percent_of_total": 100 * reasoning / total
        if reasoning is not None and total is not None and total > 0 else None,
    }


def summary_mismatches(rows, summaries):
    """Compare chronological checkpoints; token_count may be thread- or turn-scoped."""
    thread_totals = dict.fromkeys(FIELDS, 0)
    turn_totals = {}
    position = 0
    mismatches = []

    def add(target, tokens):
        for key in FIELDS:
            target[key] = (target[key] + tokens[key]
                           if target[key] is not None and tokens[key] is not None else None)

    for summary in summaries:
        while position < len(rows) and rows[position]["line_number"] <= summary["line_number"]:
            row = rows[position]
            add(thread_totals, row["tokens"])
            if isinstance(row["turn_id"], str) and row["turn_id"]:
                add(turn_totals.setdefault(row["turn_id"], dict.fromkeys(FIELDS, 0)), row["tokens"])
            position += 1
        candidates = []
        if summary["scope"] in ("thread", "thread_or_turn"):
            candidates.append(thread_totals)
        if summary["scope"] in ("turn", "thread_or_turn") and summary["turn_id"]:
            candidates.append(turn_totals.get(summary["turn_id"], dict.fromkeys(FIELDS, 0)))
        if candidates and not any(all(
            counts[key] is None or summary["tokens"][key] is None or counts[key] == summary["tokens"][key]
            for key in FIELDS
        ) for counts in candidates):
            mismatches.append(summary)
    return mismatches


def analyze(lines, model_override=""):
    pricing = catalog()
    if model_override and model_override not in pricing["models"]:
        raise ValueError("Choose a model from the pricing catalog")
    detailed, legacy, seen = [], [], {}
    findings = Counter()
    model, turn = None, None
    previous = None
    detail_records = 0
    reported_total = 0
    summaries = []

    def remember_summary(value, scope, row):
        try:
            counts = token_counts(value)
        except ValueError:
            return
        summaries.append({"line_number": row["line_number"], "scope": scope,
                          "turn_id": row["turn_id"] if isinstance(row["turn_id"], str) else None,
                          "tokens": counts})

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        record = json.loads(line)
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        outer, kind = record.get("type"), payload.get("type")
        if outer in ("session_meta", "turn_context"):
            # Use chronological evidence, never last-seen session metadata for old requests.
            if "model" in payload:
                model = payload["model"] if isinstance(payload["model"], str) else None
            turn = payload.get("turn_id", turn)
        if outer == "event_msg" and kind == "task_started":
            turn = payload.get("turn_id", turn)
        direct = outer == "token_usage_record"
        if not direct and not (outer == "event_msg" and kind == "token_count"):
            continue
        row = {
            "line_number": line_number, "timestamp": record.get("timestamp"),
            "turn_id": payload.get("turn_id", turn), "response_id": None,
            "model": payload.get("model", model), "request_known": True,
        }
        if not isinstance(row["model"], str):
            row["model"] = None
        if direct:
            detail_records += 1
            try:
                counts = token_counts(payload.get("usage"))
            except ValueError:
                findings["invalid_usage_records"] += 1
                continue
            response_id = payload.get("response_id")
            if not isinstance(response_id, str) or not response_id:
                findings["missing_response_ids"] += 1
                continue
            if response_id in seen:
                findings["duplicate_response_records" if seen[response_id] == counts
                         else "conflicting_response_records"] += 1
                continue
            seen[response_id] = counts
            row.update(tokens=counts, response_id=response_id, method="response_usage")
            detailed.append(row)
            remember_summary(payload.get("thread_token_usage"), "thread", row)
            remember_summary(payload.get("turn_token_usage"), "turn", row)
            try:
                reported_total = max(reported_total, token_counts(payload.get("thread_token_usage"))["total_tokens"])
            except ValueError:
                pass
        else:
            info = payload.get("info")
            if info is None:
                findings["empty_token_notifications"] += 1
                continue
            try:
                cumulative = token_counts(info.get("total_token_usage") if isinstance(info, dict) else None)
            except ValueError:
                findings["invalid_cumulative_records"] += 1
                continue
            reported_total = max(reported_total, cumulative["total_tokens"])
            remember_summary(cumulative, "thread_or_turn", row)
            if previous == cumulative:
                findings["duplicate_cumulative_records"] += 1
                continue
            if previous and any(cumulative[key] is not None and previous[key] is not None
                                and cumulative[key] < previous[key] for key in FIELDS):
                findings["cumulative_resets"] += 1
                previous = cumulative
                continue
            delta = {
                key: cumulative[key] - previous[key] if previous and cumulative[key] is not None
                and previous[key] is not None else cumulative[key] if previous is None else None
                for key in FIELDS
            }
            previous = cumulative
            try:
                delta = token_counts(delta)
            except ValueError:
                findings["invalid_cumulative_deltas"] += 1
                continue
            if delta["total_tokens"] == 0:
                continue
            try:
                last = token_counts(info.get("last_token_usage"))
                row["request_known"] = all(delta[key] == last[key] for key in ("input_tokens", "output_tokens"))
            except ValueError:
                row["request_known"] = False
            if not row["request_known"]:
                row["model"] = None
            row.update(tokens=delta, method="cumulative_delta")
            legacy.append(row)

    rows = detailed if detail_records else legacy
    mismatches = summary_mismatches(rows, summaries) if detail_records else []
    if detail_records:
        # These are alternative accounts of usage, never independent billable streams.
        for key in ("invalid_cumulative_records", "cumulative_resets", "invalid_cumulative_deltas"):
            findings.pop(key, None)
        if reported_total > (totals(rows)["total_tokens"] or 0):
            findings["tokens_outside_response_records"] = reported_total - (totals(rows)["total_tokens"] or 0)
        if mismatches:
            findings["usage_summary_mismatches"] = len(mismatches)
    partial_keys = {"invalid_usage_records", "missing_response_ids", "conflicting_response_records",
                    "invalid_cumulative_records", "cumulative_resets", "invalid_cumulative_deltas",
                    "tokens_outside_response_records", "usage_summary_mismatches"}
    partial = any(findings[key] for key in partial_keys)

    for row in rows:
        row["pricing_model"] = model_override or row["model"]
        row["breakdown"] = breakdown(row["tokens"])
    long_models = {
        row["pricing_model"] for row in rows
        if row["request_known"] and row["tokens"]["input_tokens"] > 272000
        and pricing["models"].get(row["pricing_model"], {}).get("long_context_scope") == "session"
    }
    # A multi-request delta cannot establish the session-wide context tier.
    uncertain_models = {
        row["pricing_model"] for row in rows if not row["request_known"]
    }
    running_cost = Decimal(0)
    running_tokens = 0
    unpriced = 0
    priced_so_far = 0
    for row in rows:
        selected = row["pricing_model"]
        if (selected in uncertain_models and selected not in long_models
                and pricing["models"].get(selected, {}).get("long_context_scope") == "session"):
            row["cost"] = {"usd": None, "reason": "Session context tier unavailable"}
        else:
            row["cost"] = price(row["tokens"], selected, request_known=row["request_known"],
                                session_long=selected in long_models)
        running_tokens += row["tokens"]["total_tokens"]
        if row["cost"]["usd"] is None:
            unpriced += 1
        else:
            running_cost += Decimal(row["cost"]["usd"])
            priced_so_far += 1
        row["cumulative_tokens"] = running_tokens
        row["cumulative_priced_usd"] = str(running_cost) if priced_so_far else None
    priced_count = len(rows) - unpriced
    groups = []
    for name in dict.fromkeys(row["model"] for row in rows):
        members = [row for row in rows if row["model"] == name]
        model_tokens = totals(members)
        priced = [row for row in members if row["cost"]["usd"] is not None]
        groups.append({
            "model": name, "records": len(members), "tokens": model_tokens,
            "breakdown": breakdown(model_tokens),
            "priced_records": len(priced),
            "priced_subtotal_usd": str(sum((Decimal(row["cost"]["usd"]) for row in priced), Decimal(0)))
            if priced else None,
        })
    session_tokens = totals(rows)
    return {
        "analysis_version": VERSION, "source": "token_usage_record" if detail_records else "token_count",
        "scope": "selected_raw_archive", "coverage": "unavailable" if not rows else "partial" if partial else "recorded",
        "tokens": session_tokens, "breakdown": breakdown(session_tokens),
        "records": len(rows), "priced_records": priced_count,
        "unpriced_records": unpriced, "estimated_cost_usd": str(running_cost) if rows and not unpriced and not partial else None,
        "priced_subtotal_usd": str(running_cost) if priced_count else None,
        "model_override": model_override or None, "pricing": pricing,
        "models": groups, "items": rows, "findings": dict(+findings),
        "usage_summary_mismatches": mismatches,
        "note": "Standard API token value in USD using the dated price catalog, not an actual bill. "
                "Uncached input excludes cache reads and includes cache writes; reasoning is included in output. "
                "Reasoning percentage uses input plus output as the denominator. "
                "Cumulative mirrors are not added to response records. Only this archived rollout is counted; "
                "missing activity and separate child sessions are excluded. Tool fees, regional uplifts, "
                "Fast/Batch/Flex pricing, subscription charges and taxes are excluded.",
    }

"""Cross-session usage from latest archives, with explicit time and coverage semantics."""

import json
from collections import defaultdict
from decimal import Decimal

from .lineage import leaves, resolve, same_value
from .pricing import catalog
from .timestamps import normalize_timestamp
from .usage import totals


def aggregate(rows):
    priced = [r for r in rows if r["cost"]["usd"] is not None]
    return {
        "tokens": totals(rows), "records": len(rows), "priced_records": len(priced),
        "unpriced_records": len(rows) - len(priced),
        "priced_subtotal_usd": str(sum((Decimal(r["cost"]["usd"]) for r in priced), Decimal(0)))
        if priced else None,
    }


def build_report(sessions, usage, active, *, start="", end="", q="", agent="", producer="",
                 model="", tags=(), metadata="{}", limit=20, offset=0):
    start = normalize_timestamp(start) if start else None
    end = normalize_timestamp(end) if end else None
    if start and end and start >= end:
        raise ValueError("Start must precede end (end is exclusive)")
    if producer not in ("", "agentboard", "unmarked"):
        raise ValueError("Unknown producer filter")
    try:
        metadata = json.loads(metadata)
    except (ValueError, TypeError) as exc:
        raise ValueError("Metadata must be a JSON object of JSON Pointer/value pairs") from exc
    if not isinstance(metadata, dict) or any(not key.startswith("/") for key in metadata):
        raise ValueError("Metadata must be a JSON object of JSON Pointer/value pairs")

    def within(value):
        return value is not None and (start is None or value >= start) and (end is None or value < end)

    facets = {"agents": set(), "tags": set(), "metadata": defaultdict(set), "models": set()}
    all_rows, items = [], []
    unknown_times = 0
    excluded_unknown_times = 0
    undated = []
    by_day, by_model = defaultdict(list), defaultdict(list)
    for session in sessions:
        facets["agents"].add(session["agent"])
        facets["tags"].update(session["tags"])
        for key, value in leaves(session["metadata"]).items():
            if key:
                facets["metadata"][key].add(json.dumps(value, ensure_ascii=False, sort_keys=True))
        if (q.casefold() not in (session["title"] + " " + session["id"]).casefold()
                or agent and session["agent"] != agent
                or producer == "agentboard" and session["producer"] != "agentboard"
                or producer == "unmarked" and session["producer"] is not None
                or not set(tags).issubset(session["tags"])):
            continue
        if any(not found or not same_value(value, expected)
               for key, expected in metadata.items()
               for found, value in [resolve(session["metadata"], key)]):
            continue
        report = usage(session["id"])
        rows = []
        session_unknown = 0
        for row in report["items"]:
            if row["model"]:
                facets["models"].add(row["model"])
            if model and row["model"] != model:
                continue
            try:
                stamp = normalize_timestamp(row["timestamp"])
            except (ValueError, TypeError):
                stamp = None
                session_unknown += 1
            if (start or end) and not within(stamp):
                continue
            rows.append({**row, "day": stamp[:10] if stamp else None})
        unknown_times += session_unknown
        if start or end:
            excluded_unknown_times += session_unknown
        if model and not rows:
            continue
        if (start or end) and not rows and not within(session["started_at"]) and not active(
            session["id"], start, end
        ):
            continue
        summary = aggregate(rows)
        summary.update({key: session[key] for key in ("id", "title", "agent", "producer", "tags", "started_at")})
        summary["coverage"] = "unavailable" if not rows else "partial" if (
            report["coverage"] == "partial" or (start or end) and session_unknown
        ) else "recorded"
        items.append(summary)
        all_rows.extend(rows)
        for row in rows:
            by_model[row["model"]].append(row)
            if row["day"]:
                by_day[row["day"]].append(row)
            else:
                undated.append(row)

    summary = aggregate(all_rows)
    missing = sum(item["coverage"] == "unavailable" for item in items)
    partial = sum(item["coverage"] == "partial" for item in items)
    summary.update(
        sessions=len(items), sessions_with_usage=len(items) - missing,
        sessions_without_usage=missing, partial_sessions=partial,
        unknown_time_records=unknown_times, excluded_unknown_time_records=excluded_unknown_times,
        estimated_cost_usd=summary["priced_subtotal_usd"]
        if not (missing or partial or summary["unpriced_records"] or excluded_unknown_times) else None,
    )
    return {
        "summary": summary, "items": items[offset:offset + limit],
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "daily": [{"day": day, **aggregate(rows)} for day, rows in sorted(by_day.items())],
        "undated": aggregate(undated),
        "models": [{"model": name, **aggregate(rows)} for name, rows in by_model.items()],
        "facets": {**{key: sorted(facets[key]) for key in ("agents", "tags", "models")},
                   "metadata": {key: sorted(values) for key, values in sorted(facets["metadata"].items())}},
        "filters": {"start": start, "end": end, "q": q, "agent": agent, "producer": producer,
                    "model": model, "tags": list(tags), "metadata": metadata},
        "pricing": catalog(),
        "note": "Recorded usage in the selected UTC period, start inclusive and end exclusive. "
                "Sessions count when they start or have recorded events/usage in the period. "
                "Only the latest archive per session is used; separate child sessions count independently. "
                "Inherited history shared by forked sessions may overlap. "
                "Model filters select usage records; metadata and all selected tags filter sessions. "
                "Cumulative differences are assigned to their ending timestamp. Undated usage is included "
                "only for All time. Missing usage is not zero. Standard API token value uses the dated "
                "catalog, not actual billing; tool fees, subscription charges, taxes and nonstandard tiers "
                "are excluded. Input includes cache reads/writes; output includes reasoning.",
    }

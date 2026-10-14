"""Strict source-span contract; the model supplies findings, never edited text."""

import json
from dataclasses import dataclass
from typing import Any

CATEGORIES = (
    "PERSON",
    "EMAIL",
    "PHONE",
    "ADDRESS",
    "ACCOUNT",
    "USERNAME",
    "HOST",
    "PATH",
    "PROJECT",
    "SECRET",
    "CONFIDENTIAL",
)
POLICY_VERSION = "turn-privacy-v2"

FINDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chunk_id": {"type": "string"},
        "requires_review": {"type": "boolean"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "occurrence": {"type": "integer", "minimum": -1},
                    "quote": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                },
                "required": ["occurrence", "quote", "category"],
            },
        },
    },
    "required": ["chunk_id", "requires_review", "findings"],
}


class PrivacyError(ValueError):
    """A safe-to-display error. Never include source text, model output or credentials."""


class InvalidFindings(PrivacyError):
    """A completed response failed validation; eligible for one bounded retry."""


def load_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PrivacyError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise PrivacyError("Non-finite JSON value")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PrivacyError("Invalid JSON") from exc


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    message_id: str
    role: str
    start: int
    text: str


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    quote: str
    category: str


@dataclass(frozen=True)
class Scan:
    findings: tuple[Finding, ...]
    requires_review: bool


def validate_result(value: Any, chunk: Chunk) -> Scan:
    if not isinstance(value, dict) or set(value) != {"chunk_id", "requires_review", "findings"}:
        raise PrivacyError("Unexpected result fields")
    if value["chunk_id"] != chunk.chunk_id or type(value["requires_review"]) is not bool:
        raise PrivacyError("Invalid chunk identity or review flag")
    if not isinstance(value["findings"], list):
        raise PrivacyError("Findings must be an array")
    findings = []
    for item in value["findings"]:
        if not isinstance(item, dict) or set(item) != {"start", "end", "quote", "category"}:
            raise PrivacyError("Unexpected finding fields")
        start, end, quote, category = (item[k] for k in ("start", "end", "quote", "category"))
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(chunk.text):
            raise PrivacyError("Invalid finding bounds")
        if type(quote) is not str or quote != chunk.text[start:end]:
            raise PrivacyError("Finding quote does not match source")
        if type(category) is not str or category not in CATEGORIES:
            raise PrivacyError("Unknown finding category")
        findings.append(Finding(start + chunk.start, end + chunk.start, quote, category))
    return Scan(merge_findings(findings), value["requires_review"])


def resolve_result(value: Any, chunk: Chunk) -> Scan:
    """Resolve exact quotes locally, then apply the canonical strict span validator."""
    if not isinstance(value, dict) or set(value) != {"chunk_id", "requires_review", "findings"}:
        raise PrivacyError("Unexpected result fields")
    if not isinstance(value["findings"], list):
        raise PrivacyError("Findings must be an array")
    spans = []
    for item in value["findings"]:
        if not isinstance(item, dict) or set(item) != {"occurrence", "quote", "category"}:
            raise PrivacyError("Unexpected quote finding fields")
        quote, occurrence = item["quote"], item["occurrence"]
        if type(quote) is not str or not quote or type(occurrence) is not int or occurrence < -1:
            raise PrivacyError("Invalid quote or occurrence selector")
        offsets, offset = [], 0
        while (offset := chunk.text.find(quote, offset)) >= 0:
            offsets.append(offset)
            offset += 1
        if not offsets or occurrence >= len(offsets):
            raise PrivacyError("Quote or occurrence does not match source")
        for start in offsets if occurrence == -1 else [offsets[occurrence]]:
            spans.append(
                {"start": start, "end": start + len(quote), "quote": quote, "category": item["category"]}
            )
    return validate_result({**value, "findings": spans}, chunk)


def merge_findings(findings: list[Finding], *, allow_contained: bool = False) -> tuple[Finding, ...]:
    """Keep a complete validated quote over its same-category window fragment."""
    merged = []
    for finding in sorted(set(findings), key=lambda f: (f.start, -f.end, f.category)):
        if merged and finding.start < merged[-1].end:
            previous = merged[-1]
            if (
                allow_contained
                and finding.end <= previous.end
                and finding.category == previous.category
                and previous.quote[finding.start - previous.start : finding.end - previous.start]
                == finding.quote
            ):
                continue
            raise PrivacyError("Conflicting overlapping findings require review")
        merged.append(finding)
    return tuple(merged)


def chunk_message(message_id: str, role: str, text: str, size: int, overlap: int) -> list[Chunk]:
    if type(size) is not int or type(overlap) is not int or not 0 <= overlap < size <= 12000:
        raise PrivacyError("Require 0 <= overlap < chunk characters <= 12000")
    if not text.strip():
        return []
    result = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        result.append(Chunk(f"{message_id}-c{len(result):05d}", message_id, role, start, text[start:end]))
        if end == len(text):
            break
        start = end - overlap
    return result


def replace_spans(text: str, findings: tuple[Finding, ...], replacements: dict[tuple[str, str], str]) -> str:
    result = text
    for finding in reversed(findings):
        if text[finding.start : finding.end] != finding.quote:
            raise PrivacyError("Source changed before replacement")
        result = (
            result[: finding.start] + replacements[(finding.category, finding.quote)] + result[finding.end :]
        )
    return result

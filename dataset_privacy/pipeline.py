"""Private draft builder. Successful detection never grants privacy approval."""

import hashlib
import json
import platform
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import VERSION
from .codex import write_json, write_private
from .contracts import (
    FINDING_SCHEMA,
    POLICY_VERSION,
    Chunk,
    InvalidFindings,
    PrivacyError,
    Scan,
    chunk_message,
    load_json,
    merge_findings,
    replace_spans,
)
from .prompt_loader import INSTRUCTIONS, PROMPT_TEXT
from .source import Projection, project


class Detector(Protocol):
    def scan(self, chunk: Chunk, attempt: Path, *, feedback: str | None = None) -> Scan: ...


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_source(
    path: Path, limit: int | None = None, expected_sha256: str | None = None
) -> tuple[Projection, str]:
    original = path.read_bytes()
    sha256 = digest(original)
    if expected_sha256 is not None and expected_sha256 != sha256:
        raise PrivacyError("Source hash does not match the selected version")
    try:
        rows = load_json(original.decode("utf-8"))
    except UnicodeError as exc:
        raise PrivacyError("Source is not valid UTF-8") from exc
    return project(rows, limit), sha256


def plan(projection: Projection, size: int, overlap: int) -> list[Chunk]:
    # Validate even if all selected targets are excluded or empty.
    chunk_message("settings", "user", "", size, overlap)
    return [
        chunk
        for message in projection.messages.values()
        for chunk in chunk_message(message.message_id, message.role, message.content, size, overlap)
    ]


def counts(projection: Projection, chunks: list[Chunk]) -> dict:
    return {
        "source_turns": projection.source_rows,
        "selected_turns": len(projection.selection),
        "eligible_examples": len(projection.examples),
        "excluded_turns": len(projection.selection) - len(projection.examples),
        "selection_status": dict(Counter(row["status"] for row in projection.selection)),
        "unique_messages": len(projection.messages),
        "chunks": len(chunks),
    }


def _checkpoint(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    write_json(temporary, value)
    temporary.replace(path)


def prepare(
    source: Path,
    output: Path,
    detector: Detector,
    *,
    limit: int | None = None,
    expected_sha256: str | None = None,
    chunk_characters: int = 4096,
    overlap: int = 512,
    max_chunks: int = 256,
    validation_retries: int = 1,
) -> dict:
    """Prepare only a private draft; refuse overwrite and any failed/incomplete scan."""
    if (
        type(max_chunks) is not int
        or max_chunks < 1
        or type(validation_retries) is not int
        or validation_retries not in (0, 1)
    ):
        raise PrivacyError("Invalid execution budget")
    source, output = source.resolve(), output.absolute()
    projection, source_sha = load_source(source, limit, expected_sha256)
    chunks = plan(projection, chunk_characters, overlap)
    if len(chunks) > max_chunks:
        raise PrivacyError(
            "Planned chunks exceed the explicit execution budget; use plan or a smaller selection"
        )
    if not projection.examples:
        raise PrivacyError("Selection has no eligible targets")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    namespace = uuid.uuid4().hex
    manifest = {
        "schema_version": "codex-privacy-draft-v1",
        "build_id": namespace,
        "status": "running",
        "privacy_status": "needs_review",
        "task_profile": "completed-turn-classification-v1",
        "created_at": timestamp(),
        "source": {"path": str(source), "sha256": source_sha},
        "code_version": VERSION,
        "python_version": platform.python_version(),
        "policy_version": POLICY_VERSION,
        "code_sha256": {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "instructions_sha256": digest(INSTRUCTIONS.encode()),
        "prompt_sha256": {name: digest(text.encode("utf-8")) for name, text in PROMPT_TEXT.items()},
        "schema_sha256": digest(json.dumps(FINDING_SCHEMA, sort_keys=True).encode()),
        "recipe": {
            "limit": limit,
            "chunk_characters": chunk_characters,
            "overlap": overlap,
            "max_chunks": max_chunks,
            "validation_retries": validation_retries,
        },
        "counts": counts(projection, chunks),
        "completed_chunks": 0,
        "chunk_receipts": "chunks/",
    }
    chunk_plan = [
        {
            "chunk_id": c.chunk_id,
            "message_id": c.message_id,
            "start": c.start,
            "end": c.start + len(c.text),
            "status": "pending",
        }
        for c in chunks
    ]
    try:
        _checkpoint(output / "manifest.json", manifest)
        write_json(output / "selection.json", projection.selection)
        write_json(output / "lineage.json", projection.lineage)
        write_private(output / "instructions.txt", INSTRUCTIONS)
        write_json(output / "schema.json", FINDING_SCHEMA)
        attempts = output / "attempts"
        attempts.mkdir(mode=0o700)
        receipts = output / "chunks"
        receipts.mkdir(mode=0o700)
        for receipt in chunk_plan:
            write_json(receipts / f"{receipt['chunk_id']}.json", receipt)
        findings = defaultdict(list)
        flagged_messages = set()
        for chunk, receipt in zip(chunks, chunk_plan):
            receipt_path = receipts / f"{chunk.chunk_id}.json"
            feedback = None
            for attempt in range(validation_retries + 1):
                receipt["attempts"] = attempt + 1
                receipt["status"] = "running"
                _checkpoint(receipt_path, receipt)
                try:
                    scan = detector.scan(chunk, attempts / f"{chunk.chunk_id}-a{attempt}", feedback=feedback)
                    break
                except InvalidFindings as exc:
                    receipt.setdefault("validation_failures", []).append(type(exc).__name__)
                    if attempt == validation_retries:
                        receipt["status"] = "failed"
                        raise
                    feedback = "Previous output failed validation. Use exact source quotes/occurrences and conform to the schema."
                except BaseException:
                    receipt["status"] = "failed"
                    raise
                finally:
                    _checkpoint(receipt_path, receipt)
            findings[chunk.message_id].extend(scan.findings)
            if scan.requires_review:
                flagged_messages.add(chunk.message_id)
            receipt["status"] = "completed"
            receipt["requires_review"] = scan.requires_review
            manifest["completed_chunks"] += 1
            _checkpoint(receipt_path, receipt)
            _checkpoint(output / "manifest.json", manifest)
        merged = {mid: merge_findings(findings[mid], allow_contained=True) for mid in projection.messages}
        contained_findings_collapsed = sum(len(set(findings[mid])) - len(merged[mid]) for mid in merged)
        mapping = defaultdict(dict)
        used_replacements = set()
        # Reserve every original placeholder-looking string by checking proposed replacements against sources.
        corpus = "\n".join(m.content for m in projection.messages.values())
        for mid, message in projection.messages.items():
            for finding in merged[mid]:
                key = (finding.category, finding.quote)
                group_map = mapping[message.group_id]
                if key not in group_map:
                    suffix = len(group_map) + 1
                    replacement = f"<{finding.category}_{namespace[:8]}_{message.group_id}_{suffix}>"
                    while replacement in corpus or replacement in used_replacements:
                        suffix += 1
                        replacement = f"<{finding.category}_{namespace[:8]}_{message.group_id}_{suffix}>"
                    group_map[key] = replacement
                    used_replacements.add(replacement)
        transformed = {
            mid: replace_spans(message.content, merged[mid], mapping[message.group_id])
            for mid, message in projection.messages.items()
        }
        write_json(output / "findings.json", {mid: [asdict(f) for f in fs] for mid, fs in merged.items()})
        write_json(
            output / "mapping.json",
            [
                {"group_id": group, "category": category, "original": quote, "replacement": replacement}
                for group, pairs in mapping.items()
                for (category, quote), replacement in pairs.items()
            ],
        )

        def render(ids):
            return [{"role": projection.messages[mid].role, "content": transformed[mid]} for mid in ids]

        drafts = []
        for example in projection.examples:
            drafts.append(
                {
                    "example_id": example.example_id,
                    "group_id": example.group_id,
                    "input": {
                        "messages": render(example.target),
                        "context": render(example.context),
                        "context_status": example.context_status,
                    },
                    "target": None,
                    "target_status": "unreviewed_for_transformed_input",
                    "privacy_status": "needs_review",
                    "detector_requested_review": bool(
                        flagged_messages.intersection(example.target + example.context)
                    ),
                }
            )
        encoded = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in drafts)
        write_private(output / ".draft.pending", encoded)
        if digest(source.read_bytes()) != source_sha:
            raise PrivacyError("Source changed during preparation")
        manifest.update(
            status="complete_draft",
            finished_at=timestamp(),
            source_unchanged=True,
            draft_sha256=digest(encoded.encode()),
            finding_count=sum(len(fs) for fs in merged.values()),
            contained_findings_collapsed=contained_findings_collapsed,
        )
        (output / ".draft.pending").replace(output / "draft.jsonl")
        _checkpoint(output / "manifest.json", manifest)
        return manifest
    except BaseException as exc:
        for path in (output / "draft.jsonl", output / ".draft.pending", output / ".manifest.json.tmp"):
            path.unlink(missing_ok=True)
        manifest.update(status="failed", finished_at=timestamp(), error_type=type(exc).__name__)
        if isinstance(exc, PrivacyError):
            manifest["error_reason"] = str(exc)
        manifest.pop("draft_sha256", None)
        _checkpoint(output / "manifest.json", manifest)
        raise

"""Allowlist projection from pinned turn JSON; no source application imports."""

from dataclasses import dataclass
from typing import Any

from .contracts import PrivacyError


@dataclass(frozen=True)
class Message:
    message_id: str
    group_id: str
    role: str
    content: str


@dataclass(frozen=True)
class Example:
    example_id: str
    group_id: str
    target: tuple[str, ...]
    context: tuple[str, ...]
    context_status: str


@dataclass(frozen=True)
class Projection:
    messages: dict[str, Message]
    examples: tuple[Example, ...]
    selection: tuple[dict, ...]
    source_rows: int
    lineage: tuple[dict, ...]


def project(rows: Any, limit: int | None = None) -> Projection:
    if not isinstance(rows, list) or not rows:
        raise PrivacyError("Source must be a nonempty array of turns")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise PrivacyError("Limit must be a positive integer")
    by_index = {}
    sessions = {}
    for row in rows:
        if not isinstance(row, dict) or not {
            "index",
            "previous_turn_index",
            "original_codex_session_id",
            "messages",
        } <= set(row):
            raise PrivacyError("Missing source adapter fields")
        index = row["index"]
        session = row["original_codex_session_id"]
        if type(index) is not int or index < 0 or index in by_index:
            raise PrivacyError("Invalid or duplicate turn index")
        if type(session) is not str or not session or not isinstance(row["messages"], list):
            raise PrivacyError("Invalid session identity or messages")
        for message in row["messages"]:
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("user", "assistant")
                or type(message.get("content")) is not str
            ):
                raise PrivacyError("Unsupported role or non-text message")
            try:
                message["content"].encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PrivacyError("Invalid Unicode source text") from exc
        by_index[index] = row
        sessions.setdefault(session, session)

    def root(session):
        while sessions[session] != session:
            sessions[session] = sessions[sessions[session]]
            session = sessions[session]
        return session

    for row in rows:
        prior = row["previous_turn_index"]
        if prior is None:
            continue
        if type(prior) is not int or prior not in by_index or prior == row["index"]:
            raise PrivacyError("Missing, invalid or self-referential predecessor")
        sessions[root(row["original_codex_session_id"])] = root(by_index[prior]["original_codex_session_id"])
    complete = set()
    for index in by_index:
        visiting = set()
        current = index
        while current is not None and current not in complete:
            if current in visiting:
                raise PrivacyError("Cyclic predecessor links")
            visiting.add(current)
            current = by_index[current]["previous_turn_index"]
        complete.update(visiting)

    groups = {}
    message_keys = {}
    messages = {}
    lineage = []

    def inventory(row, group):
        result = []
        for position, message in enumerate(row["messages"]):
            key = (row["index"], position)
            if key not in message_keys:
                mid = f"m{len(messages):07d}"
                message_keys[key] = mid
                messages[mid] = Message(mid, group, message["role"], message["content"])
                lineage.append(
                    {
                        "message_id": mid,
                        "group_id": group,
                        "source_index": row["index"],
                        "message_position": position,
                    }
                )
            result.append(message_keys[key])
        return tuple(result)

    examples = []
    selection = []
    for row in rows[:limit]:
        status = "needs_review"
        if not any(m["content"].strip() for m in row["messages"]):
            status = "empty_target"
        elif not any(m["role"] == "user" and m["content"].strip() for m in row["messages"]):
            status = "no_user_input"
        eid = f"e{len(selection):07d}"
        selection.append({"source_index": row["index"], "example_id": eid, "status": status})
        if status != "needs_review":
            continue
        family = root(row["original_codex_session_id"])
        group = groups.setdefault(family, f"g{len(groups):07d}")
        target = inventory(row, group)
        prior = row["previous_turn_index"]
        context = inventory(by_index[prior], group) if prior is not None else ()
        examples.append(
            Example(eid, group, target, context, "present" if prior is not None else "absent_in_source")
        )
    return Projection(messages, tuple(examples), tuple(selection), len(rows), tuple(lineage))

"""Conservative rollout input attribution, independent of the transport role.

These are local interpretation rules, not an upstream schema or proof of authorship.
"""

import re

METHOD = "codex-input-v1"
CONTEXT_BLOCK = re.compile(r"<(environment_context|INSTRUCTIONS|recommended_plugins)>.*?</\1>", re.S)
AGENTS_HEADER = re.compile(r"# AGENTS\.md instructions for [^\n]+\n+")
INTERNAL_BLOCK = re.compile(r"<subagent_notification\b[^>]*>.*?</subagent_notification>", re.S)


def session_input_origin(metadata):
    source = metadata.get("source")
    evidence = {}
    if (isinstance(source, dict) and "subagent" in source) or source == "subagent":
        evidence["session_meta.source"] = source
    if metadata.get("thread_source") == "guardian_review":
        evidence["session_meta.thread_source"] = metadata["thread_source"]
    parent = metadata.get("parent_thread_id")
    if isinstance(source, dict) and isinstance(source.get("subagent"), dict):
        spawn = source["subagent"].get("thread_spawn")
        if isinstance(spawn, dict):
            parent = spawn.get("parent_thread_id", parent)
    return {
        "origin": "internal" if evidence else "unknown",
        "method": METHOD,
        "basis": "recorded subagent/reviewer session source" if evidence else "no explicit internal session source",
        "evidence": evidence,
        "parent_session_id": parent if isinstance(parent, str) and parent else None,
    }


def context_blocks(text):
    """Only complete, unquoted context envelopes; prose and fenced examples survive."""
    remaining = text.strip()
    tags = []
    while remaining:
        header = AGENTS_HEADER.match(remaining)
        if header:
            remaining = remaining[header.end():].lstrip()
        match = CONTEXT_BLOCK.match(remaining)
        if not match:
            return []
        tag = match[1]
        # INSTRUCTIONS alone is a valid human prompt; require the AGENTS header.
        if (tag == "INSTRUCTIONS") != bool(header):
            return []
        tags.append(tag)
        remaining = remaining[match.end():].strip()
    return tags


def attribute_input(text, payload, metadata):
    source = session_input_origin(metadata)
    # An accompanying image/audio is real input evidence even if its text is a wrapper.
    content = payload.get("content", [])
    multimodal = isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") not in (None, "input_text", "text")
        for part in content
    )
    multimodal = multimodal or any(payload.get(key) for key in ("images", "local_images", "audio"))
    tags = context_blocks(text) if not multimodal else []
    notification = INTERNAL_BLOCK.match(text.strip()) if not multimodal else None
    if tags:
        origin, basis, evidence = "context", "entire text is a recognized context envelope", {"envelopes": tags}
    elif source["origin"] == "internal":
        origin, basis, evidence = "internal", source["basis"], source["evidence"]
    elif notification and notification.end() == len(text.strip()):
        origin, basis, evidence = "internal", "entire text is a subagent notification", {
            "envelopes": ["subagent_notification"]
        }
    else:
        origin, basis, evidence = "human", (
            "user message without recognized context/internal evidence; human authorship is inferred"
        ), {}
    return {"origin": origin, "method": METHOD, "basis": basis, "evidence": evidence}

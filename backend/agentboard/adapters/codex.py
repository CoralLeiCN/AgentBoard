"""Streaming Codex rollout adapter. Never mutates Codex files or instruments its hot path."""

import json
import re
import shlex

from ..domain import Event, RawLine, RawTraceEnd, Session, is_user_input_tool, stable_id
from ..input_origin import attribute_input, session_input_origin
from ..lineage import CodexLineage, copied, ref, spec
from ..timestamps import format_timestamp, normalize_timestamp


def content_text(content):
    if isinstance(content, str):
        return content
    # Codex can split one prompt into adjacent input_text fragments. Inserting a
    # newline changes the prompt and prevents matching its event_msg mirror.
    return "".join(p.get("text", "") for p in (content or []) if isinstance(p, dict))


def command_text(command):
    if command is None:
        return ""
    if isinstance(command, str):
        return command
    if isinstance(command, list) and all(isinstance(arg, str) for arg in command):
        return shlex.join(command)
    return json.dumps(command, ensure_ascii=False)


class CodexAdapter:
    mapping_version = "codex-jsonl-v5"

    def parse(self, lines):
        sid = None
        turn = None
        previous_turn = None
        pending = {}
        anchor = None
        active = False
        seen_inputs = {}
        fallback_inputs = {}
        session = None
        seq = 0
        waiting_since = None
        lineage = CodexLineage()

        turn_origin = None

        def can_wait():
            return session_input_origin(session.metadata)["origin"] != "internal" and turn_origin not in (
                "internal", "context"
            )

        def wait_until(ts):
            nonlocal waiting_since
            start, waiting_since = waiting_since, None
            if start is not None and ts > start:
                return event(
                    "user_wait",
                    "Between turns (inferred user wait)",
                    start,
                    "user-wait",
                    end_time=ts,
                    timing="estimated",
                    raw_line_numbers=[],
                    attributes={
                        "wait_type": "between_turns",
                        "basis": "turn completion to next user prompt; may include idle time",
                    },
                )

        def event(kind, name, ts, suffix="", **kwargs):
            kwargs.setdefault("raw_line_numbers", [seq])
            return lineage.event(Event(
                id=stable_id(sid, seq, suffix),
                session_id=sid,
                sequence=seq,
                kind=kind,
                name=name,
                start_time=ts,
                turn_id=turn,
                **kwargs,
            ), r, seq, suffix)

        def llm_until(ts):
            if active and anchor is not None and not pending and ts > anchor:
                return event(
                    "llm",
                    "LLM response (estimated)",
                    anchor,
                    "llm",
                    end_time=ts,
                    timing="estimated",
                    raw_line_numbers=[],
                    attributes={"basis": "gap between rollout items; includes orchestration"},
                )

        def input_event(text, payload, ts, outer):
            attribution = attribute_input(text, payload, session.metadata)
            origin = attribution["origin"]
            return event(
                "user" if origin == "human" else "event",
                {"human": "User input", "context": "Injected context", "internal": "Internal input"}[origin],
                ts,
                text=text,
                attributes={
                    "previous_turn_id": previous_turn,
                    "transport_role": payload.get("role") if outer == "response_item" else None,
                    "input_record_type": f"{outer}/{payload['type']}",
                    "input_attribution": attribution,
                },
            )

        for seq, line in enumerate(lines, 1):
            yield RawLine(seq, line)
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                p = r.get("payload", {})
                outer, kind = r.get("type"), p.get("type")
                ts = normalize_timestamp(r["timestamp"])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError(f"Invalid Codex record on line {seq}: {type(exc).__name__}") from exc
            if outer == "session_meta":
                new_sid = p.get("id") or p.get("session_id")
                if not isinstance(new_sid, str) or not new_sid or (sid and new_sid != sid):
                    raise ValueError("Expected one session per rollout with a valid session_meta id")
                sid = new_sid
                session = Session(
                    id=sid,
                    started_at=ts,
                    metadata=dict(p),
                )
                lineage.session(session, r, seq)
                yield session
                continue
            if sid is None:
                raise ValueError("The rollout must begin with session_meta")
            if outer == "turn_context":
                turn = p.get("turn_id", turn)
                lineage.context(p, seq)
                if p.get("model"):
                    session.metadata["model"] = p["model"]
                    session.field_lineage.update(copied(p["model"], "/metadata/model", seq, "/payload/model"))
                    yield session
            if outer == "event_msg" and kind == "item_completed":
                item = p.get("item", {})
                item_type = item.get("type", "")
                tool_types = ("CommandExecution", "McpToolCall", "FileChange", "DynamicToolCall")
                item_kind = (
                    "tool"
                    if item_type in tool_types
                    else "llm"
                    if item_type in ("Reasoning", "AgentMessage")
                    else "event"
                )
                if (
                    item_kind != "event"
                    and p.get("started_at_ms") is not None
                    and p.get("completed_at_ms") is not None
                ):
                    end = int(p["completed_at_ms"]) * 1000000
                    start = int(p["started_at_ms"]) * 1000000
                    duration = item.get("duration")
                    basis = (
                        "item lifetime; LLM output items measure streaming only, not full response latency"
                    )
                    if item_kind == "tool" and isinstance(duration, dict):
                        start = end - (
                            int(duration.get("secs", 0)) * 1000000000 + int(duration.get("nanos", 0))
                        )
                        basis = "Codex reported tool duration"
                    name = item.get("tool") or item_type
                    input_request = item_kind == "tool" and is_user_input_tool(name)
                    if input_request:
                        item_kind = "user_wait" if can_wait() else "event"
                        basis += "; blocking input request lifetime, including delivery overhead"
                    yield lineage.item(Event(
                        id=stable_id(sid, "item", item.get("id", seq)),
                        session_id=sid,
                        sequence=seq,
                        raw_line_numbers=[seq],
                        kind=item_kind,
                        name=name,
                        start_time=format_timestamp(start),
                        end_time=format_timestamp(end),
                        timing="measured",
                        source="codex_item",
                        turn_id=p.get("turn_id", turn),
                        text=command_text(item.get("command")),
                        attributes={
                            "basis": basis,
                            "item": item,
                            **({"wait_type": "input_request", "input_scope": "human" if can_wait() else "internal"}
                               if input_request else {}),
                        },
                        status="error"
                        if item.get("exit_code") not in (0, None) or item.get("status") == "failed"
                        else "ok",
                    ), r, seq)
            if outer == "event_msg" and kind == "task_started":
                turn = p.get("turn_id", turn)
                lineage.context(p, seq)
                active, anchor = True, ts
                lineage.anchor = ref(seq, "/timestamp", "interval_start")
            if (outer == "response_item" and kind == "message" and p.get("role") == "user") or (
                outer == "event_msg" and kind == "user_message"
            ):
                text = content_text(p.get("content")) if outer == "response_item" else p.get("message", "")
                prompt = input_event(text, p, ts, outer)
                origin = prompt.attributes["input_attribution"]["origin"]
                if origin == "human":
                    wait = wait_until(ts)
                    if wait:
                        yield wait
                    if session.title == "Untitled session":
                        session.title = text.strip().split("\n")[0][:100] or "Untitled session"
                        lineage.title(session, lineage.text(r, seq))
                        yield session
                elif origin == "internal":
                    waiting_since = None
                elif turn_origin is None:
                    turn_origin = "context"
                # Context does not begin work or move a pending human wait/LLM anchor.
                if origin != "context":
                    active, anchor, turn_origin = True, ts, origin
                    lineage.anchor = ref(seq, "/timestamp", "interval_start")
                # Track human and nonhuman mirrors separately so injected context
                # cannot displace a remembered prompt and manufacture a duplicate.
                if outer == "response_item":
                    fallback_user = fallback_inputs.pop(origin, None)
                    if fallback_user is not None and fallback_user.text != text:
                        yield fallback_user
                    seen_inputs[origin] = text
                    yield prompt
                elif text != seen_inputs.get(origin):
                    fallback_user = fallback_inputs.get(origin)
                    if fallback_user is not None:
                        yield fallback_user
                    fallback_inputs[origin] = prompt
            elif outer == "response_item" and kind in ("function_call", "custom_tool_call"):
                waiting_since = None
                span = llm_until(ts)
                if span:
                    yield span
                call = p.get("call_id") or p.get("id") or str(seq)
                name = p.get("name", "tool")
                input_request = is_user_input_tool(name)
                user_wait = input_request and can_wait()
                pending[call] = event(
                    "user_wait" if user_wait else "event" if input_request else "tool",
                    name,
                    ts,
                    call,
                    text=str(p.get("arguments", p.get("input", ""))),
                    attributes={"call_id": call, **(
                        {"wait_type": "input_request", "input_scope": "human" if user_wait else "internal"}
                        if input_request else {}
                    )},
                )
                anchor = None
            elif outer == "response_item" and kind in ("function_call_output", "custom_tool_call_output"):
                call = p.get("call_id")
                output = p.get("output", "")
                output = output if isinstance(output, str) else json.dumps(output)
                tool = pending.pop(call, None)
                if tool:
                    tool.raw_line_numbers.append(seq)
                    tool.end_time = max(ts, tool.start_time)
                    if ts < tool.start_time:
                        tool.attributes["end_time_clamped"] = True
                    tool.timing = "estimated"
                    tool.attributes["output"] = output
                    tool.attributes["basis"] = "call/output timestamps; may include scheduling and approvals"
                    if tool.kind == "user_wait":
                        tool.attributes["basis"] = (
                            "blocking input request to result; includes delivery overhead"
                        )
                    # Preserve call wall time separately; asynchronous exec may yield before completion.
                    m = re.search(r"Wall time:\s*([\d.]+)\s*seconds", output, re.I)
                    if m:
                        tool.attributes["reported_wall_time_ms"] = float(m[1]) * 1000
                    if re.search(
                        r"(?:exit(?:ed with)? code|Process exited with code)\s*[:=]?\s*[1-9]", output, re.I
                    ):
                        tool.status = "error"
                    lineage.complete(tool, seq)
                    yield tool
                else:
                    yield event("event", "Unmatched tool output", ts, text=output, status="incomplete")
                anchor = ts if active and not pending else None
                lineage.anchor = ref(seq, "/timestamp", "interval_start") if anchor is not None else None
            elif outer == "response_item" and kind in ("message", "reasoning"):
                if p.get("role") in ("developer", "system"):
                    continue
                waiting_since = None
                span = llm_until(ts)
                if span:
                    yield span
                anchor = ts if active else None
                lineage.anchor = ref(seq, "/timestamp", "interval_start") if anchor is not None else None
                text = content_text(p.get("content", p.get("summary", [])))
                yield event(
                    "assistant", "Reasoning summary" if kind == "reasoning" else "Assistant", ts, text=text
                )
                if p.get("phase") == "final_answer":
                    active, anchor = False, None
                    waiting_since = ts if can_wait() else None
                    lineage.waiting = ref(seq, "/timestamp", "interval_start") if waiting_since else None
            elif outer == "event_msg" and kind in ("task_complete", "turn_aborted"):
                yield from sorted(fallback_inputs.values(), key=lambda e: e.sequence)
                fallback_inputs.clear()
                previous_turn = p.get("turn_id", turn) if kind == "task_complete" else previous_turn
                if kind == "task_complete":
                    lineage.previous_turn = (spec("normalized", "Turn ID propagated from preceding task_complete.",
                                                  ref(seq, "/payload/turn_id", "context"))
                                             if "turn_id" in p else lineage.turn)
                seen_inputs.clear()
                active, anchor = False, None
                waiting_since = ts if kind == "task_complete" and can_wait() else None
                turn_origin = None
                lineage.input_sources = []
                lineage.waiting = ref(seq, "/timestamp", "interval_start") if waiting_since else None
                yield event("event", kind, ts, status="ok" if kind == "task_complete" else "interrupted")
            elif outer in ("compacted",) or (outer == "event_msg" and kind == "thread_rolled_back"):
                yield event("event", outer if outer == "compacted" else kind, ts, attributes=p)
                active, anchor = False, None
                waiting_since = None
            elif outer == "event_msg" and kind == "token_count":
                yield event("event", "Token usage", ts, attributes={"info": p.get("info")})
        if sid is None:
            raise ValueError("No session_meta found")
        yield from sorted(fallback_inputs.values(), key=lambda e: e.sequence)
        for tool in pending.values():
            tool.status = "incomplete"
            lineage.incomplete(tool)
            yield tool
        yield RawTraceEnd(sid, self.mapping_version)

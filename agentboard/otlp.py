"""OTLP/HTTP traces and logs, JSON or binary protobuf. Metrics are intentionally outside v1."""

import base64
import copy
import json
import re

from google.protobuf.json_format import MessageToDict, ParseDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from .domain import Event, Session, is_user_input_tool, stable_id
from .timestamps import format_timestamp


def convert_ids(value, to_proto):
    if isinstance(value, list):
        for item in value:
            convert_ids(item, to_proto)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ("traceId", "spanId", "parentSpanId") and item:
                if to_proto:
                    length = 32 if key == "traceId" else 16
                    if not isinstance(item, str) or not re.fullmatch(r"[a-fA-F0-9]{%d}" % length, item):
                        raise ValueError(f"Invalid {key}")
                    value[key] = base64.b64encode(bytes.fromhex(item)).decode()
                else:
                    value[key] = base64.b64decode(item).hex()
            else:
                convert_ids(item, to_proto)


def decode(body, signal, binary):
    message = ExportTraceServiceRequest() if signal == "traces" else ExportLogsServiceRequest()
    if binary:
        message.ParseFromString(body)
    else:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected an OTLP object")
        convert_ids(data, True)
        ParseDict(data, message, ignore_unknown_fields=True)
    data = MessageToDict(message)
    convert_ids(data, False)
    return data


def any_value(v):
    if "arrayValue" in v:
        return [any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attributes(v["kvlistValue"].get("values", []))
    if "intValue" in v:
        return int(v["intValue"])
    return next(iter(v.values()), None)


def attributes(items):
    return {p["key"]: any_value(p.get("value", {})) for p in items}


def session_identity(resource, scope, record):
    """Keep worker identity separate from conversation evidence in decoded OTLP."""
    attrs = {**attributes(resource.get("attributes", [])),
             **attributes(scope.get("attributes", [])),
             **attributes(record.get("attributes", []))}

    def identity(values):
        for key in ("session.id", "conversation.id", "gen_ai.conversation.id"):
            if values.get(key):
                return str(values[key]), key
        for key in ("thread.id", "thread_id"):
            thread = values.get(key)
            if isinstance(thread, str) and re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", thread
            ):
                return thread, f"{key} (UUID)"
        return None, None

    direct, basis = identity(attrs)
    nested = {sid for event in record.get("events", [])
              if (sid := identity(attributes(event.get("attributes", [])))[0])}
    candidates = nested | ({direct} if direct else set())
    if not direct and len(nested) == 1:
        direct, basis = next(iter(nested)), "span event conversation identity"
    return direct, {"direct_session_id": direct, "basis": basis, "candidates": sorted(candidates)}


def normalize(data, signal):
    resource_key, scope_key, record_key = (
        ("resourceSpans", "scopeSpans", "spans")
        if signal == "traces"
        else ("resourceLogs", "scopeLogs", "logRecords")
    )
    for resource in data.get(resource_key, []):
        resource_attrs = attributes(resource.get("resource", {}).get("attributes", []))
        for scope in resource.get(scope_key, []):
            for sequence, record in enumerate(scope.get(record_key, [])):
                attrs = {**resource_attrs, **attributes(record.get("attributes", []))}
                trace_id = record.get("traceId") or None
                span_id = record.get("spanId") or None
                for value, length in ((trace_id, 32), (span_id, 16)):
                    if value and (len(value) != length or int(value, 16) == 0):
                        raise ValueError("Invalid OTLP trace/span id")
                direct_sid, identity = session_identity(
                    resource.get("resource", {}), scope.get("scope", {}), record
                )
                sid = direct_sid or trace_id or "unattributed-" + stable_id(resource_attrs)
                source = "otlp_trace" if signal == "traces" else "otlp_log"
                ts = int(
                    record.get(
                        "startTimeUnixNano", record.get("timeUnixNano", record.get("observedTimeUnixNano", 0))
                    )
                )
                if ts <= 0:
                    raise ValueError("OTLP record needs a positive timestamp")
                body = any_value(record.get("body", {}))
                name = (
                    record.get("name")
                    or record.get("eventName")
                    or attrs.get("event.name")
                    or (body if isinstance(body, str) else "Log event")
                )
                name = str(name)
                kind, timing, end = "event", "unknown", None
                if signal == "traces":
                    if not trace_id or not span_id:
                        raise ValueError("OTLP spans require traceId and spanId")
                    end = int(record.get("endTimeUnixNano", 0))
                    if end < ts:
                        raise ValueError("Span end precedes start")
                    timing = "measured"
                    op = str(attrs.get("gen_ai.operation.name", ""))
                    if attrs.get("gen_ai.tool.name") or op == "execute_tool" or "tool" in name.lower():
                        kind = "tool"
                    elif op in ("chat", "text_completion", "generate_content") or name in (
                        "codex.api_request",
                        "codex.websocket_request",
                    ):
                        kind = "llm"
                else:
                    if name == "codex.tool_result":
                        kind = "tool"
                    elif name in ("codex.api_request", "codex.websocket_request"):
                        kind = "llm"
                    elif name == "codex.user_prompt":
                        kind = "user"
                    duration = attrs.get("duration_ms", attrs.get("duration"))
                    if duration is not None and kind in ("tool", "llm"):
                        duration = float(duration)
                        if duration < 0:
                            raise ValueError("Negative duration")
                        end, ts = ts, ts - int(duration * 1e6)
                        timing = "measured"
                raw_attrs = copy.deepcopy(attrs)
                raw_attrs["agentboard_session_identity"] = identity
                tool_name = str(attrs.get("gen_ai.tool.name") or attrs.get("tool_name") or name)
                if (kind == "tool" or signal == "traces") and is_user_input_tool(tool_name):
                    kind = "user_wait"
                    raw_attrs["wait_type"] = "input_request"
                    raw_attrs["basis"] = "blocking input request lifetime; includes delivery overhead"
                raw_attrs["otel"] = {
                    "record": record,
                    "resource": resource.get("resource", {}),
                    "scope": scope.get("scope", {}),
                }
                status = (
                    "error"
                    if (
                        record.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR")
                        or str(attrs.get("success", True)).lower() == "false"
                        or int(record.get("severityNumber", 0)) >= 17
                    )
                    else "ok"
                )
                yield Session(
                    id=sid,
                    identity_kind="session" if direct_sid else
                    "unattributed_trace" if trace_id else "unattributed_resource",
                    agent="codex"
                    if "codex" in str(attrs.get("service.name", "")) or name.startswith("codex.")
                    else "otel",
                    started_at=format_timestamp(ts),
                    metadata={"service.name": attrs.get("service.name", "unknown")},
                )
                yield Event(
                    id=stable_id(source, trace_id, span_id)
                    if signal == "traces"
                    else stable_id(source, sid, record),
                    session_id=sid,
                    sequence=sequence,
                    kind=kind,
                    name=name,
                    start_time=format_timestamp(ts),
                    end_time=format_timestamp(end) if end is not None else None,
                    timing=timing,
                    source=source,
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=record.get("parentSpanId"),
                    turn_id=attrs.get("turn.id"),
                    status=status,
                    text=str(attrs.get("prompt", ""))
                    if kind == "user"
                    else (body if isinstance(body, str) else ""),
                    attributes=raw_attrs,
                )

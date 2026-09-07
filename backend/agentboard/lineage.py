"""Field evidence uses JSON Pointers; import-only descriptors never alter normalized exports."""

from copy import deepcopy


def same_value(left, right):
    # JSON booleans are not numbers. Python's True == 1 must not retarget evidence.
    return type(left) is type(right) and left == right


def escape(key):
    return str(key).replace("~", "~0").replace("/", "~1")


def leaves(value, path=""):
    """Include nulls and empty containers, which are values rather than missing evidence."""
    if isinstance(value, (dict, list)) and value:
        entries = value.items() if isinstance(value, dict) else enumerate(value)
        return {p: v for key, child in entries for p, v in leaves(child, path + "/" + escape(key)).items()}
    return {path: value}


def resolve(value, pointer):
    try:
        for part in pointer.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return True, value
    except (KeyError, IndexError, ValueError, TypeError):
        return False, None


def spec(origin, method, *sources):
    return {"origin": origin, "method": method, "sources": [s for s in sources if s is not None]}


def ref(line, pointer, role="value"):
    return {"line_number": line, "pointer": pointer, "role": role}


def copied(value, target, line, pointer):
    return {path: spec("normalized", "Copied from the source payload.", ref(line, pointer + path[len(target):]))
            for path in leaves(value, target)}


def defaults(value):
    return {path: spec("inferred", "Adapter default: " + repr(child) + ".")
            for path, child in leaves(value).items()}


def expanded(value, descriptions):
    """Expand a transformation on a container to its leaves unless a leaf is more specific."""
    result = {}
    for path, child in leaves(value).items():
        parent = path
        while parent not in descriptions and parent:
            parent = parent.rsplit("/", 1)[0]
        description = descriptions.get(parent)
        if description is not None:
            result[path] = {**deepcopy(description), "value": child}
    return result


class CodexLineage:
    """Follow the adapter's state, including source records that emit no event."""

    def __init__(self):
        self.session_id = spec("unknown", "Session metadata has not been read.")
        self.turn = spec("inferred", "No turn ID has been set; null default.")
        self.previous_turn = spec("inferred", "No completed turn has been observed; null default.")
        self.anchor = None
        self.waiting = None

    def session(self, session, record, line):
        key = "id" if record["payload"].get("id") else "session_id"
        self.session_id = spec("normalized", "Session ID selected from session_meta.", ref(line, f"/payload/{key}"))
        session.field_lineage = {
            **defaults(session.model_dump()),
            **copied(session.metadata, "/metadata", line, "/payload"),
            "/id": self.session_id,
            "/started_at": spec("normalized", "Outer timestamp normalized to UTC RFC 3339.", ref(line, "/timestamp")),
        }

    def context(self, payload, line):
        if "turn_id" in payload:
            self.turn = spec("normalized", "Turn ID propagated until a later context or task start replaces it.",
                             ref(line, "/payload/turn_id", "context"))

    def title(self, session, text_spec):
        session.field_lineage["/title"] = spec(
            "calculated", "Strip prompt, select first line, truncate to 100 characters; blank uses Untitled session.",
            *text_spec["sources"],
        )

    @staticmethod
    def text(record, line):
        p = record["payload"]
        if record["type"] == "event_msg":
            return spec("normalized" if "message" in p else "inferred", "Read message; absent key defaults to empty text.",
                        ref(line, "/payload/message"))
        key = "content" if "content" in p or p.get("role") == "user" else "summary"
        return spec("normalized" if key in p else "inferred",
                    "Keep a string or concatenate dictionary text parts without a separator; ignore other parts; missing is empty.",
                    ref(line, f"/payload/{key}"))

    def event(self, event, record, line, suffix):
        p = record["payload"]
        outer, kind = record["type"], p.get("type")
        discriminator = [ref(line, "/type", "classification"), ref(line, "/payload/type", "classification")]
        d = defaults(event.model_dump())
        d.update({
            "/id": spec("calculated", f"First 32 hex characters of SHA-256 of JSON [session_id, physical line, {suffix!r}].",
                        *self.session_id["sources"], ref(line, "", "physical_line")),
            "/session_id": self.session_id,
            "/sequence": spec("calculated", "One-based physical JSONL line number, including blank lines.",
                              ref(line, "", "physical_line")),
            "/turn_id": self.turn,
            "/start_time": spec("normalized", "Outer timestamp normalized to UTC RFC 3339.", ref(line, "/timestamp")),
            "/kind": spec("inferred", "Category assigned by the Codex record mapping.", *discriminator),
            "/name": spec("inferred", "Display label assigned by the Codex record mapping.", *discriminator),
        })
        inferred = event.kind == "llm" or event.attributes.get("wait_type") == "between_turns"
        if inferred:
            start = self.anchor if event.kind == "llm" else self.waiting
            bounds = [start, ref(line, "/timestamp", "interval_end")]
            d["/start_time"] = spec("normalized", "Normalize source timestamp; selecting this interval boundary is inferred.", start)
            d["/end_time"] = spec("normalized", "Normalize triggering timestamp; selecting this interval boundary is inferred.", bounds[1])
            for field in ("/kind", "/name", "/timing", "/attributes/basis", "/attributes/wait_type"):
                if resolve(event.model_dump(), field)[0]:
                    d[field] = spec("inferred", event.attributes["basis"] + "; requires a positive gap and adapter state.", *bounds)
        elif outer == "response_item" and kind in ("function_call", "custom_tool_call"):
            call_key = "call_id" if p.get("call_id") else "id" if p.get("id") else None
            call_spec = spec("normalized" if call_key else "calculated",
                             "First truthy call_id or id; otherwise physical line number converted to a string.",
                             ref(line, f"/payload/{call_key}") if call_key else ref(line, "", "physical_line"))
            d["/attributes/call_id"] = call_spec
            d["/id"]["sources"] += call_spec["sources"]
            d["/name"] = spec("normalized" if "name" in p else "inferred", "Read name; absent key defaults to tool.",
                              ref(line, "/payload/name"))
            d["/kind"] = spec("inferred", "Blocking request_user_input tools are user_wait; other calls are tool.",
                              *discriminator, ref(line, "/payload/name"))
            key = "arguments" if "arguments" in p else "input"
            d["/text"] = spec("normalized" if key in p else "inferred", "Python str of arguments, otherwise input; absent defaults to empty.",
                              ref(line, f"/payload/{key}"))
            if event.kind == "user_wait":
                d["/attributes/wait_type"] = d["/kind"]
        elif event.kind in ("user", "assistant"):
            d["/text"] = self.text(record, line)
            d["/kind"]["sources"].append(ref(line, "/payload/role", "classification"))
            if event.kind == "user":
                d["/attributes/previous_turn_id"] = self.previous_turn
        elif event.name == "Unmatched tool output":
            d["/text"] = spec("normalized", "Keep string output; serialize other values with json.dumps; missing defaults to empty.",
                              ref(line, "/payload/output"))
            d["/status"] = spec("inferred", "No pending call matches this output call_id.", ref(line, "/payload/call_id"))
        elif event.name in ("task_complete", "turn_aborted", "thread_rolled_back", "compacted"):
            d["/name"] = spec("normalized", "Lifecycle discriminator selected as name.",
                              ref(line, "/type" if outer == "compacted" else "/payload/type"))
            d["/status"] = spec("inferred", "turn_aborted maps to interrupted; other lifecycle records default to ok.", *discriminator)
            if event.name in ("compacted", "thread_rolled_back"):
                d.update(copied(event.attributes, "/attributes", line, "/payload"))
        elif event.name == "Token usage":
            if "info" in p:
                d.update(copied(event.attributes["info"], "/attributes/info", line, "/payload/info"))
            else:
                d["/attributes/info"] = spec("inferred", "Missing token info defaults to null.", ref(line, "/payload/info"))
        event.field_lineage = d
        return event

    def item(self, event, record, line):
        self.event(event, record, line, "item")
        p = record["payload"]
        item = p["item"]
        d = event.field_lineage
        d.update(copied(item, "/attributes/item", line, "/payload/item"))
        d["/id"] = spec("calculated", "First 32 hex characters of SHA-256 of JSON [session_id, 'item', item.id or physical line when absent].",
                        *self.session_id["sources"], ref(line, "/payload/item/id"), ref(line, "", "physical_line"))
        name_key = "tool" if item.get("tool") else "type"
        d["/name"] = spec("normalized" if name_key in item else "inferred", "First truthy item.tool, otherwise item.type (empty default).",
                          ref(line, f"/payload/item/{name_key}"))
        d["/kind"] = spec("inferred", "Map item.type to tool or LLM; blocking request_user_input maps to user_wait.",
                          ref(line, "/payload/item/type"), ref(line, "/payload/item/tool"))
        d["/timing"] = spec("inferred", "Recorded item timestamps/duration map to measured timing; LLM items measure streaming only.",
                            ref(line, "/payload/started_at_ms"), ref(line, "/payload/completed_at_ms"))
        d["/start_time"] = spec("normalized", "int(started_at_ms) epoch milliseconds converted to UTC RFC 3339.", ref(line, "/payload/started_at_ms"))
        d["/end_time"] = spec("normalized", "int(completed_at_ms) epoch milliseconds converted to UTC RFC 3339.", ref(line, "/payload/completed_at_ms"))
        if event.attributes["basis"].startswith("Codex reported tool duration"):
            d["/start_time"] = spec("calculated", "completed_at_ms * 1,000,000 - (int(secs) * 1,000,000,000 + int(nanos)); absent duration components default to zero; convert nanoseconds to RFC 3339.",
                                   ref(line, "/payload/completed_at_ms"), ref(line, "/payload/item/duration/secs"), ref(line, "/payload/item/duration/nanos"))
        d["/attributes/basis"] = spec("inferred", event.attributes["basis"], *d["/start_time"]["sources"], ref(line, "/payload/item/type"))
        if event.kind == "user_wait":
            d["/attributes/wait_type"] = d["/kind"]
        if "turn_id" in p:
            d["/turn_id"] = spec("normalized", "Explicit completed-item turn ID overrides propagated context.", ref(line, "/payload/turn_id"))
        d["/text"] = spec("normalized" if "command" in item else "inferred", "Null/missing command becomes empty; keep strings; shlex.join string arrays; JSON-serialize other values.", ref(line, "/payload/item/command"))
        d["/status"] = spec("inferred", "Error when exit_code is neither 0 nor null, or status is failed; otherwise ok.", ref(line, "/payload/item/exit_code"), ref(line, "/payload/item/status"))
        return event

    @staticmethod
    def complete(tool, line):
        d = tool.field_lineage
        result = ref(line, "/timestamp", "result")
        output = ref(line, "/payload/output")
        d["/end_time"] = spec("calculated", "max(result timestamp, call start), normalized to UTC RFC 3339.", *d["/start_time"]["sources"], result)
        pairing = [*d["/attributes/call_id"]["sources"], ref(line, "/payload/call_id", "pairing")]
        d["/timing"] = spec("inferred", "Call/result pairing estimates operation lifetime.", *d["/start_time"]["sources"], result)
        d["/attributes/output"] = spec("normalized", "Pair by call_id; keep a string; serialize other outputs with json.dumps; missing defaults to empty.", output, *pairing)
        d["/attributes/basis"] = spec("inferred", tool.attributes["basis"], *d["/timing"]["sources"])
        d["/status"] = spec("inferred", "Error if output matches a nonzero exit-code pattern; otherwise ok. Not proof of success.", output)
        if "end_time_clamped" in tool.attributes:
            d["/attributes/end_time_clamped"] = spec("calculated", "Result timestamp precedes call start.", *d["/end_time"]["sources"])
        if "reported_wall_time_ms" in tool.attributes:
            d["/attributes/reported_wall_time_ms"] = spec("calculated", "Parse Wall time seconds from output with float and multiply by 1,000.", output)

    @staticmethod
    def incomplete(tool):
        tool.field_lineage["/status"] = spec("inferred", "Call remains pending at the end of this archived snapshot; no paired output was observed.",
                                             *tool.field_lineage["/attributes/call_id"]["sources"])

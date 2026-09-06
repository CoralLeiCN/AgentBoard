"""Native Codex branching is explicit CLI work, never a subprocess exposed by the HTTP server."""

import json
import selectors
import subprocess
import time


def make_plan(store, sid, input_id, replacement):
    session = store.get_session(sid)
    selected = store.event(sid, input_id)
    if session["agent"] != "codex" or selected["kind"] != "user" or selected["source"] != "codex_jsonl":
        raise ValueError("Native resume requires an imported Codex user input")
    earlier_inputs = [
        e
        for e in store.ordered_events(sid, selected["sequence"])
        if e["kind"] == "user" and e["source"] == "codex_jsonl"
    ]
    if selected["turn_id"] and any(e["turn_id"] == selected["turn_id"] for e in earlier_inputs):
        raise ValueError(
            "This input steers an existing turn; use conversation replay for an exact input boundary"
        )
    previous = selected["attributes"].get("previous_turn_id")
    if earlier_inputs and not previous:
        raise ValueError("This rollout lacks turn boundaries; use conversation replay instead")
    if previous:
        branch = {
            "method": "thread/fork",
            "params": {
                "threadId": sid,
                "lastTurnId": previous,
                "sandbox": "read-only",
                "approvalPolicy": "never",
            },
        }
    else:
        # There is no prior turn to retain when replacing the first input.
        branch = {"method": "thread/start", "params": {"sandbox": "read-only", "approvalPolicy": "never"}}
    return {
        "source_session_id": sid,
        "input_id": input_id,
        "branch": branch,
        "turn": {
            "method": "turn/start",
            "params": {
                "threadId": "$BRANCH_ID",
                "input": [{"type": "text", "text": replacement, "text_elements": []}],
            },
        },
        "note": "Run with agentboard resume. Source history is preserved. Files are not rewound. A first-input replacement starts a fresh thread. Native Codex must have the source session locally.",
    }


class CodexRPC:
    def __init__(self, executable="codex", timeout=120):
        self.executable, self.timeout = executable, timeout
        self.counter, self.buffer, self.notifications = 0, b"", []

    def __enter__(self):
        self.process = subprocess.Popen(
            [self.executable, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.call("initialize", {"clientInfo": {"name": "agentboard", "version": "0.1.0"}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        self.selector.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())

    def receive(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("Timed out waiting for Codex app-server")
            chunk = self.process.stdout.read(65536)
            if not chunk:
                raise RuntimeError("Codex app-server closed the connection")
            self.buffer += chunk
            if len(self.buffer) > 32 * 1024 * 1024:
                raise RuntimeError("Codex response exceeds 32 MiB")
        line, self.buffer = self.buffer.split(b"\n", 1)
        message = json.loads(line)
        if "method" in message and "id" in message:
            # This client never grants approval or fabricates user responses.
            self.send(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Interactive requests unsupported"},
                }
            )
        return message

    def call(self, method, params):
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.receive(deadline)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"Codex {method}: {message['error']}")
                return message["result"]
            if message.get("method") == "turn/completed":
                self.notifications.append(message)

    def wait_turn(self, thread_id, turn_id):
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.notifications.pop(0) if self.notifications else self.receive(deadline)
            p = message.get("params", {})
            if (
                message.get("method") == "turn/completed"
                and p.get("threadId") == thread_id
                and p.get("turn", {}).get("id") == turn_id
            ):
                return p["turn"]


def execute_plan(plan, cwd, rpc_factory=CodexRPC):
    with rpc_factory() as rpc:
        branch = plan["branch"]
        result = rpc.call(branch["method"], {**branch["params"], "cwd": str(cwd)})
        sid = result["thread"]["id"]
        turn = rpc.call("turn/start", {**plan["turn"]["params"], "threadId": sid})
        try:
            completed = rpc.wait_turn(sid, turn["turn"]["id"])
        except (TimeoutError, KeyboardInterrupt):
            rpc.call("turn/interrupt", {"threadId": sid, "turnId": turn["turn"]["id"]})
            raise
        return {"thread_id": sid, "turn": completed}

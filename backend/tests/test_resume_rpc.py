import sys

from agentboard.resume import CodexRPC, execute_plan


def test_stdio_protocol_handles_completion_before_turn_response(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import sys,json
for line in sys.stdin:
    r=json.loads(line)
    if "id" not in r: continue
    method=r["method"]
    if method=="initialize": result={"userAgent":"test"}
    elif method=="thread/fork": result={"thread":{"id":"branch"}}
    elif method=="turn/start":
        print(json.dumps({"method":"turn/completed","params":{"threadId":"branch","turn":{"id":"turn","status":"completed"}}}),flush=True)
        result={"turn":{"id":"turn"}}
    else: result={}
    print(json.dumps({"id":r["id"],"result":result}),flush=True)
"""
    )
    executable.chmod(0o700)
    plan = {
        "branch": {"method": "thread/fork", "params": {"threadId": "source", "lastTurnId": "previous"}},
        "turn": {"params": {"threadId": "$BRANCH_ID", "input": [{"type": "text", "text": "replacement"}]}},
    }
    result = execute_plan(plan, tmp_path, lambda: CodexRPC(str(executable), timeout=2))
    assert result == {"thread_id": "branch", "turn": {"id": "turn", "status": "completed"}}

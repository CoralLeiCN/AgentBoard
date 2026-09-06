"""Compare LLM and tool timing without adding duplicate sources or overlapping tools."""

from common import demo_session, request

sid = demo_session()
stats = request("GET", f"/api/v1/sessions/{sid}/stats").json()
for group in stats["timing"]:
    print(
        f"{group['source']:14} {group['kind']:5} {group['timing']:9} "
        f"sum={group['sum_ms']} ms, active={group['active_ms']} ms, spans={group['count']}"
    )
print(stats["note"])

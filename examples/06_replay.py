"""Replace the second user input and continue the preceding conversation in a new branch."""

from common import demo_session, events, request

sid = demo_session()
inputs = list(events(sid, "inputs"))
result = request(
    "POST",
    f"/api/v1/sessions/{sid}/replay",
    json={
        "input_id": inputs[1]["id"],
        "replacement": "Explain the fix as a short technical note instead of making more changes.",
    },
).json()
print(result)
print("Original input is unchanged:", list(events(sid, "inputs"))[1]["text"])

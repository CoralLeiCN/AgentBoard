"""Filter/extract user prompts; every page is consumed."""

from common import demo_session, events

sid = demo_session()
for event in events(sid, "inputs", q="test"):
    print(f"{event['id']}\n{event['text']}\n")

"""Use the configured local model through OpenAI, or the labeled dummy fallback.

An independent coding agent can also GET /export and PUT /classification with
{category, reason, model}; it need not run inside AgentBoard.
"""

from common import demo_session, request

sid = demo_session()
print(request("POST", f"/api/v1/sessions/{sid}/classify").json())

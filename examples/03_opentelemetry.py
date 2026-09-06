"""Send real OTLP/protobuf using the Python SDK's background batch exporter."""

import time

from common import BASE, HEADERS, request
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider(resource=Resource.create({"service.name": "codex-demo", "session.id": "otel-demo"}))
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=BASE + "/v1/traces", headers=HEADERS))
)
tracer = provider.get_tracer("agentboard.examples")
with tracer.start_as_current_span("agent.turn"):
    with tracer.start_as_current_span(
        "Model response", attributes={"gen_ai.operation.name": "chat", "demo": True}
    ):
        time.sleep(0.08)
    with tracer.start_as_current_span(
        "read_file", attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "read_file"}
    ):
        time.sleep(0.03)
provider.shutdown()
print(request("GET", "/api/v1/sessions/otel-demo/stats").json())

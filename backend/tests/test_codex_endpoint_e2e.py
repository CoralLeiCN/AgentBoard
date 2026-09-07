import json
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from agentboard.api import create_app
from agentboard.config import Settings
from scripts.private_endpoint import PRIVATE_BASE_URL, validate_base_url

BASE_URL_ENV = "AGENTBOARD_E2E_CODEX_BASE_URL"
MODEL_ENV = "AGENTBOARD_E2E_CODEX_MODEL"
API_KEY_ENV = "AGENTBOARD_E2E_CODEX_API_KEY"
PROMPT = "Reply with exactly AGENTBOARD_E2E_OK and do not use tools."
EXAMPLE_ENV = Path(__file__).parents[2] / "example.env"


def _config(key, value):
    return ["-c", f"{key}={json.dumps(value)}"]


def codex_environment(codex_home):
    # Keep runtime essentials, not desktop app pipes, session IDs, provider auth, or proxy settings.
    environment = {name: os.environ[name] for name in (
        "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SYSTEMROOT", API_KEY_ENV
    ) if name in os.environ}
    environment["CODEX_HOME"] = str(codex_home)
    # The desktop parent may use RUST_LOG=warn, suppressing Codex's INFO OTel events.
    environment["RUST_LOG"] = "info"
    return environment


def codex_command(executable, base_url, model, receiver_url, output_path, api_key_configured):
    base_url = validate_base_url(base_url)
    command = [
        executable,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
        "--output-last-message",
        str(output_path),
    ]
    overrides = {
        "approval_policy": "never",
        "model": model,
        "model_provider": "agentboard_e2e",
        "model_providers.agentboard_e2e.name": "AgentBoard E2E endpoint",
        "model_providers.agentboard_e2e.base_url": base_url.rstrip("/"),
        "model_providers.agentboard_e2e.wire_api": "responses",
        "model_providers.agentboard_e2e.requires_openai_auth": False,
        "model_providers.agentboard_e2e.request_max_retries": 0,
        "model_providers.agentboard_e2e.stream_max_retries": 0,
        "shell_environment_policy.inherit": "none",
        "otel.environment": "agentboard-e2e",
        "otel.log_user_prompt": False,
        "otel.exporter.otlp-http.endpoint": f"{receiver_url}/v1/logs",
        "otel.exporter.otlp-http.protocol": "binary",
        "otel.trace_exporter.otlp-http.endpoint": f"{receiver_url}/v1/traces",
        "otel.trace_exporter.otlp-http.protocol": "binary",
    }
    if api_key_configured:
        overrides["model_providers.agentboard_e2e.env_key"] = API_KEY_ENV
    for key, value in overrides.items():
        command.extend(_config(key, value))
    return [*command, PROMPT]


def test_codex_e2e_command_uses_responses_provider_without_exposing_key(tmp_path):
    command = codex_command(
        "codex",
        PRIVATE_BASE_URL + "/",
        "test-model",
        "http://127.0.0.1:43210",
        tmp_path / "last-message.txt",
        api_key_configured=True,
    )
    joined = " ".join(command)
    assert 'model_provider="agentboard_e2e"' in joined
    assert 'wire_api="responses"' in joined
    assert f'base_url="{PRIVATE_BASE_URL}"' in joined
    assert f'env_key="{API_KEY_ENV}"' in joined
    assert "requires_openai_auth=false" in joined
    assert 'shell_environment_policy.inherit="none"' in joined
    assert "AGENTBOARD_E2E_OK" in command[-1]


def test_example_env_documents_codex_endpoint_settings():
    values = dict(
        line.split("=", 1)
        for line in EXAMPLE_ENV.read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert {BASE_URL_ENV, MODEL_ENV, API_KEY_ENV} <= set(values)
    assert values[BASE_URL_ENV] == PRIVATE_BASE_URL
    assert values[MODEL_ENV] == ""
    assert values[API_KEY_ENV] == ""


def test_codex_child_enables_telemetry_without_changing_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("RUST_LOG", "warn")
    monkeypatch.setenv("CODEX_APP_TOOLS_PIPE_PATH", "/parent-app-pipe")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-key")
    child = codex_environment(tmp_path / "isolated-codex")
    assert child["RUST_LOG"] == "info"
    assert child["CODEX_HOME"] == str(tmp_path / "isolated-codex")
    assert os.environ["RUST_LOG"] == "warn"
    assert "CODEX_APP_TOOLS_PIPE_PATH" not in child
    assert "OPENAI_API_KEY" not in child


@pytest.fixture
def codex_endpoint(private_endpoint):
    endpoint = private_endpoint("AGENTBOARD_E2E_CODEX")
    executable = shutil.which("codex")
    if not executable:
        pytest.fail("real endpoint test requires the codex executable on PATH")
    return executable, endpoint.base_url, endpoint.model


@pytest.fixture
def live_receiver(tmp_path, codex_endpoint):
    database = tmp_path / "codex-endpoint-e2e.db"
    app = create_app(
        Settings(
            database=str(database),
            environment="test",
            features=set(),
            model_mode="dummy",
            otlp_enabled=True,
        )
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        pytest.fail("temporary AgentBoard OTLP receiver did not start")
    try:
        yield f"http://127.0.0.1:{port}", database
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            pytest.fail("temporary AgentBoard OTLP receiver did not stop")


def _event_counts(database):
    with sqlite3.connect(database) as connection:
        return {
            (source, kind): count
            for source, kind, count in connection.execute(
                "SELECT source, kind, count(*) FROM events GROUP BY source, kind"
            )
        }


@pytest.mark.e2e
def test_real_responses_endpoint_produces_codex_telemetry(
    tmp_path, codex_endpoint, live_receiver
):
    executable, base_url, model = codex_endpoint
    receiver_url, database = live_receiver
    output_path = tmp_path / "last-message.txt"
    command = codex_command(
        executable,
        base_url,
        model,
        receiver_url,
        output_path,
        api_key_configured=bool(os.getenv(API_KEY_ENV)),
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=codex_environment(codex_home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Retain diagnostics locally even when model generation succeeds but telemetry is missing.
    (tmp_path / "codex.stdout.log").write_text(completed.stdout)
    (tmp_path / "codex.stderr.log").write_text(completed.stderr)
    assert completed.returncode == 0, (
        f"codex failed with exit {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert output_path.is_file(), f"codex did not write {output_path.name}"
    assert "AGENTBOARD_E2E_OK" in output_path.read_text()

    deadline = time.monotonic() + 15
    counts = {}
    while time.monotonic() < deadline:
        counts = _event_counts(database)
        sources = {source for source, _kind in counts}
        if {"otlp_log", "otlp_trace"} <= sources and any(kind == "llm" for _source, kind in counts):
            break
        time.sleep(0.1)
    sources = {source for source, _kind in counts}
    assert {"otlp_log", "otlp_trace"} <= sources, f"missing Codex telemetry sources; received {counts}"
    assert any(kind == "llm" for _source, kind in counts), f"missing normalized LLM event; received {counts}"

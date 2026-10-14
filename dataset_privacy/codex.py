"""Direct Codex CLI adapter with isolated configuration and private evidence."""

import ipaddress
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import (
    FINDING_SCHEMA,
    Chunk,
    InvalidFindings,
    PrivacyError,
    Scan,
    load_json,
    resolve_result,
)
from .prompt_loader import BATCH_INSTRUCTIONS, INSTRUCTIONS

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
SUPPORTED_CODEX_VERSION = "codex-cli 0.160.0"
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_REASONING_EFFORT = "high"
DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "code_mode",
    "code_mode_host",
    "multi_agent",
    "multi_agent_v2",
    "apps",
    "plugins",
    "hooks",
    "memories",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "view_image",
    "sleep_tool",
    "tool_suggest",
    "workspace_dependencies",
    "remote_plugin",
    "system_proxy_fallback",
)


def write_private(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        stream.write(content)


def write_json(path: Path, value: object) -> None:
    write_private(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


@dataclass(frozen=True)
class CodexConfig:
    model: str | None = None
    base_url: str | None = None
    executable: str = "codex"
    timeout: float = 120
    api_key_env: str | None = None
    reasoning_effort: str = DEFAULT_REASONING_EFFORT

    def __post_init__(self):
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise PrivacyError("Invalid model override")
        if not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip():
            raise PrivacyError("Invalid reasoning effort")
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise PrivacyError("Timeout must be positive and finite")
        if self.base_url is None:
            if self.api_key_env is not None:
                raise PrivacyError("Default Codex mode uses its existing authentication")
            if self.model is None:
                object.__setattr__(self, "model", DEFAULT_MODEL)
            return
        if self.model is None:
            raise PrivacyError("An explicit model ID is required for a private endpoint")
        try:
            url = urlsplit(self.base_url)
            address = ipaddress.ip_address(url.hostname or "")
            private = address.is_private or address.is_loopback
            port = url.port
        except ValueError as exc:
            raise PrivacyError("Endpoint must use an explicit private or loopback IP address") from exc
        if not private or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise PrivacyError("Only private or loopback model endpoints are supported")
        if url.scheme not in ("http", "https") or url.username or url.password or url.query or url.fragment:
            raise PrivacyError("Invalid private endpoint URL")
        if url.path.rstrip("/") != "/v1" or port == 0:
            raise PrivacyError("Endpoint must end in /v1")
        if self.api_key_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise PrivacyError("Invalid API-key environment variable name")


def child_environment(home: Path, config: CodexConfig) -> dict[str, str]:
    environment = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    if config.base_url is not None:
        environment["CODEX_HOME"] = str(home)
    else:
        # Codex reads/refreshes its own saved login. Never copy or archive credential files.
        for key in (
            "CODEX_HOME",
            "CODEX_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        ):
            if key in os.environ:
                environment[key] = os.environ[key]
    environment["RUST_LOG"] = "warn"
    if config.api_key_env:
        key = os.environ.get(config.api_key_env)
        if not key:
            raise PrivacyError("Configured API-key environment variable is empty")
        environment["DATASET_PRIVACY_API_KEY"] = key
    return environment


def build_command(executable: str, config: CodexConfig, attempt: Path, instructions: Path) -> list[str]:
    command = [
        executable,
        "exec",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
        "--output-schema",
        str(attempt / "schema.json"),
        "--output-last-message",
        str(attempt / "result.json"),
    ]
    overrides = {
        "approval_policy": "never",
        "model_reasoning_effort": config.reasoning_effort,
        "shell_environment_policy.inherit": "none",
        "model_instructions_file": str(instructions),
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "suppress_unstable_features_warning": True,
        "analytics.enabled": False,
        "otel.exporter": "none",
        "otel.trace_exporter": "none",
        "otel.log_user_prompt": False,
        "history.persistence": "none",
        "features.skip_host_skill_discovery": True,
        **{f"features.{name}": False for name in DISABLED_FEATURES},
    }
    if config.model is not None:
        overrides["model"] = config.model
    if config.base_url is not None:
        command.append("--ignore-user-config")
        overrides.update(
            {
                "model_provider": "dataset_privacy",
                "model_providers.dataset_privacy.name": "Private dataset detector",
                "model_providers.dataset_privacy.base_url": config.base_url.rstrip("/"),
                "model_providers.dataset_privacy.wire_api": "responses",
                "model_providers.dataset_privacy.requires_openai_auth": False,
                "model_providers.dataset_privacy.request_max_retries": 0,
                "model_providers.dataset_privacy.stream_max_retries": 0,
            }
        )
    else:
        user_config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
        if user_config.exists():
            try:
                settings = tomllib.loads(user_config.read_text())
            except (OSError, ValueError) as exc:
                raise PrivacyError("Cannot read default Codex configuration") from exc
            for name in settings.get("mcp_servers", {}):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise PrivacyError("Unsupported MCP configuration name for a detection-only run")
                overrides[f"mcp_servers.{name}.enabled"] = False
    if config.api_key_env:
        overrides["model_providers.dataset_privacy.env_key"] = "DATASET_PRIVACY_API_KEY"
    for key, value in overrides.items():
        command.extend(["-c", f"{key}={json.dumps(value)}"])
    return [*command, "-"]


def validate_events(path: Path, final: str) -> None:
    started = completed = False
    last_message = None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = load_json(line)
        if not isinstance(event, dict) or completed:
            raise PrivacyError("Malformed or trailing Codex event")
        kind = event.get("type")
        if kind == "thread.started":
            if started:
                raise PrivacyError("Unexpected Codex thread event")
        elif kind == "turn.started":
            if started:
                raise PrivacyError("Unexpected extra Codex turn")
            started = True
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if (
                not started
                and kind == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "error"
                and item.get("message")
                == "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`."
            ):
                continue
            if (
                not started
                or not isinstance(item, dict)
                or item.get("type") not in ("reasoning", "agent_message")
            ):
                raise PrivacyError("Unexpected Codex action or item; no dataset emitted")
            if kind == "item.completed" and item["type"] == "agent_message":
                last_message = item.get("text")
        elif kind == "turn.completed":
            if not started:
                raise PrivacyError("Codex completed without starting")
            completed = True
        else:
            raise PrivacyError("Codex failed or emitted an unsupported event")
    if not completed or type(last_message) is not str or last_message.strip() != final.strip():
        raise PrivacyError("Missing or inconsistent completed Codex response")


def _stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


class CodexDetector:
    def __init__(self, config: CodexConfig):
        if os.name != "posix":
            raise PrivacyError("This initial Codex runner requires a POSIX host")
        self.config = config
        self.executable = shutil.which(config.executable)
        if not self.executable:
            raise PrivacyError("Codex executable not found")

    def scan(self, chunk: Chunk, attempt: Path, *, feedback: str | None = None) -> Scan:
        """Each attempt is a fresh process/thread; failed evidence stays private."""
        request = {"chunk_id": chunk.chunk_id, "role": chunk.role, "text": chunk.text}
        if feedback:
            request["validation_feedback"] = feedback
        value = self._run(request, attempt, FINDING_SCHEMA, INSTRUCTIONS)
        try:
            return resolve_result(value, chunk)
        except PrivacyError as exc:
            raise InvalidFindings(str(exc)) from exc

    def scan_batch(
        self, chunks: list[Chunk], attempt: Path, *, feedback: str | None = None
    ) -> dict[str, Scan]:
        """Amortize CLI startup while requiring an explicit valid result for every chunk."""
        if not chunks or len(chunks) > 16 or len({c.chunk_id for c in chunks}) != len(chunks):
            raise PrivacyError("Batch requires 1–16 uniquely identified chunks")
        if sum(len(c.text) for c in chunks) > 12000:
            raise PrivacyError("Batch character budget exceeded")
        request = {"chunks": [{"chunk_id": c.chunk_id, "role": c.role, "text": c.text} for c in chunks]}
        if feedback:
            request["validation_feedback"] = feedback
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"results": {"type": "array", "items": FINDING_SCHEMA}},
            "required": ["results"],
        }
        value = self._run(request, attempt, schema, BATCH_INSTRUCTIONS)
        try:
            if (
                not isinstance(value, dict)
                or set(value) != {"results"}
                or not isinstance(value["results"], list)
            ):
                raise PrivacyError("Invalid batch response")
            by_id = {c.chunk_id: c for c in chunks}
            result = {}
            for item in value["results"]:
                if not isinstance(item, dict) or not isinstance(item.get("chunk_id"), str):
                    raise PrivacyError("Invalid batch item")
                cid = item["chunk_id"]
                if cid not in by_id or cid in result:
                    raise PrivacyError("Duplicate or unexpected batch chunk")
                result[cid] = resolve_result(item, by_id[cid])
            if result.keys() != by_id.keys():
                raise PrivacyError("Incomplete batch coverage")
            return result
        except PrivacyError as exc:
            raise InvalidFindings(str(exc)) from exc

    def _run(self, request: dict, attempt: Path, schema: dict, instructions: str):
        attempt.mkdir(mode=0o700)
        write_json(attempt / "request.json", request)
        write_json(attempt / "schema.json", schema)
        write_private(attempt / "instructions.txt", instructions)
        with tempfile.TemporaryDirectory(prefix="runtime-", dir=attempt) as temporary:
            runtime = Path(temporary)
            home, workspace = runtime / "home", runtime / "workspace"
            home.mkdir(mode=0o700)
            workspace.mkdir(mode=0o700)
            environment = child_environment(home, self.config)
            try:
                version = subprocess.run(
                    [self.executable, "--version"],
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    timeout=10,
                    check=False,
                    umask=0o077,
                )
            except subprocess.TimeoutExpired as exc:
                raise PrivacyError("Codex version probe timed out") from exc
            if version.returncode != 0 or len(version.stdout) > 1024:
                raise PrivacyError("Cannot identify Codex executable version")
            try:
                version_text = version.stdout.decode("utf-8")
            except UnicodeError as exc:
                raise PrivacyError("Cannot identify Codex executable version") from exc
            write_private(attempt / "codex-version.txt", version_text)
            if version_text.strip() != SUPPORTED_CODEX_VERSION:
                raise PrivacyError(
                    "Unsupported Codex version; review the runner configuration before upgrading"
                )
            command = build_command(self.executable, self.config, attempt, attempt / "instructions.txt")
            write_json(
                attempt / "invocation.json",
                {
                    "argv": command,
                    "base_url": self.config.base_url,
                    "model": self.config.model,
                    "reasoning_effort": self.config.reasoning_effort,
                    "connection": "codex_default" if self.config.base_url is None else "private_endpoint",
                    "timeout_seconds": self.config.timeout,
                },
            )
            input_path = attempt / "request.json"
            output_path, stderr_path = attempt / "events.jsonl", attempt / "stderr.txt"
            with (
                input_path.open("rb") as stdin,
                output_path.open("xb") as stdout,
                stderr_path.open("xb") as stderr,
            ):
                output_path.chmod(0o600)
                stderr_path.chmod(0o600)
                process = subprocess.Popen(
                    command,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=workspace,
                    env=environment,
                    start_new_session=True,
                    umask=0o077,
                )
                deadline = time.monotonic() + self.config.timeout
                try:
                    while process.poll() is None:
                        if time.monotonic() >= deadline:
                            raise PrivacyError("Codex detector timed out")
                        if any(
                            p.exists() and p.stat().st_size > MAX_OUTPUT_BYTES
                            for p in (output_path, stderr_path, attempt / "result.json")
                        ):
                            raise PrivacyError("Codex output exceeded the size limit")
                        time.sleep(0.02)
                finally:
                    # Kill any surviving children as well, even after the main process exits.
                    _stop(process)
            if process.returncode != 0:
                raise PrivacyError("Codex process failed; inspect private attempt evidence")
        final_path = attempt / "result.json"
        if not final_path.is_file() or final_path.is_symlink():
            raise PrivacyError("Codex did not produce a regular result file")
        final_path.chmod(0o600)
        if any(p.stat().st_size > MAX_OUTPUT_BYTES for p in (output_path, stderr_path, final_path)):
            raise PrivacyError("Codex output exceeded the size limit")
        try:
            final = final_path.read_text(encoding="utf-8")
            validate_events(output_path, final)
            try:
                return load_json(final)
            except PrivacyError as exc:
                raise InvalidFindings(str(exc)) from exc
        except UnicodeError as exc:
            raise PrivacyError("Codex output is not valid UTF-8") from exc

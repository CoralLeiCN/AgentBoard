"""Synthetic regressions; the fake executable never connects to a model."""

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dataset_privacy.__main__ import main
from dataset_privacy.codex import CodexConfig, CodexDetector, build_command, child_environment
from dataset_privacy.contracts import (
    Chunk,
    PrivacyError,
    Scan,
    chunk_message,
    load_json,
    resolve_result,
    validate_result,
)
from dataset_privacy.pipeline import digest, prepare
from dataset_privacy.source import project


def row(index, text, previous=None, session="synthetic-session"):
    return {
        "index": index,
        "original_codex_session_id": session,
        "previous_turn_index": previous,
        "source_rollout": "/private/synthetic.jsonl",
        "messages": [{"role": "user", "content": text, "event_id": "private-event"}],
    }


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.json"
    rows = [
        row(10, "你好 👋 Contact Alex at alex@example.test"),
        row(20, "Alex replied.", 10),
        row(30, ""),
        {**row(40, "Assistant only"), "messages": [{"role": "assistant", "content": "Only an echo"}]},
    ]
    path.write_text(json.dumps(rows))
    return path


@pytest.fixture
def fake_codex(tmp_path):
    def create(mode="ok"):
        path = tmp_path / f"fake-codex-{mode}"
        code = r"""
import json, os, pathlib, sys, time
if '--version' in sys.argv:
    if MODE == 'invalid-version':
        sys.stdout.buffer.write(b'\xff')
        sys.exit(0)
    print('codex-cli 0.160.0' if MODE != 'version' else 'codex-cli unknown')
    sys.exit(0)
args = sys.argv[1:]
assert '--ignore-rules' in args and '--strict-config' in args
assert args[args.index('--sandbox')+1] == 'read-only'
assert 'features.shell_tool=false' in args and 'features.plugins=false' in args
if MODE in ('default', 'default-override'):
    assert '--ignore-user-config' not in args
    expected_model, expected_effort = ('gpt-6.1-sol', 'high') if MODE == 'default' else ('synthetic-override', 'medium')
    assert f'model={json.dumps(expected_model)}' in args
    assert f'model_reasoning_effort={json.dumps(expected_effort)}' in args
    assert not any(a.startswith('model_provider=') for a in args)
    assert 'mcp_servers.synthetic.enabled=false' in args
    assert (pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json').read_text() == 'synthetic-auth'
else:
    assert '--ignore-user-config' in args
    assert 'model_providers.dataset_privacy.requires_openai_auth=false' in args
    assert not (pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json').exists()
assert 'web_search="disabled"' in args and 'otel.exporter="none"' in args
assert not {'OPENAI_API_KEY', 'HTTPS_PROXY', 'CODEX_APP_TOOLS_PIPE_PATH'} & os.environ.keys()
assert not list(pathlib.Path.cwd().iterdir())
if MODE == 'timeout':
    time.sleep(10)
if MODE == 'exit':
    print('sensitive synthetic diagnostic', file=sys.stderr)
    sys.exit(7)
request = json.load(sys.stdin)
def inspect(one):
    findings = []
    for quote, category in [('Alex', 'PERSON'), ('alex@example.test', 'EMAIL')]:
        start = 0
        while (start := one['text'].find(quote, start)) >= 0:
            findings.append(dict(occurrence=-1, quote=quote, category=category))
            start += len(quote)
    return dict(chunk_id=one['chunk_id'], findings=findings, requires_review=False)
response = {'results': [inspect(one) for one in reversed(request['chunks'])]} if 'chunks' in request else inspect(request)
if MODE == 'batch-missing':
    response['results'].pop()
if MODE == 'batch-duplicate':
    response['results'].append(response['results'][0])
if MODE == 'batch-unknown':
    response['results'][0]['chunk_id'] = 'unknown'
if MODE == 'batch-bad-span':
    response['results'][-1]['findings'][0]['quote'] = 'invented quote'
if MODE == 'invalid' or (MODE == 'retry' and 'validation_feedback' not in request):
    response['extra'] = 'not allowed'
final = json.dumps(response)
output = pathlib.Path(args[args.index('--output-last-message')+1])
if MODE != 'missing':
    output.write_text(final)
if MODE == 'exit-after-result':
    sys.exit(7)
events = [dict(type='thread.started', thread_id='synthetic'), dict(type='turn.started'),
          dict(type='item.completed', item=dict(type='agent_message', text=final)),
          dict(type='turn.completed', usage=dict(input_tokens=1, output_tokens=1))]
if MODE == 'action':
    events.insert(2, dict(type='item.started', item=dict(type='command_execution', command='synthetic')))
if MODE == 'incomplete':
    events.pop()
if MODE == 'mismatch':
    events[2]['item']['text'] = '{}'
if MODE == 'malformed':
    print('not-json')
else:
    for event in events:
        print(json.dumps(event))
"""
        path.write_text(f"#!{sys.executable}\nMODE={mode!r}\n" + code)
        path.chmod(0o700)
        return str(path)

    return create


def detector(fake_codex, mode="ok", timeout=5):
    return CodexDetector(
        CodexConfig(
            "synthetic-model",
            base_url="http://192.168.1.220:30000/v1",
            executable=fake_codex(mode),
            timeout=timeout,
        )
    )


def test_codex_process_to_private_draft_preserves_source_and_shared_context(
    source, tmp_path, fake_codex, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://forbidden.example")
    monkeypatch.setenv("CODEX_APP_TOOLS_PIPE_PATH", "/synthetic-parent")
    before = source.read_bytes()
    output = tmp_path / "run"
    report = prepare(source, output, detector(fake_codex))
    assert report["status"] == "complete_draft"
    assert report["privacy_status"] == "needs_review"
    assert report["counts"]["selected_turns"] == 4
    assert report["counts"]["excluded_turns"] == 2
    assert report["completed_chunks"] == 2  # The predecessor was scanned once, not twice.
    drafts = [json.loads(line) for line in (output / "draft.jsonl").read_text().splitlines()]
    assert len(drafts) == 2
    assert drafts[1]["input"]["context"] == drafts[0]["input"]["messages"]
    assert drafts[0]["input"]["messages"][0]["content"].startswith("你好 👋 Contact <PERSON_")
    assert "Alex" not in (output / "draft.jsonl").read_text()
    assert "alex@example.test" not in (output / "draft.jsonl").read_text()
    assert "source_rollout" not in (output / "draft.jsonl").read_text()
    assert "private-event" not in (output / "draft.jsonl").read_text()
    assert all(d["privacy_status"] == "needs_review" and d["target"] is None for d in drafts)
    assert source.read_bytes() == before
    assert report["draft_sha256"] == digest((output / "draft.jsonl").read_bytes())
    prompts = {
        path.name: path.read_bytes()
        for path in (Path(__file__).resolve().parents[1] / "prompts").glob("*.txt")
    }
    assert report["prompt_sha256"] == {name: digest(text) for name, text in prompts.items()}
    assert report["instructions_sha256"] == digest(prompts["detection.txt"])
    assert (output / "instructions.txt").read_bytes() == prompts["detection.txt"]
    for attempt in (output / "attempts").iterdir():
        assert (attempt / "instructions.txt").read_bytes() == prompts["detection.txt"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.rglob("*") if p.is_file())
    assert not list(output.rglob("runtime-*"))
    saved_mapping = json.loads((output / "mapping.json").read_text())
    name = next(x["replacement"] for x in saved_mapping if x["original"] == "Alex")
    assert name in drafts[0]["input"]["messages"][0]["content"]
    assert name in drafts[1]["input"]["messages"][0]["content"]


@pytest.mark.parametrize(
    "mode",
    [
        "invalid",
        "action",
        "incomplete",
        "mismatch",
        "malformed",
        "missing",
        "exit",
        "exit-after-result",
        "timeout",
        "version",
        "invalid-version",
    ],
)
def test_failed_runs_keep_private_evidence_without_a_draft(mode, source, tmp_path, fake_codex):
    output = tmp_path / "failure"
    with pytest.raises(PrivacyError):
        prepare(source, output, detector(fake_codex, mode, timeout=0.15 if mode == "timeout" else 5))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    receipt = json.loads(next((output / "chunks").glob("*.json")).read_text())
    assert receipt["status"] == "failed"
    assert not (output / "draft.jsonl").exists()
    attempts = list((output / "attempts").iterdir())
    assert len(attempts) == (2 if mode == "invalid" else 1)
    assert all((attempt / "request.json").is_file() for attempt in attempts)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.rglob("*") if p.is_file())


def test_validation_retry_is_bounded_and_records_both_attempts(source, tmp_path, fake_codex):
    result = prepare(source, tmp_path / "retry", detector(fake_codex, "retry"))
    assert result["status"] == "complete_draft"
    assert all(json.loads(p.read_text())["attempts"] == 2 for p in (tmp_path / "retry/chunks").glob("*.json"))


@pytest.mark.parametrize(
    "mutation",
    [
        {"start": True},
        {"start": "0"},
        {"start": -1},
        {"end": 9},
        {"end": 0},
        {"quote": "invented"},
        {"category": "APPROVED"},
        {"new_text": "rewrite"},
    ],
)
def test_strict_findings_reject_invalid_spans_and_fields(mutation):
    finding = {"start": 0, "end": 4, "quote": "Alex", "category": "PERSON", **mutation}
    with pytest.raises(PrivacyError):
        validate_result(
            {"chunk_id": "c", "requires_review": False, "findings": [finding]},
            Chunk("c", "m", "user", 0, "Alex"),
        )


def test_repeated_unicode_offsets_and_overlap_conflicts():
    chunk = Chunk("c", "m", "user", 10, "你好 👋 Alex Alex")
    result = {
        "chunk_id": "c",
        "requires_review": True,
        "findings": [
            {"start": 5, "end": 9, "quote": "Alex", "category": "PERSON"},
            {"start": 10, "end": 14, "quote": "Alex", "category": "PERSON"},
        ],
    }
    assert [f.start for f in validate_result(result, chunk).findings] == [15, 20]
    result["findings"].append({"start": 5, "end": 14, "quote": "Alex Alex", "category": "PERSON"})
    with pytest.raises(PrivacyError, match="overlapping"):
        validate_result(result, chunk)


def test_quote_resolution_handles_unicode_repeats_and_occurrence_selection():
    chunk = Chunk("c", "m", "user", 10, "你好 👋 Alex Alex")
    result = {
        "chunk_id": "c",
        "requires_review": True,
        "findings": [{"quote": "Alex", "category": "PERSON", "occurrence": -1}],
    }
    assert [f.start for f in resolve_result(result, chunk).findings] == [15, 20]
    result["findings"][0]["occurrence"] = 1
    assert [f.start for f in resolve_result(result, chunk).findings] == [20]


@pytest.mark.parametrize(
    "finding",
    [
        {"quote": "invented", "category": "PERSON", "occurrence": -1},
        {"quote": "", "category": "PERSON", "occurrence": -1},
        {"quote": "Alex", "category": "PERSON", "occurrence": True},
        {"quote": "Alex", "category": "PERSON", "occurrence": -2},
        {"quote": "Alex", "category": "PERSON", "occurrence": 1},
        {"quote": "Alex", "category": "UNKNOWN", "occurrence": -1},
    ],
)
def test_quote_resolution_rejects_unmatched_or_invalid_findings(finding):
    with pytest.raises(PrivacyError):
        resolve_result(
            {"chunk_id": "c", "requires_review": False, "findings": [finding]},
            Chunk("c", "m", "user", 0, "Alex"),
        )


def test_complete_long_unicode_chunk_coverage():
    text = "你好👋 " * 21000
    coverage = bytearray(len(text))
    chunks = chunk_message("m", "user", text, 4096, 512)
    for chunk in chunks:
        assert chunk.text == text[chunk.start : chunk.start + len(chunk.text)]
        coverage[chunk.start : chunk.start + len(chunk.text)] = b"\x01" * len(chunk.text)
    assert all(coverage)
    assert len(chunks) > 1


@pytest.mark.parametrize(
    "rows",
    [
        [row(0, "a"), row(0, "b")],
        [row(0, "a", 4)],
        [row(0, "a", True)],
        [row(0, "a", 0)],
        [row(0, "a", 1), row(1, "b", 0)],
        [{**row(0, "a"), "messages": [{"role": "tool", "content": "b"}]}],
        [{**row(0, "a"), "messages": [{"role": "user", "content": {"image": "x"}}]}],
    ],
)
def test_source_rejects_ambiguous_or_unsupported_data(rows):
    with pytest.raises(PrivacyError):
        project(rows)


def test_projection_limits_targets_but_resolves_context_and_preserves_recorded_roles():
    rows = [row(10, "<user_shell_command>synthetic output</user_shell_command>", 20), row(20, "prior")]
    rows[0]["messages"].insert(0, {"role": "assistant", "content": "assistant first"})
    result = project(rows, limit=1)
    assert len(result.examples) == 1
    assert len(result.examples[0].context) == 1
    assert [result.messages[mid].role for mid in result.examples[0].target] == ["assistant", "user"]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://8.8.8.8/v1",
        "http://169.254.169.254/v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?key=secret",
    ],
)
def test_disallows_public_or_credential_bearing_endpoints(url):
    with pytest.raises(PrivacyError):
        CodexConfig("synthetic", base_url=url)


def test_only_explicit_private_key_is_forwarded(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PRIVATE_TOKEN", "synthetic-private-token")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    environment = child_environment(
        tmp_path,
        CodexConfig("synthetic", base_url="http://192.168.1.220:30000/v1", api_key_env="TEST_PRIVATE_TOKEN"),
    )
    assert environment["DATASET_PRIVACY_API_KEY"] == "synthetic-private-token"
    assert "OPENAI_API_KEY" not in environment
    assert "TEST_PRIVATE_TOKEN" not in environment


@pytest.mark.parametrize(
    ("mode", "options", "model", "effort"),
    [
        ("default", [], "gpt-6.1-sol", "high"),
        (
            "default-override",
            ["--model", "synthetic-override", "--reasoning-effort", "medium"],
            "synthetic-override",
            "medium",
        ),
    ],
)
def test_default_cli_uses_privacy_settings_and_saved_login_without_copying_them(
    mode, options, model, effort, source, tmp_path, monkeypatch, fake_codex
):
    home = tmp_path / "normal-codex-home"
    home.mkdir()
    (home / "auth.json").write_text("synthetic-auth")
    config_text = 'model="configured-model"\nmodel_reasoning_effort="xhigh"\n[mcp_servers.synthetic]\ncommand="unused"\n'
    (home / "config.toml").write_text(config_text)
    monkeypatch.setenv("CODEX_HOME", str(home))
    for key in ("OPENAI_API_KEY", "HTTPS_PROXY", "CODEX_APP_TOOLS_PIPE_PATH"):
        monkeypatch.delenv(key, raising=False)
    output = tmp_path / "default-run"
    assert main(["prepare", str(source), "--output", str(output), "--codex", fake_codex(mode), *options]) == 0
    report = json.loads((output / "manifest.json").read_text())
    assert report["status"] == "complete_draft"
    assert (home / "auth.json").read_text() == "synthetic-auth"
    assert (home / "config.toml").read_text() == config_text
    assert not list(output.rglob("auth.json"))
    invocation = json.loads(next(output.rglob("invocation.json")).read_text())
    assert invocation["connection"] == "codex_default"
    assert invocation["model"] == model and invocation["base_url"] is None
    assert invocation["reasoning_effort"] == effort


def test_default_command_applies_privacy_defaults_and_disables_configured_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'model="configured-model"\nmodel_reasoning_effort="xhigh"\n[mcp_servers.test-server]\ncommand="unused"\n'
    )
    command = build_command("codex", CodexConfig(), tmp_path, tmp_path / "instructions.txt")
    assert "--ignore-user-config" not in command
    assert "mcp_servers.test-server.enabled=false" in command
    assert 'model="gpt-6.1-sol"' in command
    assert 'model_reasoning_effort="high"' in command
    assert not any(arg.startswith("model_provider=") for arg in command)
    with pytest.raises(PrivacyError):
        CodexConfig(api_key_env="UNEXPECTED")
    with pytest.raises(PrivacyError):
        CodexConfig(base_url="http://127.0.0.1/v1")


@pytest.mark.parametrize("base_url", [None, "http://127.0.0.1/v1"])
def test_explicit_model_and_reasoning_override_privacy_defaults(base_url, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = CodexConfig("synthetic-model", base_url=base_url, reasoning_effort="medium")
    command = build_command("codex", config, tmp_path, tmp_path / "instructions.txt")
    assert 'model="synthetic-model"' in command
    assert 'model_reasoning_effort="medium"' in command


@pytest.mark.parametrize("effort", ["", " ", None])
def test_invalid_reasoning_effort_is_rejected(effort):
    with pytest.raises(PrivacyError, match="Invalid reasoning effort"):
        CodexConfig(reasoning_effort=effort)


def test_batch_results_match_ids_and_apply_message_offsets(fake_codex, tmp_path):
    chunks = [Chunk("c1", "m1", "user", 20, "你好 Alex"), Chunk("c2", "m2", "assistant", 0, "clean")]
    results = detector(fake_codex).scan_batch(chunks, tmp_path / "batch")
    assert results["c1"].findings[0].start == 23
    assert results["c2"].findings == ()
    prompts = Path(__file__).resolve().parents[1] / "prompts"
    expected = (prompts / "detection.txt").read_bytes() + b"\n" + (prompts / "batch.txt").read_bytes()
    assert (tmp_path / "batch/instructions.txt").read_bytes() == expected


@pytest.mark.parametrize("mode", ["batch-missing", "batch-duplicate", "batch-unknown", "batch-bad-span"])
def test_invalid_batch_blocks_acceptance(mode, fake_codex, tmp_path):
    chunks = [Chunk("c1", "m1", "user", 0, "Alex"), Chunk("c2", "m2", "assistant", 0, "clean")]
    with pytest.raises(PrivacyError):
        detector(fake_codex, mode).scan_batch(chunks, tmp_path / "batch")


def test_existing_output_and_source_hash_mismatch_make_no_model_calls(source, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected detector call")

    stub = SimpleNamespace(scan=forbidden)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel").write_text("preserve")
    with pytest.raises(FileExistsError):
        prepare(source, output, stub)
    with pytest.raises(PrivacyError, match="hash"):
        prepare(source, tmp_path / "different", stub, expected_sha256="0" * 64)
    with pytest.raises(PrivacyError, match="budget"):
        prepare(source, tmp_path / "budget", stub, max_chunks=1)
    assert (output / "sentinel").read_text() == "preserve"


def test_source_changes_invalidate_draft(source, tmp_path):
    def change_source(chunk, attempt, **kwargs):
        source.write_text("[]")
        return Scan((), False)

    output = tmp_path / "changed"
    with pytest.raises(PrivacyError, match="Source changed"):
        prepare(source, output, SimpleNamespace(scan=change_source))
    assert not (output / "draft.jsonl").exists()
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"


def test_no_findings_never_grants_approval(source, tmp_path):
    result = prepare(
        source, tmp_path / "empty-findings", SimpleNamespace(scan=lambda *a, **kw: Scan((), False))
    )
    assert result["privacy_status"] == "needs_review"
    assert result["finding_count"] == 0


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', '{"value": NaN}', "```json\n{}\n```"])
def test_ambiguous_or_non_json_responses_are_rejected(text):
    with pytest.raises(PrivacyError):
        load_json(text)


def test_plan_cli_is_offline_and_reports_only_aggregate_data(source, capsys):
    assert main(["plan", str(source)]) == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["model_calls"] == 0
    assert value["eligible_examples"] == 2
    assert "Alex" not in output and "private-event" not in output


def test_cli_process_works_after_copying_module_without_site_packages(source, tmp_path, fake_codex):
    destination = tmp_path / "portable"
    destination.mkdir()
    shutil.copytree(
        Path(__file__).resolve().parents[1],
        destination / "dataset_privacy",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    result = subprocess.run(
        [sys.executable, "-S", "-m", "dataset_privacy", "plan", str(source)],
        cwd=destination,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["model_calls"] == 0
    output = destination / "private-run"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "dataset_privacy",
            "prepare",
            str(source),
            "--output",
            str(output),
            "--codex",
            fake_codex(),
            "--base-url",
            "http://127.0.0.1/v1",
            "--model",
            "synthetic-model",
        ],
        cwd=destination,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["status"] == "complete_draft"
    expected = (destination / "dataset_privacy/prompts/detection.txt").read_bytes()
    assert (output / "instructions.txt").read_bytes() == expected
    for attempt in (output / "attempts").iterdir():
        assert (attempt / "instructions.txt").read_bytes() == expected


def test_conflicting_findings_between_chunks_block_entire_draft(tmp_path):
    source = tmp_path / "overlap.json"
    source.write_text(json.dumps([row(0, "abcdAlexefgh")]))

    def scan(chunk, attempt, **kwargs):
        category = "PERSON" if chunk.start == 0 else "USERNAME"
        offset = chunk.text.index("Alex")
        return validate_result(
            {
                "chunk_id": chunk.chunk_id,
                "requires_review": False,
                "findings": [{"start": offset, "end": offset + 4, "quote": "Alex", "category": category}],
            },
            chunk,
        )

    output = tmp_path / "conflict"
    with pytest.raises(PrivacyError, match="overlapping"):
        prepare(source, output, SimpleNamespace(scan=scan), chunk_characters=10, overlap=8)
    assert not (output / "draft.jsonl").exists()


def test_complete_path_replaces_contained_boundary_fragment(tmp_path):
    source = tmp_path / "boundary.json"
    path = "/home/example/private.txt"
    text = "X" * 20 + path + " suffix"
    source.write_text(json.dumps([row(0, text)]))

    def scan(chunk, attempt, **kwargs):
        quote = path if path in chunk.text else text[20:30] if chunk.start == 0 else None
        if quote is None:
            return Scan((), False)
        start = chunk.text.index(quote)
        return validate_result(
            {
                "chunk_id": chunk.chunk_id,
                "requires_review": chunk.start == 0,
                "findings": [{"start": start, "end": start + len(quote), "quote": quote, "category": "PATH"}],
            },
            chunk,
        )

    output = tmp_path / "boundary-run"
    manifest = prepare(source, output, SimpleNamespace(scan=scan), chunk_characters=30, overlap=15)
    assert manifest["contained_findings_collapsed"] == 1
    assert manifest["finding_count"] == 1
    assert path not in (output / "draft.jsonl").read_text()
    saved = json.loads((output / "findings.json").read_text())
    assert next(iter(saved.values()))[0]["quote"] == path

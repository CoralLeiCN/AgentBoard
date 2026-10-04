"""Synthetic Jev protocol, input preservation, and interrupted-run regressions."""

import copy
import fcntl
import json
from dataclasses import replace

import pytest

from classifier.artifacts import read_json, read_rows
from classifier.evaluation import compare, valid_prediction
from classifier.taxonomy import CATEGORIES
from run_jev import ENDPOINT, MODEL, JevConfig, Reply, main, parse_prediction, post_evaluation, run


def turns_file(tmp_path):
    turns = [
        {
            "index": i,
            "original_codex_session_id": "synthetic-session",
            "original_codex_turn_id": f"synthetic-turn-{i}",
            "classification_input_sha256": str(i) * 64,
            "previous_turn_index": None if i == 0 else i - 1,
            "messages": [{"role": "user", "content": text}],
            "unknown_source_field": {"keep": True},
        }
        for i, text in enumerate(["Implement a sorting function. " * 2000, "Add tests.", "Fix the crash."])
    ]
    turns[-1]["messages"] = []
    path = tmp_path / "turns.json"
    path.write_text(json.dumps(turns))
    return path


def reply(category="coding"):
    return Reply(
        200,
        json.dumps(
            {
                "model": MODEL,
                "answers": {
                    "purpose": {
                        "type": "choice",
                        "choice": category,
                        "probabilities": {c: float(c == category) for c in CATEGORIES},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 200, "output_tokens": 34},
            }
        ),
    )


def test_preview_is_offline_lossless_and_selects_in_source_order(tmp_path, monkeypatch):
    source = turns_file(tmp_path)
    turns = read_json(source)
    # Selected first target references a predecessor outside the selected subset.
    source.write_text(json.dumps([turns[1], turns[0], turns[2]]))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = JevConfig(source, tmp_path / "run", limit=1)
    summary = run(config, transport=lambda *args: pytest.fail("Preview must not request a model"))
    assert summary["mode"] == "preview" and summary["pending"] == 1
    assert (config.output / "source-turns").read_bytes() == source.read_bytes()
    inputs = read_rows(config.output / "inputs.jsonl")
    state = json.loads(inputs[0]["text"])
    assert state == {"target": turns[1]["messages"], "previous": turns[0]["messages"]}
    assert list((config.output / "attempts").iterdir()) == []
    assert inputs[0]["input_sha256"] == turns[1]["classification_input_sha256"]


def test_execute_requires_key_and_resume_preserves_successes_and_raw_failures(tmp_path, monkeypatch):
    config = JevConfig(turns_file(tmp_path), tmp_path / "run")
    run(config)
    config = replace(config, execute=True, resume=True)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        run(config)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-secret")
    requests = []

    def first(payload, key, timeout):
        assert key == "synthetic-secret" and timeout == 60
        requests.append(copy.deepcopy(payload))
        return reply() if len(requests) == 1 else Reply(429, '{"error":"rate limit"}')

    summary = run(config, transport=first)
    assert summary["available"] == 1 and summary["pending"] == 2 and summary["attempts"] == 2
    first_success = (config.output / "attempts/00000000.response.json").read_bytes()
    retried = []

    def remaining(payload, key, timeout):
        retried.append(payload)
        return reply()

    summary = run(config, transport=remaining)
    assert summary["available"] == 3 and summary["attempts"] == 4
    assert len(retried) == 2
    assert retried[0] == requests[1]
    assert json.loads(retried[1]["state"])["target"] == []
    assert (config.output / "attempts/00000000.response.json").read_bytes() == first_success
    predictions = read_rows(config.output / "predictions.jsonl")
    assert all(valid_prediction(row) for row in predictions)
    assert predictions[0]["usage"] == {"input_tokens": 200, "output_tokens": 34}
    assert predictions[0]["provider_metadata"] is None
    assert predictions[0]["response_model"] == "jev-1.13.0"
    assert predictions[0]["confidence"] == 1.0
    assert all("reason" not in row for row in predictions)
    for file in config.output.rglob("*"):
        if file.is_file():
            assert b"synthetic-secret" not in file.read_bytes()
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert (
        run(config, transport=lambda *a: pytest.fail("Completed run must not repeat calls"))["pending"] == 0
    )


def test_interrupted_request_is_preserved_and_retried_explicitly(tmp_path, monkeypatch):
    config = JevConfig(turns_file(tmp_path), tmp_path / "run", execute=True, limit=1)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")

    def interrupted(*args):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(config, transport=interrupted)
    assert (config.output / "attempts/00000000.request.json").exists()
    assert not (config.output / "attempts/00000000.response.json").exists()
    summary = run(replace(config, resume=True), transport=lambda *a: reply())
    assert summary["attempts"] == 2 and summary["available"] == 1


@pytest.mark.parametrize("failure", [Reply(401, "unauthorized"), Reply(200, "not json"), TimeoutError()])
def test_failed_attempt_stops_and_retains_evidence(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")
    config = JevConfig(turns_file(tmp_path), tmp_path / "run", execute=True)

    def failed(*args):
        if isinstance(failure, Exception):
            raise failure
        return failure

    summary = run(config, transport=failed)
    assert summary["available"] == 0 and summary["attempts"] == 1 and summary["pending"] == 3
    assert len(list((config.output / "attempts").glob("*.response.json"))) == 1


@pytest.mark.parametrize("change", ["source", "selection", "question", "saved-source", "model"])
def test_resume_rejects_changed_evidence(tmp_path, monkeypatch, change):
    config = JevConfig(turns_file(tmp_path), tmp_path / "run")
    run(config)
    if change == "source":
        config.turns.write_text(config.turns.read_text() + "\n")
    elif change == "selection":
        config = replace(config, limit=1)
    elif change == "question":
        monkeypatch.setattr("run_jev.QUESTION", {"new": "question"})
    elif change == "model":
        monkeypatch.setattr("run_jev.MODEL", "jev-1.14.0")
    else:
        (config.output / "source-turns").write_text("altered")
    with pytest.raises(ValueError, match="Resume requires|Saved source bytes"):
        run(replace(config, resume=True))


@pytest.mark.parametrize("change", ["model", "missing", "unknown", "nan", "sum", "argmax", "type"])
def test_invalid_provider_results_are_rejected(change):
    value = json.loads(reply().body)
    answer = value["answers"]["purpose"]
    if change == "model":
        value["model"] = "unexpected-provider/model"
    elif change == "missing":
        answer.pop("probabilities")
    elif change == "unknown":
        answer["choice"] = "debugging"
    elif change == "nan":
        answer["probabilities"]["coding"] = float("nan")
    elif change == "sum":
        answer["probabilities"]["coding"] = 0.2
    elif change == "argmax":
        answer["choice"] = "other"
    else:
        answer["type"] = "boolean"
    row = dict.fromkeys(("session_id", "turn_id", "input_sha256", "text_sha256"), "synthetic")
    with pytest.raises(ValueError):
        parse_prediction(Reply(200, json.dumps(value)), row, {"model": MODEL})


def test_http_transport_uses_evaluate_and_bearer_only(monkeypatch):
    class Response:
        code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return reply().body.encode()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.typesafe.ai/v1/systemone" == ENDPOINT
            assert request.get_header("Authorization") == "Bearer synthetic-key"
            assert request.method == "POST" and timeout == 7
            assert json.loads(request.data) == {"model": MODEL}
            return Response()

    def opener(*handlers):
        assert handlers[0].proxies == {}
        assert handlers[1].redirect_request(None, None, None, None, None, None) is None
        return Opener()

    monkeypatch.setattr("run_jev.build_opener", opener)
    assert post_evaluation({"model": MODEL}, "synthetic-key", 7) == reply()


def test_cli_preview_and_nonzero_failure(tmp_path, monkeypatch):
    source = turns_file(tmp_path)
    args = ["--turns", str(source), "--output", str(tmp_path / "run"), "--limit", "1"]
    assert main(args) == 0
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main([*args, "--resume", "--execute"])
    assert exc.value.code == 2


def test_predictions_compare_with_existing_reference_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")
    config = JevConfig(turns_file(tmp_path), tmp_path / "run", limit=1, execute=True)
    run(config, transport=lambda *a: reply())
    targets = read_rows(config.output / "inputs.jsonl")
    targets[0].update(category="coding", label_source={"kind": "gpt", "name": "synthetic-reference"})
    predictions = read_rows(config.output / "predictions.jsonl")
    result = compare(targets, {"jev": predictions})
    assert result["interpretation"] == "reference_agreement"
    assert result["classifiers"]["jev"]["available_metrics"]["accuracy"] == 1.0
    predictions[0]["input_sha256"] = "f" * 64
    result = compare(targets, {"jev": predictions})
    assert result["classifiers"]["jev"]["coverage"] == {"changed_input": 1}


def test_existing_output_and_concurrent_resume_are_rejected(tmp_path):
    config = JevConfig(turns_file(tmp_path), tmp_path / "run")
    run(config)
    with pytest.raises(FileExistsError):
        run(config)
    with (config.output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="Another process"):
            run(replace(config, resume=True))


@pytest.mark.parametrize("previous", [True, {}, "0"])
def test_invalid_predecessor_fails_before_output_or_network(tmp_path, previous):
    path = turns_file(tmp_path)
    turns = read_json(path)
    turns[1]["previous_turn_index"] = previous
    path.write_text(json.dumps(turns))
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="previous_turn_index"):
        run(JevConfig(path, output))
    assert not output.exists()


def test_direct_protocol_uses_typesafe_key_without_gateway_options(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-typesafe-key")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "synthetic-gateway-key")
    config = JevConfig(turns_file(tmp_path), tmp_path / "run", limit=1, execute=True)

    def transport(payload, key, timeout):
        assert key == "synthetic-typesafe-key"
        assert set(payload) == {"model", "state", "questions"}
        assert payload["model"] == "jev-1.13.0"
        assert set(payload["questions"]["purpose"]["criteria"]) == set(CATEGORIES)
        return reply()

    assert run(config, transport=transport)["available"] == 1
    manifest = read_json(config.output / "run.json")
    assert manifest["format_version"] == "jev-turn-run-v2"
    assert manifest["configuration"]["endpoint"] == "https://api.typesafe.ai/v1/systemone"
    assert manifest["configuration"]["provider"] == "typesafe"
    assert "provider_options" not in manifest["configuration"]
    assert manifest["configuration"]["privacy"]["retention"] == "account-agreement-unverified"
    for file in config.output.rglob("*"):
        if file.is_file():
            assert b"synthetic-typesafe-key" not in file.read_bytes()
            assert b"synthetic-gateway-key" not in file.read_bytes()


def test_gateway_key_alone_cannot_send_to_typesafe(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "synthetic-gateway-key")
    config = JevConfig(turns_file(tmp_path), tmp_path / "run", execute=True)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        run(config, transport=lambda *a: pytest.fail("Wrong provider key must never be sent"))
    assert list((config.output / "attempts").iterdir()) == []


def test_gateway_run_cannot_be_resumed_or_rewritten(tmp_path):
    config = JevConfig(turns_file(tmp_path), tmp_path / "run")
    run(config)
    path = config.output / "run.json"
    manifest = read_json(path)
    manifest["format_version"] = "jev-turn-run-v1"
    manifest["configuration"].update(
        model="typesafe-ai/jev",
        endpoint="https://ai-gateway.vercel.sh/v1/evaluate",
        provider_options={"gateway": {"only": ["typesafe-ai"], "disallowPromptTraining": True}},
    )
    path.write_text(json.dumps(manifest))
    before = {p: p.read_bytes() for p in config.output.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Resume requires"):
        run(replace(config, resume=True, execute=True), transport=lambda *a: pytest.fail("No request"))
    assert {p: p.read_bytes() for p in config.output.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("confidence", [None, True, -0.1, 1.1, "0.5", float("nan"), float("inf")])
def test_direct_response_requires_valid_confidence(confidence):
    value = json.loads(reply().body)
    value["answers"]["purpose"]["confidence"] = confidence
    row = dict.fromkeys(("session_id", "turn_id", "input_sha256", "text_sha256"), "synthetic")
    with pytest.raises(ValueError, match="confidence"):
        parse_prediction(Reply(200, json.dumps(value)), row, {"model": MODEL})


def test_obsolete_gateway_retention_switch_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--turns", "unused", "--output", str(tmp_path / "run"), "--allow-provider-retention"])
    assert exc.value.code == 2
    assert not (tmp_path / "run").exists()


def test_target_selection_keeps_unselected_predecessor_and_pins_bytes(tmp_path):
    source = turns_file(tmp_path)
    selection = tmp_path / "indices.json"
    selection.write_text("[1, 2]\n")
    config = JevConfig(source, tmp_path / "run", target_indices=selection, limit=1)
    assert run(config)["requested"] == 1
    row = read_rows(config.output / "inputs.jsonl")[0]
    assert row["turn_id"] == "synthetic-turn-1"
    assert json.loads(row["text"])["previous"] == read_json(source)[0]["messages"]
    assert (config.output / "target-indices.json").read_bytes() == selection.read_bytes()
    assert run(replace(config, resume=True))["requested"] == 1
    selection.write_text("[2, 1]\n")
    with pytest.raises(ValueError, match="Resume requires"):
        run(replace(config, resume=True))


@pytest.mark.parametrize("indices", [[True], [1, 1], [99], {}, ["1"], []])
def test_invalid_selection_fails_before_output_or_requests(tmp_path, indices):
    source = turns_file(tmp_path)
    selection = tmp_path / "indices.json"
    selection.write_text(json.dumps(indices))
    config = JevConfig(source, tmp_path / "run", target_indices=selection)
    with pytest.raises(ValueError, match="target_indices|No turns"):
        run(config)
    assert not config.output.exists()

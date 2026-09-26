"""Boundary errors stay actionable without hiding unrelated implementation failures."""

import json
from pathlib import Path

import pytest

from classifier import cli, models
from classifier.artifacts import read_rows
from classifier.data import turn_inputs


@pytest.mark.parametrize("extension", ["json", "jsonl"])
@pytest.mark.parametrize("row", [None, "not an object", ["not", "an", "object"]])
def test_row_files_reject_nonobjects_at_the_read_boundary(tmp_path, extension, row):
    path = tmp_path / f"rows.{extension}"
    path.write_text(json.dumps([row] if extension == "json" else row) + "\n")
    with pytest.raises(ValueError, match="rows containing objects"):
        read_rows(path)


def test_malformed_message_is_an_input_error():
    turn = {
        "index": 0,
        "original_codex_session_id": "synthetic",
        "original_codex_turn_id": "turn",
        "classification_input_sha256": "0" * 64,
        "messages": [None],
    }
    with pytest.raises(ValueError, match="messages with string content"):
        turn_inputs([turn])


@pytest.mark.parametrize("size", [True, 0, 1.5])
def test_prediction_rejects_invalid_batch_sizes_before_model_work(size):
    with pytest.raises(ValueError, match="batch size must be a positive integer"):
        models.predict(Path("unused-synthetic-model"), [], batch_size=size)


def test_cli_explains_a_missing_optional_package(monkeypatch, capsys):
    def execute(args):
        raise ModuleNotFoundError("No module named 'torch'", name="torch")

    monkeypatch.setattr(cli, "execute", execute)
    with pytest.raises(SystemExit) as caught:
        cli.main(["inputs", "--turns", "synthetic.json", "--output", "unused.jsonl"])
    assert caught.value.code == 2
    assert "--extra ml" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [
        KeyError("Synthetic implementation error"),
        TypeError("Synthetic implementation error"),
        ImportError("Synthetic incompatible package API"),
        ModuleNotFoundError("Synthetic internal import error", name="classifier.missing_internal_module"),
    ],
)
def test_cli_preserves_unexpected_errors_without_dependency_advice(monkeypatch, capsys, failure):
    def execute(args):
        raise failure

    monkeypatch.setattr(cli, "execute", execute)
    with pytest.raises(type(failure)) as caught:
        cli.main(["inputs", "--turns", "synthetic.json", "--output", "unused.jsonl"])
    assert caught.value is failure
    assert "--extra ml" not in capsys.readouterr().err

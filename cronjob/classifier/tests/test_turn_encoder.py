"""Synthetic contracts for chronological Astra distillation and direct serving."""

import copy
import json
import sys
from types import SimpleNamespace

import pytest

from classifier.artifacts import digest
from classifier.turn_encoder import (
    MAX_TOKENS,
    TEACHER,
    chronological_split,
    join_astra,
    serialize,
    source_hash,
    tokenize,
)


class CharacterTokenizer:
    """Deterministic test tokenizer with two preserved special tokens."""

    def __call__(self, text, *, truncation=False, padding=False, max_length=None):
        assert not padding
        ids = [ord(char) + 3 for char in text]
        if truncation:
            ids = ids[: max_length - 2]
        return {"input_ids": [1, *ids, 2]}


def cohort(n=20):
    turns, answers = [], []
    for i in range(n):
        target = [{"role": "user", "content": f"Synthetic task {i}"}]
        turn = {
            "index": i,
            "original_codex_session_id": f"session-{i:02d}",
            "original_codex_turn_id": f"turn-{i:02d}",
            "previous_turn_index": None,
            "messages": target,
            "started_at": f"2026-01-{i + 1:02d}T00:00:00Z",
            "classification_input_sha256": source_hash(target, []),
        }
        turns.append(turn)
        answers.append(
            {
                **{
                    k: turn[k]
                    for k in (
                        "original_codex_session_id",
                        "original_codex_turn_id",
                        "classification_input_sha256",
                    )
                },
                "category": "coding" if i % 2 else "writing",
                "reason": "PRIVATE_REASON_SENTINEL",
            }
        )
    return turns, {"reference_configuration": TEACHER, "answers": answers}


@pytest.mark.parametrize("length", [8191, 8192, 8193, 12000])
def test_token_cap_preserves_special_tokens_and_reports_loss(length):
    overhead = len(serialize([{"role": "user", "content": ""}], [])) + 2
    target = [{"role": "user", "content": "x" * (length - overhead)}]
    row = tokenize(CharacterTokenizer(), target, [])
    assert row["original_token_count"] == length
    assert row["used_token_count"] == min(length, MAX_TOKENS)
    assert row["dropped_token_count"] == max(0, length - MAX_TOKENS)
    assert row["truncated"] == (length > MAX_TOKENS)
    assert row["input_ids"][0] == 1 and row["input_ids"][-1] == 2
    assert row["effective_input_sha256"] == digest(row["input_ids"])


def test_target_priority_and_empty_inputs():
    target = [{"role": "user", "content": "x" * 9000}]
    before = [{"role": "assistant", "content": "DO_NOT_RETAIN"}]
    row = tokenize(CharacterTokenizer(), target, before)
    decoded = "".join(chr(i - 3) for i in row["input_ids"][1:-1])
    assert "DO_NOT_RETAIN" not in decoded
    assert "[TARGET TURN]" in decoded
    empty = tokenize(CharacterTokenizer(), [], [])
    assert not empty["truncated"]
    assert serialize([], []).count("[EMPTY]") == 2
    assert row["input_sha256"] != tokenize(CharacterTokenizer(), target, [])["input_sha256"]


def test_complete_label_join_and_no_metadata_features():
    turns, reference = cohort()
    before = copy.deepcopy(turns)
    rows = join_astra(turns, reference)
    assert len(rows) == len(turns)
    assert turns == before
    for row in rows:
        text = serialize(row["target_turn"], row["preceding_turn_context"])
        assert row["session_id"] not in text
        assert "PRIVATE_REASON_SENTINEL" not in text
    reference["answers"].pop()
    with pytest.raises(ValueError, match="every target"):
        join_astra(turns, reference)


@pytest.mark.parametrize("mutate", ["source", "label", "duplicate", "predecessor", "naive_date", "role"])
def test_invalid_sources_fail_before_training(mutate):
    turns, reference = cohort()
    if mutate == "source":
        turns[0]["messages"][0]["content"] = "Changed text"
    elif mutate == "label":
        reference["answers"][0]["category"] = "unknown"
    elif mutate == "duplicate":
        reference["answers"].append(reference["answers"][0])
    elif mutate == "predecessor":
        turns[0]["previous_turn_index"] = 1
    elif mutate == "naive_date":
        turns[0]["started_at"] = "2026-01-01"
    else:
        turns[0]["messages"][0]["role"] = "tool"
    with pytest.raises(ValueError):
        join_astra(turns, reference)


def test_chronological_session_split_does_not_stratify_labels():
    turns, reference = cohort()
    rows = join_astra(turns, reference)
    splits, report = chronological_split(rows)
    assert [len(splits[s]) for s in ("train", "validation", "test")] == [14, 3, 3]
    assert {r["session_id"] for r in splits["test"]} == {"session-17", "session-18", "session-19"}
    changed = [dict(row, category="creative-media") for row in reversed(rows)]
    again, _ = chronological_split(changed)
    assert {s: [r["turn_id"] for r in part] for s, part in splits.items()} == {
        s: [r["turn_id"] for r in part] for s, part in again.items()
    }
    assert report["temporal_overlap"] == {"train_validation": False, "validation_test": False}


def test_forks_duplicates_and_old_turns_stay_in_later_group():
    turns, reference = cohort()
    rows = join_astra(turns, reference)
    rows[1]["target_turn"] = rows[18]["target_turn"] = [{"role": "user", "content": "x" * 2500}]
    # Generic short replies do not merge independent sessions.
    rows[2]["target_turn"] = rows[3]["target_turn"] = [{"role": "user", "content": "yes"}]
    splits, report = chronological_split(rows, [["session-00", "session-19"]])
    assigned = {r["session_id"]: s for s, part in splits.items() for r in part}
    assert assigned["session-00"] == assigned["session-19"] == "test"
    assert assigned["session-01"] == assigned["session-18"] == "test"
    assert report["groups"] == 18
    assert report["temporal_overlap"]["validation_test"]


def test_predecessor_text_and_empty_target_are_retained():
    turns, reference = cohort(2)
    turns[1]["original_codex_session_id"] = turns[0]["original_codex_session_id"]
    turns[1]["previous_turn_index"] = 0
    turns[1]["messages"] = []
    turns[1]["classification_input_sha256"] = source_hash([], turns[0]["messages"])
    reference["answers"][1].update(
        {k: turns[1][k] for k in ("original_codex_session_id", "classification_input_sha256")}
    )
    rows = join_astra(turns, reference)
    assert rows[1]["target_turn"] == []
    assert rows[1]["preceding_turn_context"] == turns[0]["messages"]


def test_service_contract_order_limits_and_redacted_errors():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from classifier.encoder_service import MAX_REQUEST_BYTES, create_app

    class FakeEncoder:
        metadata = {"checkpoint_sha256": "synthetic", "calibrated": False}

        def predict(self, items):
            return [
                {
                    "request_id": i["request_id"],
                    **{
                        k: v
                        for k, v in tokenize(
                            CharacterTokenizer(), i["target_turn"], i["preceding_turn_context"]
                        ).items()
                        if k != "input_ids"
                    },
                }
                for i in items
            ]

    app = create_app(FakeEncoder)
    item = {"request_id": "one", "target_turn": [], "preceding_turn_context": []}
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        response = client.post(
            "/v1/turn-classifications", json={"items": [item, dict(item, request_id="two")]}
        )
        assert [r["request_id"] for r in response.json()["results"]] == ["one", "two"]
        assert (
            response.json()["results"][0]["effective_input_sha256"]
            == tokenize(CharacterTokenizer(), [], [])["effective_input_sha256"]
        )
        assert client.post("/v1/turn-classifications", json={"items": [item, item]}).status_code == 422
        bad = dict(item, target_turn=[{"role": "tool", "content": "SENSITIVE"}])
        response = client.post("/v1/turn-classifications", json={"items": [bad]})
        assert response.status_code == 422 and "SENSITIVE" not in response.text
        assert (
            client.post("/v1/turn-classifications", content=b"x" * (MAX_REQUEST_BYTES + 1)).status_code == 413
        )
        app.state.busy = True
        assert client.post("/v1/turn-classifications", json={"items": [item]}).status_code == 429
        app.state.encoder = None
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("changed_field", ["category", "turn_id", "input_ids"])
def test_prepared_sources_can_recreate_the_frozen_dataset(tmp_path, monkeypatch, changed_field):
    from classifier.artifacts import manifest_hash, read_rows, seal, sha256, write_rows
    from prepare_turn_encoder import prepare

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=lambda *args, **kwargs: CharacterTokenizer())
        ),
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    turns, reference = cohort()
    files = {}
    for key, name, content in (
        ("turns", "turns.json", turns),
        ("reference", "astra-answers.json", reference),
    ):
        path = inputs / name
        path.write_text(json.dumps(content))
        files[key] = {"path": name, "sha256": sha256(path)}
    (inputs / "sources.json").write_text(json.dumps({"files": files}))
    model = tmp_path / "model"
    model.mkdir()
    (model / "source.json").write_text('{"revision": "synthetic"}')
    first, restored = tmp_path / "first", tmp_path / "restored"
    prepare(inputs / "sources.json", model, first)
    result = prepare(first / "sources/sources.json", model, restored, match_dataset=first)
    assert result["comparison_dataset_manifest_sha256"] == manifest_hash(first)
    for name in ("train.jsonl", "validation.jsonl", "test.jsonl", "split-assignment.json"):
        assert sha256(first / name) == sha256(restored / name)
    # A different, internally sealed comparison snapshot must fail, even if its
    # original source files are unchanged.
    changed = read_rows(first / "validation.jsonl")
    changed[0][changed_field] = {"category": "other", "turn_id": "different-turn", "input_ids": [1, 2]}[
        changed_field
    ]
    (first / "validation.jsonl").unlink()
    (first / "manifest.json").unlink()
    write_rows(first / "validation.jsonl", changed)
    seal(first, "turn-encoder-dataset")
    with pytest.raises(ValueError, match="frozen membership, labels or effective tokens"):
        prepare(inputs / "sources.json", model, tmp_path / "mismatch", match_dataset=first)

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from curation_fixtures import REFERENCE, data, prepare_curation, synthetic_embeddings
from fastapi.testclient import TestClient

from agentboard.experiments.archive import sha256
from agentboard.experiments.cli import execute
from agentboard.experiments.curation import (
    CurationStore,
    RevisionConflict,
    build_workspace,
    input_text,
)
from agentboard.experiments.curation_api import create_review_app


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    monkeypatch.setattr("agentboard.experiments.embedding_similarity.encode_inputs", synthetic_embeddings)


@pytest.fixture
def prepared(tmp_path):
    return prepare_curation(tmp_path)


def decision(store, wid, target, action="verify", **kwargs):
    return {
        "revision": store.read(wid)["revision"],
        "target": target,
        "action": action,
        "category": "coding",
        "reviewer": "Synthetic reviewer",
        "reason": "Reviewed the target",
        **kwargs,
    }


def test_reference_inference_disagreement_and_missing_are_separate():
    recipe, targets, sources = data()
    rows = build_workspace(recipe, targets, sources)["rows"]
    assert [r["comparison"] for r in rows] == [
        "disagreement",
        "agreement",
        "insufficient",
        "insufficient",
        "insufficient",
    ]
    assert [r["label"]["status"] for r in rows] == [
        "inferred",
        "inferred",
        "unlabeled",
        "unlabeled",
        "unlabeled",
    ]
    assert rows[0]["label"]["category"] == "bug-fixing"
    assert rows[2]["results"][0]["status"] == "changed_input"
    assert rows[3]["results"][0]["status"] == "invalid_result"
    assert rows[4]["results"][0]["status"] == "missing"
    assert rows[3]["results"][0]["result"]["prompt_compliance_errors"] == ["invalid"]
    assert len(sources[0]["value"]["turn_results"]) == 5


def test_configurations_and_input_validation():
    recipe, targets, sources = data()
    with pytest.raises(ValueError, match="Duplicate target"):
        build_workspace(recipe, targets + [targets[0]], sources)
    with pytest.raises(ValueError, match="Duplicate classifier"):
        build_workspace(recipe, targets, sources * 2)
    with pytest.raises(ValueError, match="Reference configuration"):
        build_workspace(
            {**recipe, "reference_configuration": {**REFERENCE, "reasoning_effort": "low"}}, targets, sources
        )
    for threshold in (float("nan"), float("inf"), -1, 0, 1.1, True):
        with pytest.raises(ValueError, match="threshold"):
            build_workspace({**recipe, "similarity_threshold": threshold}, targets, sources)
    bad = deepcopy(sources)
    bad[0]["value"]["method"] = {"taxonomy_version": "other-rubric"}
    with pytest.raises(ValueError, match="taxonomy"):
        build_workspace(recipe, targets, bad)
    targets[0]["messages"][0]["content"] = {"not": "text"}
    with pytest.raises(ValueError, match="must be text"):
        build_workspace(recipe, targets, sources)


def test_independent_results_distinguish_execution_and_ignore_invalid_labels():
    recipe, targets, sources = data()
    independent = {
        "model": "small-model",
        "reasoning_effort": "low",
        "classifications": [
            {
                **targets[0],
                "model": "small-model",
                "reasoning_effort": "low",
                "category": "research",
                "reason": "Investigation",
            }
        ],
    }
    rows = build_workspace(
        recipe, targets, sources + [{"format": "independent-turn-v1", "value": independent}]
    )["rows"]
    assert len(rows[0]["results"]) == 3
    assert rows[0]["results"][2]["configuration"]["execution"] == "independent"
    assert rows[1]["results"][2]["status"] == "missing"


def test_similarity_uses_only_current_user_inputs_and_retains_boundaries():
    recipe, targets, sources = data()
    first = build_workspace(recipe, targets, sources)
    assert len(first["pairs"]) == 3
    assert all(p["score"] == 1 for p in first["pairs"])
    for target in targets:
        target["messages"].append({"role": "assistant", "content": "A very different answer"})
        target["messages"].append({"role": "tool", "content": "Unrelated output"})
        target["previous_turn_index"] = None
    second = build_workspace(recipe, targets, sources)
    assert first["pairs"] == second["pairs"]
    assert input_text({"messages": [{"role": "user", "content": " ＦＩＸ\n  Button "}]}) == "FIX Button"
    assert (
        input_text({"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]})
        == "a\nb"
    )


def test_human_verification_reopen_persistence_and_conflict(prepared):
    store, wid, recipe = prepared
    row = store.read(wid)["rows"][0]
    original_hash = sha256(store.archive.resolve(recipe["results"][0]["reference"]))
    update = decision(store, wid, row["id"])
    store.decide(wid, update)
    reread = CurationStore(store.archive).read(wid)
    assert reread["rows"][0]["label"]["status"] == "verified"
    assert reread["rows"][0]["label"]["category"] == "coding"
    assert reread["rows"][0]["results"] == row["results"]
    with pytest.raises(RevisionConflict):
        store.decide(wid, update)
    store.decide(wid, decision(store, wid, row["id"], "reopen_label"))
    assert store.read(wid)["rows"][0]["label"] == row["label"]
    assert len(store.read(wid)["history"]) == 2
    assert sha256(store.archive.resolve(recipe["results"][0]["reference"])) == original_hash
    with pytest.raises(ValueError, match="Reviewer"):
        store.decide(wid, decision(store, wid, row["id"], reviewer=" "))
    with pytest.raises(ValueError, match="category"):
        store.decide(wid, decision(store, wid, row["id"], category="unknown"))


def test_duplicate_removal_requires_decision_and_preserves_labels(prepared):
    store, wid, _ = prepared
    value = store.read(wid)
    pair = value["pairs"][0]
    assert all(r["disposition"] == "active" for r in value["rows"])
    store.decide(wid, decision(store, wid, pair["id"], "keep_both"))
    assert all(r["disposition"] == "active" for r in store.read(wid)["rows"])
    store.decide(wid, decision(store, wid, pair["id"], "remove_left"))
    removed = next(r for r in store.read(wid)["rows"] if r["id"] == pair["left"])
    assert removed["disposition"] == "removed_duplicate"
    assert removed["removal"]["duplicate_of"] == pair["right"]
    assert removed["label"] == next(r for r in value["rows"] if r["id"] == pair["left"])["label"]
    with pytest.raises(ValueError, match="Restore removed"):
        store.decide(wid, decision(store, wid, pair["id"], "remove_right"))
    other_pair = next(
        p
        for p in value["pairs"]
        if pair["right"] in (p["left"], p["right"]) and pair["left"] not in (p["left"], p["right"])
    )
    action = "remove_left" if other_pair["left"] == pair["right"] else "remove_right"
    with pytest.raises(ValueError, match="dependent"):
        store.decide(wid, decision(store, wid, other_pair["id"], action))
    store.decide(wid, decision(store, wid, pair["left"], "restore"))
    assert all(r["disposition"] == "active" for r in store.read(wid)["rows"])
    assert store.read(wid)["pairs"][0]["decision"] == "pending"
    assert len(store.read(wid)["history"]) == 3


def test_export_mixed_states_removed_rows_and_source_pins(prepared):
    store, wid, recipe = prepared
    value = store.read(wid)
    store.decide(wid, decision(store, wid, value["rows"][0]["id"]))
    store.decide(wid, decision(store, wid, value["pairs"][0]["id"], "remove_right"))
    with pytest.raises(RevisionConflict):
        store.export(wid, 0)
    result = store.export(wid, 2)
    root = store.archive.locate(result["id"])
    all_rows = json.loads((root / "outputs/all-turns.json").read_bytes())
    active = json.loads((root / "outputs/active-turns.json").read_bytes())
    assert len(all_rows) == 5 and len(active) == 4
    assert {r["label"]["status"] for r in all_rows} == {"inferred", "verified", "unlabeled"}
    assert sum(r["disposition"] == "removed_duplicate" for r in all_rows) == 1
    assert store.archive.inspect(result["id"])["inputs"] == [
        recipe["dataset"],
        recipe["results"][0]["reference"],
    ]
    assert store.archive.verify(result["id"])["verified"]
    assert store.export(wid, 2)["id"] != result["id"]
    assert store.read(wid)["revision"] == 2


def test_changed_dataset_gets_fresh_workspace_and_corrupt_input_rejected(prepared):
    store, wid, recipe = prepared
    row = store.read(wid)["rows"][0]
    store.decide(wid, decision(store, wid, row["id"]))
    fresh = store.create(recipe)["id"]
    assert store.read(fresh)["rows"][0]["label"]["status"] == "inferred"
    bad = deepcopy(recipe)
    bad["dataset"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        store.create(bad)
    assert len(store.list()) == 2


def test_api_review_context_validation_and_export(prepared):
    store, wid, _ = prepared
    with TestClient(create_review_app(store, wid)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/curation.js").status_code == 200
        rows = client.get("/api/turns").json()
        assert rows["total"] == 1
        row = client.get("/api/turns/" + rows["items"][0]["id"]).json()
        assert row["context_status"] == "not_required"
        second = store.read(wid)["rows"][1]
        assert client.get("/api/turns/" + second["id"]).json()["context"]["index"] == 0
        update = decision(store, wid, row["id"])
        assert (
            client.post(
                "/api/decisions", json=update, headers={"Origin": "https://untrusted.invalid"}
            ).status_code
            == 403
        )
        assert client.post("/api/decisions", json={**update, "extra": True}).status_code == 422
        assert client.post("/api/decisions", json=update).status_code == 200
        assert client.post("/api/decisions", json=update).status_code == 409
        assert client.get("/api/turns").json()["total"] == 0
        assert client.get("/api/turns?queue=verified").json()["total"] == 1
        assert client.get("/api/pairs?limit=1").json()["total"] == 3
        assert client.get("/api/turns?limit=0").status_code == 422
        assert client.get("/api/turns/unknown").status_code == 404
        assert client.get("/api/workspace", headers={"Host": "untrusted.invalid"}).status_code == 400
        exported = client.post("/api/export", json={"revision": 1}).json()
        download = client.get(f"/api/exports/{exported['id']}/all")
        assert len(download.json()) == 5
        assert "attachment" in download.headers["content-disposition"]


def test_cli_curation_isolated_from_trace_runtime(prepared, monkeypatch, capsys):
    store, wid, _ = prepared

    def forbidden(*args, **kwargs):
        raise AssertionError("Trace runtime constructed")

    monkeypatch.setattr("agentboard.runtime.Runtime", forbidden)
    args = SimpleNamespace(
        data_home=store.archive.home,
        archive_config=None,
        mode="filesystem",
        experiment_command="curation-export",
        id=wid,
        revision=0,
    )
    execute(args)
    assert json.loads(capsys.readouterr().out)["curation"]["turns"] == 5


def test_lock_contention_and_failed_writes_preserve_saved_decisions(prepared, monkeypatch):
    from agentboard.experiments.archive import locked

    store, wid, _ = prepared
    before = store.path(wid).read_bytes()
    update = decision(store, wid, store.read(wid)["rows"][0]["id"])
    with locked(store.root / (wid + ".lock")):
        with pytest.raises(ValueError, match="lock"):
            store.decide(wid, update)

    def fail_write(*args, **kwargs):
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr("agentboard.experiments.curation.atomic_json", fail_write)
    with TestClient(create_review_app(store, wid)) as client:
        assert client.post("/api/decisions", json=update).status_code == 503
    assert store.path(wid).read_bytes() == before


def test_prior_workspace_formats_are_reported_unsupported_without_rewriting(prepared):
    store, wid, _ = prepared
    value = store.read(wid)
    value["format_version"] = "dataset-curation-v1"
    store.path(wid).write_text(json.dumps(value))
    before = store.path(wid).read_bytes()
    with pytest.raises(ValueError, match="Unsupported"):
        store.read(wid)
    assert store.list() == [{"id": wid, "error": "Unsupported or invalid workspace; create a new workspace"}]
    assert store.path(wid).read_bytes() == before

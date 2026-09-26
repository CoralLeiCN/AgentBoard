"""Synthetic offline evidence only; no pretrained weights, providers or real sessions."""

import copy
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import train_bert
import train_lightgbm
from classifier import data as classifier_data
from classifier import models
from classifier.artifacts import read_json, read_rows, verify, write_json, write_rows
from classifier.cli import record_workspace
from classifier.data import (
    connected_groups,
    import_gpt,
    prepare,
    split_rows,
    turn_inputs,
)
from classifier.evaluation import compare, metrics
from classifier.taxonomy import CATEGORIES
from train_bert import BertConfig
from train_lightgbm import LightGBMConfig


def synthetic_turns(n=24):
    return [
        {
            "index": i,
            "original_codex_session_id": f"session-{i}",
            "original_codex_turn_id": f"turn-{i}",
            "classification_input_sha256": f"{i:064x}",
            "previous_turn_index": None,
            "messages": [{"role": "user", "content": f"{'write' if i % 2 else 'code'} synthetic task {i}"}],
            "unknown_source_field": {"preserve": True},
        }
        for i in range(n)
    ]


def fixture_dataset(tmp_path):
    turns = tmp_path / "turns.json"
    labels = tmp_path / "labels.jsonl"
    write_json(turns, synthetic_turns())
    rows = turn_inputs(read_rows(turns))
    write_rows(
        labels,
        [
            {
                **r,
                "category": "coding" if i % 2 else "writing",
                "label_source": {"kind": "human", "name": "synthetic-reviewer"},
            }
            for i, r in enumerate(rows)
        ],
    )
    output = tmp_path / "dataset"
    prepare(turns, labels, output)
    return output


def test_preparation_is_reproducible_grouped_complete_and_lossless(tmp_path):
    data = fixture_dataset(tmp_path)
    original = read_rows(tmp_path / "turns.json")
    original.reverse()
    write_json(tmp_path / "reverse.json", original)
    other = tmp_path / "second"
    prepare(tmp_path / "reverse.json", tmp_path / "labels.jsonl", other)
    meta = verify(data, "dataset")
    assert meta["labeled_count"] == 24 and meta["pending_count"] == 0
    assert meta["warnings"]
    assert (data / "source-turns.json").read_bytes() == (tmp_path / "turns.json").read_bytes()
    parts = [read_rows(data / f"{name}.jsonl") for name in ("train", "validation", "test")]
    assert sum(map(len, parts)) == 24
    for name in ("train", "validation", "test"):
        assert (data / f"{name}.jsonl").read_bytes() == (other / f"{name}.jsonl").read_bytes()
    assert not set(r["session_id"] for r in parts[0]) & set(r["session_id"] for r in parts[2])
    with pytest.raises(FileExistsError):
        prepare(tmp_path / "turns.json", tmp_path / "labels.jsonl", data)
    (data / "test.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(data, "dataset")


def test_context_and_transitive_duplicate_family_groups():
    turns = synthetic_turns(6)
    turns[1]["previous_turn_index"] = 0
    turns[2]["messages"] = turns[0]["messages"]
    turns[3]["group_id"] = "family"
    turns[4]["group_id"] = "family"
    rows = turn_inputs(turns)
    groups = connected_groups(rows)
    assert sorted(len(g) for g in groups) == [1, 2, 3]
    text = json.loads(rows[1]["text"])
    assert text["previous"] == [{"role": "user", "content": "code synthetic task 0"}]
    assert "unknown_source_field" not in rows[0]["text"]
    turns[0]["previous_turn_index"] = 999
    with pytest.raises(ValueError, match="predecessor"):
        turn_inputs(turns)


def test_unlabeled_bridges_still_prevent_leakage():
    turns = synthetic_turns(20)
    turns[0]["group_id"] = "a"
    turns[1]["group_id"] = "a"
    turns[1]["previous_turn_index"] = 2
    inputs = turn_inputs(turns)
    rows = [
        {**r, "category": "coding" if i % 2 else "writing"}
        for i, r in enumerate(inputs)
        if r["turn_id"] != "turn-1"
    ]
    parts = split_rows(rows, grouping_rows=inputs)
    owner = {r["turn_id"]: name for name, part in parts.items() for r in part}
    assert owner["turn-0"] == owner["turn-2"]


@pytest.mark.parametrize("ratios", [(1, 0, 0), (0.7, 0.2, 0.2), (float("nan"), 0.2, 0.2)])
def test_invalid_splits_fail(ratios):
    with pytest.raises(ValueError, match="ratios"):
        split_rows([], ratios=ratios)


def test_small_eight_class_dataset_keeps_coverage_with_flexible_splits(tmp_path):
    turns = tmp_path / "turns.json"
    labels = tmp_path / "labels.jsonl"
    write_json(turns, synthetic_turns(10))
    inputs = turn_inputs(read_rows(turns))
    categories = [*CATEGORIES, "coding", "writing"]
    rows = [
        {**row, "category": category, "label_source": {"kind": "human", "name": "synthetic-reviewer"}}
        for row, category in zip(inputs, categories)
    ]
    write_rows(labels, rows)
    output = tmp_path / "dataset"
    meta = prepare(turns, labels, output)
    parts = {name: read_rows(output / f"{name}.jsonl") for name in ("train", "validation", "test")}
    assert {name: len(part) for name, part in parts.items()} == {"train": 8, "validation": 1, "test": 1}
    assert {row["category"] for row in parts["train"]} == set(CATEGORIES)
    assert sorted(row["turn_id"] for part in parts.values() for row in part) == sorted(
        r["turn_id"] for r in rows
    )
    assert len({row["session_id"] for part in parts.values() for row in part}) == 10
    assert meta["requested_ratios"] == [0.7, 0.15, 0.15]
    assert meta["actual_ratios"] == {"train": 0.8, "validation": 0.1, "test": 0.1}
    assert meta["split_algorithm"] == "coverage-adaptive-groups-v2"
    assert split_rows(list(reversed(rows))) == parts


def test_split_rejects_genuinely_impossible_training_coverage():
    rows = [
        {**row, "category": category} for row, category in zip(turn_inputs(synthetic_turns(8)), CATEGORIES)
    ]
    with pytest.raises(ValueError, match="isolated splits"):
        split_rows(rows)


def test_split_checks_feasibility_when_sampled_orders_miss_valid_partitions(monkeypatch):
    inputs = turn_inputs(synthetic_turns(10))
    rows = [
        {**row, "category": category, "session_id": "session-0" if i < 8 else f"session-{i}"}
        for i, (row, category) in enumerate(zip(inputs, [*CATEGORIES, "coding", "writing"]))
    ]
    # Every sampled ordering reserves the only group containing all eight classes
    # for a holdout. A deterministic feasibility check must still find the solution.
    monkeypatch.setattr(classifier_data.random.Random, "shuffle", lambda self, order: None)
    parts = split_rows(rows)
    assert {row["category"] for row in parts["train"]} == set(CATEGORIES)
    assert {row["session_id"] for row in parts["train"]} == {"session-0"}
    assert len(parts["validation"]) == len(parts["test"]) == 1


def test_impossible_splits_and_conflicting_labels_fail(tmp_path):
    data = fixture_dataset(tmp_path)
    with pytest.raises(ValueError, match="three independent"):
        split_rows(read_rows(data / "test.jsonl")[:2])
    labels = read_rows(tmp_path / "labels.jsonl")
    labels[0]["input_sha256"] = "f" * 64
    write_rows(tmp_path / "stale.jsonl", labels)
    with pytest.raises(ValueError, match="Stale"):
        prepare(tmp_path / "turns.json", tmp_path / "stale.jsonl", tmp_path / "bad")
    write_rows(tmp_path / "duplicate.jsonl", labels + [labels[0]])
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(tmp_path / "turns.json", tmp_path / "duplicate.jsonl", tmp_path / "bad")


def test_pseudo_labels_are_explicit_and_pending_rows_are_retained(tmp_path):
    fixture_dataset(tmp_path)
    labels = read_rows(tmp_path / "labels.jsonl")
    for row in labels:
        row["label_source"] = {"kind": "gpt", "name": "synthetic-teacher"}
    labels.pop()
    labels[0]["category"] = "unsupported"
    write_rows(tmp_path / "pseudo.jsonl", labels)
    with pytest.raises(ValueError, match="allow-gpt-labels"):
        prepare(tmp_path / "turns.json", tmp_path / "pseudo.jsonl", tmp_path / "pseudo")
    meta = prepare(
        tmp_path / "turns.json", tmp_path / "pseudo.jsonl", tmp_path / "pseudo", allow_gpt_labels=True
    )
    assert meta["labeled_count"] == 22 and meta["pending_count"] == 2
    assert {r["status"] for r in read_rows(tmp_path / "pseudo/pending.jsonl")} == {
        "invalid_label",
        "missing_label",
    }


def gpt_fixture():
    turn = synthetic_turns(1)[0]
    return {
        "model": "synthetic-gpt",
        "reasoning_effort": "low",
        "classifications": [
            {
                **turn,
                "model": "synthetic-gpt",
                "reasoning_effort": "low",
                "category": "coding",
                "reason": "Synthetic",
            }
        ],
    }


def test_gpt_adapters_preserve_invalid_and_reject_mixed_configuration():
    value = gpt_fixture()
    rows = import_gpt(value)
    assert rows[0]["label_source"]["kind"] == "gpt"
    value["classifications"][0]["prompt_compliance_errors"] = ["synthetic violation"]
    assert import_gpt(value)[0]["error"]
    value["classifications"][0]["reasoning_effort"] = "high"
    with pytest.raises(ValueError, match="Mixed"):
        import_gpt(value)
    row = gpt_fixture()["classifications"][0]
    batched = {
        "experiment": {"models": [{"model": "synthetic-gpt", "reasoning_effort": "low"}]},
        "turn_results": [{**row, "classifications": {"synthetic-gpt": row}}],
    }
    assert import_gpt(batched, model="synthetic-gpt")[0]["category"] == "coding"
    with pytest.raises(ValueError, match="Select exactly"):
        import_gpt(batched)


def test_comparison_preserves_denominators_and_uses_common_subset(tmp_path):
    data = fixture_dataset(tmp_path)
    targets = read_rows(data / "test.jsonl")
    full = [{**r, "configuration": {"model": "bert"}} for r in targets]
    partial = [{**r, "configuration": {"model": "gpt"}} for r in targets[:-1]]
    partial[0]["input_sha256"] = "f" * 64
    report = compare(targets, {"bert": full, "gpt": partial})
    assert report["requested"] == len(targets) and report["paired_count"] == len(targets) - 2
    assert report["classifiers"]["gpt"]["coverage"] == {
        "changed_input": 1,
        "available": len(targets) - 2,
        "missing": 1,
    }
    assert report["classifiers"]["bert"]["paired_metrics"]["accuracy"] == 1
    assert report["interpretation"] == "accuracy"
    pseudo = [{**r, "label_source": {"kind": "gpt", "name": "teacher"}} for r in targets]
    assert compare(pseudo, {"bert": full})["interpretation"] == "reference_agreement"
    assert compare(targets, {"empty": []})["classifiers"]["empty"]["paired_metrics"]["accuracy"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        compare(targets, {"duplicates": full + [full[0]]})
    with pytest.raises(ValueError, match="one explicit configuration"):
        compare(targets, {"mixed": [partial[0], *full[1:]]})


def test_metrics_known_confusion_and_invalid_probabilities(tmp_path):
    result = metrics(["coding", "coding", "writing"], ["coding", "writing", "writing"])
    assert result["accuracy"] == 2 / 3
    assert result["macro_f1"] == pytest.approx((2 / 3 + 2 / 3) / 8)
    data = fixture_dataset(tmp_path)
    targets = read_rows(data / "test.jsonl")
    rows = [
        {**r, "configuration": {"model": "bad"}, "probabilities": dict.fromkeys(CATEGORIES, float("nan"))}
        for r in targets
    ]
    assert compare(targets, {"bad": rows})["classifiers"]["bad"]["coverage"] == {"invalid": len(targets)}


def test_cli_is_offline_and_does_not_create_trace_database(tmp_path):
    data = fixture_dataset(tmp_path)
    rows = [{**r, "configuration": {"model": "synthetic"}} for r in read_rows(data / "test.jsonl")]
    write_rows(tmp_path / "predictions.jsonl", rows)
    command = [sys.executable, "-S", "-m", "classifier"]
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        command
        + [
            "compare",
            "--dataset",
            str(data),
            "--prediction",
            f"dummy={tmp_path / 'predictions.jsonl'}",
            "--output",
            str(tmp_path / "report.json"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob("*.db"))
    assert read_json(tmp_path / "report.json")["paired_count"] == len(rows)
    assert (
        subprocess.run(command + ["--help"], env=environment, cwd=tmp_path, capture_output=True).returncode
        == 0
    )
    result = subprocess.run(
        command
        + [
            "prepare",
            "--turns",
            str(tmp_path / "turns.json"),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--output",
            str(tmp_path / "from-cli"),
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "from-cli/test.jsonl").read_bytes() == (data / "test.jsonl").read_bytes()


def test_record_complete_evidence_and_restore(tmp_path):
    Archive = pytest.importorskip("agentboard.experiments").Archive
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_json(workspace / "synthetic.json", {"synthetic": True})
    dataset_path = fixture_dataset(workspace)
    targets = read_rows(dataset_path / "test.jsonl")
    predictions = [{**r, "configuration": {"model": "synthetic"}} for r in targets]
    write_rows(workspace / "predictions.jsonl", predictions)
    expected = compare(targets, {"synthetic": predictions})
    options = SimpleNamespace(
        directory=workspace,
        data_home=tmp_path / "archive",
        archive_config=None,
        references=None,
        project="synthetic",
        experiment="classifiers",
    )
    before_options = vars(options).copy()
    result = record_workspace(options)
    assert vars(options) == before_options
    archive = Archive(tmp_path / "archive")
    restored = tmp_path / "restored"
    shutil.copytree(archive.home, restored)
    shutil.rmtree(archive.home)
    shutil.rmtree(workspace)
    archive = Archive(restored)
    assert archive.verify(result["id"])["verified"]
    bundle = archive.locate(result["id"])
    assert read_json(bundle / "workspace/synthetic.json") == {"synthetic": True}
    assert (bundle / "source/uv.lock").is_file()
    assert (bundle / "source/classifier/cli.py").is_file()
    assert (bundle / "source/train_bert.py").is_file()
    assert (bundle / "source/train_lightgbm.py").is_file()
    assert not (bundle / "source/backend").exists()
    assert not (bundle / "source/frontend").exists()
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "classifier",
            "compare",
            "--dataset",
            str(bundle / "workspace/dataset"),
            "--prediction",
            f"synthetic={bundle / 'workspace/predictions.jsonl'}",
            "--output",
            str(tmp_path / "regenerated.json"),
        ],
        env={**os.environ, "PYTHONPATH": str(bundle / "source")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    regenerated = read_json(tmp_path / "regenerated.json")
    regenerated.pop("dataset_manifest_sha256")
    assert regenerated == expected


def test_invalid_hyperparameters_rejected_before_optional_imports(tmp_path):
    for call in (
        lambda: train_bert.train(BertConfig(batch_size=0)),
        lambda: train_lightgbm.embed(LightGBMConfig(embedding_max_length=-1)),
        lambda: train_lightgbm.train(LightGBMConfig(parameters={"learning_rate": float("nan")})),
    ):
        with pytest.raises(ValueError, match="positive"):
            call()
    with pytest.raises(ValueError, match="local model"):
        models.local_model(tmp_path / "absent")


def test_local_dummy_bert_embedding_lightgbm_train_reload(tmp_path, monkeypatch):
    """Random tiny BERT, synthetic labels; exercise real libraries without a model service."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "1")
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    st = pytest.importorskip("sentence_transformers")
    pytest.importorskip("lightgbm")
    torch.set_num_threads(1)
    data = fixture_dataset(tmp_path)
    source = tmp_path / "tiny-bert"
    source.mkdir()
    (source / "vocab.txt").write_text(
        "\n".join(
            ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "code", "write", "synthetic", "task", "user"]
        )
    )
    tokenizer = transformers.BertTokenizerFast(vocab_file=str(source / "vocab.txt"), model_max_length=64)
    tokenizer.save_pretrained(source)
    config = transformers.BertConfig(
        vocab_size=len(tokenizer),
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        max_position_embeddings=64,
    )
    transformers.BertModel(config).save_pretrained(source)
    accessed = []
    original_reader = models.read_rows

    def traced(path):
        accessed.append(Path(path).name)
        return original_reader(path)

    monkeypatch.setattr(models, "read_rows", traced)
    bert = tmp_path / "bert"
    bert_config = BertConfig(
        dataset=data,
        model=source,
        output=bert,
        epochs=2,
        max_length=24,
        seed=17,
        batch_size=4,
        gradient_clip_norm=0.25,
        truncation_side="left",
        optimizer_params={"lr": 1e-4, "weight_decay": 0.04, "eps": 1e-7},
        model_config={"hidden_dropout_prob": 0.0},
    )
    with pytest.raises(ValueError, match="Unknown model_config"):
        train_bert.train(replace(bert_config, model_config={"misspelled_dropout": 0.1}))
    assert not bert.exists()
    metadata = train_bert.train(bert_config)
    assert metadata["run_config"]["model"] == str(source)
    assert metadata["run_config"]["seed"] == 17
    assert metadata["configuration"]["optimizer_params"]["lr"] == 1e-4
    assert metadata["configuration"]["optimizer_params"]["weight_decay"] == 0.04
    assert metadata["configuration"]["gradient_clip_norm"] == 0.25
    assert read_json(bert / "model/config.json")["hidden_dropout_prob"] == 0.0
    with pytest.raises(ValueError, match="already exists"):
        train_bert.train(bert_config)
    assert "test.jsonl" not in accessed
    assert metadata["best_epoch"] in (1, 2) and len(metadata["history"]) == 2
    assert metadata["truncation"]["train"]["truncated"] > 0
    test_rows = read_rows(data / "test.jsonl")
    original_test_rows = copy.deepcopy(test_rows)
    result = models.predict(bert, test_rows, batch_size=2)
    predictions, timing = result.predictions, result.metadata
    assert timing["count"] == len(test_rows)
    assert all(sum(r["probabilities"].values()) == pytest.approx(1) for r in predictions)
    second = models.predict(bert, test_rows, batch_size=1).predictions
    for a, b in zip(predictions, second):
        assert a["probabilities"] == pytest.approx(b["probabilities"], abs=1e-6)
    encoder_path = tmp_path / "encoder"
    from sentence_transformers.sentence_transformer.modules import Pooling, Transformer

    transformer = Transformer(str(source), max_seq_length=32, model_kwargs={"local_files_only": True})
    encoder = st.SentenceTransformer(modules=[transformer, Pooling(8)])
    encoder.save(str(encoder_path), create_model_card=False)
    vectors = tmp_path / "vectors"
    gbm_config = LightGBMConfig(
        dataset=data,
        embedding_model=encoder_path,
        embeddings=vectors,
        output=tmp_path / "gbm",
        embedding_max_length=24,
        embedding_batch_size=3,
        embedding_prompt="synthetic ",
        normalize_embeddings=False,
        truncation_side="left",
        rounds=5,
        patience=2,
    )
    gbm_config.parameters.update(learning_rate=0.07, num_leaves=7, min_data_in_leaf=1, lambda_l2=0.6, seed=23)
    vector_meta = train_lightgbm.embed(gbm_config)
    gbm_config = replace(gbm_config, reuse_embeddings=True)
    assert vector_meta["splits"]["train"]["shape"][1] == 8
    incompatible = tmp_path / "other-dataset"
    shutil.copytree(data, incompatible)
    changed = read_json(incompatible / "manifest.json")
    changed["seed"] = 999
    (incompatible / "manifest.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="different dataset"):
        train_lightgbm.train(replace(gbm_config, dataset=incompatible, output=tmp_path / "bad-gbm"))
    original_vectors = (vectors / "train.npy").read_bytes()
    (vectors / "train.npy").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        train_lightgbm.train(replace(gbm_config, output=tmp_path / "bad-gbm"))
    (vectors / "train.npy").write_bytes(original_vectors)
    accessed.clear()
    gbm = tmp_path / "gbm"
    with pytest.raises(ValueError, match="cache settings"):
        train_lightgbm.train(replace(gbm_config, embedding_prompt="changed "))
    metadata = train_lightgbm.train(gbm_config)
    assert metadata["run_config"]["embedding_prompt"] == "synthetic "
    assert metadata["configuration"]["lambda_l2"] == 0.6
    assert metadata["configuration"]["num_leaves"] == 7
    assert metadata["configuration"]["seed"] == 23
    assert metadata["configuration"]["normalize_embeddings"] is False
    import lightgbm as lgb

    saved_gbm = lgb.Booster(model_file=str(gbm / "model.txt"))
    assert saved_gbm.params["lambda_l2"] == pytest.approx(0.6)
    assert saved_gbm.params["learning_rate"] == pytest.approx(0.07)
    # The one-script default creates embeddings and fits the classifier together.
    fresh_config = replace(
        gbm_config,
        reuse_embeddings=False,
        embeddings=tmp_path / "fresh-vectors",
        output=tmp_path / "fresh-gbm",
    )
    fresh_metadata = train_lightgbm.train(fresh_config)
    assert fresh_metadata["embeddings_manifest_sha256"]
    assert (fresh_config.embeddings / "train.npy").is_file()
    assert "test.jsonl" not in accessed
    assert metadata["best_iteration"] > 0
    unselected = replace(gbm_config, output=tmp_path / "unselected")
    with monkeypatch.context() as patch:
        patch.setattr(lgb, "train", lambda *args, **kwargs: SimpleNamespace(best_iteration=0))
        with pytest.raises(ValueError, match="did not select a validation best iteration"):
            train_lightgbm.train(unselected)
    assert not (unselected.output / "manifest.json").exists()
    assert not (unselected.output / "model.txt").exists()
    import numpy as np

    expected_probs = saved_gbm.predict(np.load(vectors / "test.npy", allow_pickle=False))
    # Restore/inference works without either source model or vector cache.
    shutil.rmtree(source)
    shutil.rmtree(encoder_path)
    shutil.rmtree(vectors)
    predictions = models.predict(gbm, test_rows).predictions
    assert len(predictions) == len(test_rows)
    for prediction, expected_values in zip(predictions, expected_probs):
        assert list(prediction["probabilities"].values()) == pytest.approx(expected_values)
    assert all(set(r["probabilities"]) == set(CATEGORIES) for r in predictions)
    assert compare(test_rows, {"bert": second, "lightgbm": predictions})["paired_count"] == len(test_rows)
    assert test_rows == original_test_rows
    bad = copy.deepcopy(test_rows)
    bad[0]["text"] = "changed"
    with pytest.raises(ValueError, match="text hash"):
        models.predict(gbm, bad)


def test_artifacts_reject_symlinks_without_archive_dependency(tmp_path):
    from classifier.artifacts import inventory

    (tmp_path / "source").write_bytes(b"synthetic")
    (tmp_path / "link").symlink_to(tmp_path / "source")
    with pytest.raises(ValueError, match="Symlink"):
        inventory(tmp_path)


def test_training_scripts_import_without_ml_or_side_effects(tmp_path):
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import train_bert, train_lightgbm; "
            "a = train_bert.BertConfig(); b = train_bert.BertConfig(); "
            "a.optimizer_params['lr'] = 0.001; assert b.optimizer_params['lr'] == 2e-5; "
            "a = train_lightgbm.LightGBMConfig(); b = train_lightgbm.LightGBMConfig(); "
            "a.parameters['num_leaves'] = 3; assert b.parameters['num_leaves'] == 15",
        ],
        env={**os.environ, "PYTHONPATH": str(project)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
    result = subprocess.run(
        [sys.executable, "-S", "-m", "classifier", "--help"],
        env={**os.environ, "PYTHONPATH": str(project)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "train-bert" not in result.stdout and "train-lightgbm" not in result.stdout
    for name in ("train_bert.py", "train_lightgbm.py"):
        result = subprocess.run(
            [sys.executable, "-S", str(project / name), "--epochs", "9"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and "does not accept command-line parameters" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_config_rejects_contract_overrides_and_fractional_counts():
    for config in (
        BertConfig(epochs=1.5),
        BertConfig(model_config={"num_labels": 2}),
        BertConfig(truncation_side="invalid"),
    ):
        with pytest.raises(ValueError):
            train_bert.train(config)
    for config in (LightGBMConfig(rounds=1.5), LightGBMConfig(parameters={"objective": "binary"})):
        with pytest.raises(ValueError):
            train_lightgbm.train(config)


@pytest.mark.parametrize("key", ["boosting_type", "boosting", "boost"])
@pytest.mark.parametrize("value", ["dart", "DART"])
def test_dart_rejected_before_loading_data_or_embeddings(tmp_path, monkeypatch, key, value):
    def unexpected_work(*args, **kwargs):
        pytest.fail("DART must fail before data loading, embedding work or optional imports")

    config = LightGBMConfig(output=tmp_path / "model", embeddings=tmp_path / "vectors")
    config.parameters[key] = value
    monkeypatch.setattr(train_lightgbm, "dataset", unexpected_work)
    monkeypatch.setattr(train_lightgbm, "training_rows", unexpected_work)
    for entrypoint in (train_lightgbm.train, train_lightgbm.embed):
        with pytest.raises(ValueError, match="DART.*early stopping"):
            entrypoint(config)
    assert not config.output.exists() and not config.embeddings.exists()

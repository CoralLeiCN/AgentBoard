"""Synthetic vectors/tokenizers only: no live model invocation or downloads."""

import json
import sys
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agentboard.experiments import Archive
from agentboard.experiments.curation import VERSION as CURATION_VERSION
from agentboard.experiments.curation import CurationStore, compare_inputs
from agentboard.experiments.embedding_similarity import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    VERSION,
    EmbeddingResult,
    cosine_scores,
    embedding_config,
    encode_inputs,
    fit_chunks,
    load_encoder,
    unit_vector,
)


class FakeEncoder:
    max_seq_length = 12

    def __init__(self):
        self.batches = []

    def tokenizer(self, text, **kwargs):
        assert kwargs["truncation"] is False
        return {"input_ids": [0, *range(len(text)), 1]}

    def encode(self, batch, **kwargs):
        assert kwargs["normalize_embeddings"] and kwargs["prompt"] == ""
        assert kwargs["precision"] == "float32"
        assert all(len(text) + 2 <= self.max_seq_length for text in batch)
        self.batches.append(batch)
        return [[len(text), sum(ch == "a" for ch in text) + 1] for text in batch]


def test_long_inputs_fit_without_losing_characters_and_batching_is_bounded(monkeypatch):
    encoder = FakeEncoder()
    text = "A longer input includes ALL of its final characters!"
    parts = fit_chunks(text, encoder.tokenizer, encoder.max_seq_length)
    assert "".join(chunk for chunk, _ in parts) == text
    assert len(parts) > 1 and all(weight <= 12 for _, weight in parts)
    monkeypatch.setattr(
        "agentboard.experiments.embedding_similarity.load_encoder", lambda config: (encoder, {"fake": "1"})
    )
    result = encode_inputs({"a": text, "b": "short"}, embedding_config({"embedding_batch_size": 2}))
    vectors, stats, metadata = result.vectors, result.inputs, result.metadata
    assert len(vectors["a"]) == 2
    assert sum(x * x for x in vectors["a"]) == pytest.approx(1)
    assert stats["a"]["chunks"] == len(parts) and stats["a"]["truncated"] is False
    assert stats["b"]["chunks"] == 1
    assert all(len(batch) <= 2 for batch in encoder.batches)
    assert "".join(x for batch in encoder.batches for x in batch) == text + "short"
    assert metadata["encode_batches"] == len(encoder.batches)
    assert metadata["revision"] == DEFAULT_REVISION


def test_cosine_geometry_order_and_invalid_vectors():
    scores = cosine_scores({"a": [1, 0], "b": [3, 4], "c": [0, 2], "d": [-1, 0]}, 0.6)
    assert scores == [("a", "b", 0.6), ("b", "c", 0.8)]
    assert cosine_scores({"b": [6, 8], "a": [3, 4]}, 1) == [("a", "b", 1.0)]
    assert cosine_scores({}, 0.85) == []
    for values in ([], [0, 0], [float("nan"), 1], [float("inf"), 1], ["bad", 1]):
        with pytest.raises(ValueError, match="Embedding"):
            unit_vector(values)
    with pytest.raises(ValueError, match="dimensions"):
        cosine_scores({"a": [1, 0], "b": [1, 0, 0]}, 0.8)


def test_loader_pins_local_model_and_disables_remote_code(monkeypatch):
    calls = []
    model = object()

    def constructor(*args, **kwargs):
        calls.append((args, kwargs))
        return model

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor)
    )
    monkeypatch.setattr("agentboard.experiments.embedding_similarity.version", lambda name: "test")
    returned, _ = load_encoder(embedding_config({}))
    assert returned is model
    assert calls == [
        (
            (DEFAULT_MODEL,),
            {
                "revision": DEFAULT_REVISION,
                "device": "cpu",
                "local_files_only": True,
                "trust_remote_code": False,
            },
        )
    ]
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(ValueError, match="optional embeddings extra"):
        load_encoder(embedding_config({}))


def test_config_rejects_moving_models_and_invalid_batch_sizes():
    for recipe in (
        {"embedding_revision": "main"},
        {"embedding_model": "../model"},
        {"embedding_model": "org/different-model"},
        {"embedding_batch_size": 0},
        {"embedding_batch_size": True},
    ):
        with pytest.raises(ValueError):
            embedding_config(recipe)
    assert (
        embedding_config({"embedding_model": "org/model", "embedding_revision": "a" * 40}).revision
        == "a" * 40
    )


def test_cosine_suggestions_keep_only_user_input_and_preserve_case(monkeypatch):
    seen = []

    def encode(inputs, config):
        seen.append(inputs)
        return EmbeddingResult(
            {key: [1, 0] for key in inputs},
            {
                key: {"chunks": 1, "input_chars": len(text), "truncated": False}
                for key, text in inputs.items()
            },
            {"version": VERSION, "method": "embedding-cosine", "metric": "cosine", **asdict(config)},
        )

    monkeypatch.setattr("agentboard.experiments.embedding_similarity.encode_inputs", encode)
    rows = [
        {"id": key, "target": {"messages": messages}}
        for key, messages in [
            (
                "a",
                [
                    {"role": "user", "content": " Ａ  Case\nSensitive input "},
                    {"role": "assistant", "content": "Never embed this answer"},
                ],
            ),
            (
                "b",
                [
                    {"role": "user", "content": "A paraphrase"},
                    {"role": "tool", "content": "Never embed tools"},
                ],
            ),
            (
                "empty",
                [
                    {"role": "user", "content": "  "},
                    {"role": "assistant", "content": "Never use an answer as fallback"},
                ],
            ),
        ]
    ]
    before = deepcopy(rows)
    comparison = compare_inputs(rows, embedding_config({}))
    pairs, metadata, vectors = comparison.pairs, comparison.metadata, comparison.vectors
    assert rows == before
    assert seen == [{"a": "A Case Sensitive input", "b": "A paraphrase"}]
    assert [(p["left"], p["right"], p["score"], p["decision"]) for p in pairs] == [("a", "b", 1, "pending")]
    assert metadata["model"] == DEFAULT_MODEL and metadata["embedded_inputs"] == 2
    assert (
        comparison.annotations["a"]["embedding"]["vector_sha256"]
        and comparison.annotations["empty"]["normalized_input_sha256"] is None
    )
    assert set(vectors) == {"a", "b"}
    with pytest.raises(ValueError, match="No nonempty user inputs"):
        compare_inputs([rows[2]], embedding_config({}))
    with pytest.raises(ValueError, match="similarity_method was removed"):
        embedding_config({"similarity_method": "jaccard"})


def test_invalid_embedding_counts_fail_without_substitution(monkeypatch):
    encoder = FakeEncoder()
    encoder.encode = lambda *args, **kwargs: []
    monkeypatch.setattr(
        "agentboard.experiments.embedding_similarity.load_encoder", lambda config: (encoder, {})
    )
    with pytest.raises(ValueError, match="wrong number"):
        encode_inputs({"a": "abc"}, embedding_config({}))
    for limit in (None, True, 0, float("inf")):
        with pytest.raises(ValueError, match="token window"):
            fit_chunks("abc", encoder.tokenizer, limit)


def test_embedding_workspace_exports_vectors_without_calling_model_again(tmp_path, monkeypatch):
    encoder = FakeEncoder()
    monkeypatch.setattr(
        "agentboard.experiments.embedding_similarity.load_encoder", lambda config: (encoder, {"fake": "1"})
    )
    archive = Archive(tmp_path / "archive")
    targets = [
        {
            "original_codex_session_id": "synthetic",
            "original_codex_turn_id": str(i),
            "classification_input_sha256": str(i) * 64,
            "messages": [{"role": "user", "content": "same input"}],
        }
        for i in (1, 2)
    ]
    data = archive.begin("synthetic", "embedding-demo", kind="dataset")
    data.add_json(targets, "turns.json")
    data.finalize()
    run = archive.begin("synthetic", "embedding-demo")
    run.add_json(
        {
            "model": "fake-classifier",
            "reasoning_effort": "low",
            "classifications": [
                {**t, "category": "coding", "reason": "Synthetic classification", "reasoning_effort": "low"}
                for t in targets
            ],
        },
        "results.json",
    )
    run.finalize()
    recipe = {
        "format_version": CURATION_VERSION,
        "project": "synthetic",
        "experiment": "embedding-demo",
        "reference_configuration": {
            "model": "fake-classifier",
            "reasoning_effort": "low",
            "execution": "independent",
            "taxonomy": "session-purpose-v1",
        },
        "dataset": archive.reference(data.id, "turns.json"),
        "results": [
            {"format": "independent-turn-v1", "reference": archive.reference(run.id, "results.json")}
        ],
    }
    store = CurationStore(archive)
    wid = store.create(recipe)["id"]
    workspace = store.read(wid)
    assert workspace["similarity"]["method"] == "embedding-cosine"
    assert len(workspace["embedding_vectors"]) == 2 and len(workspace["pairs"]) == 1
    before = len(encoder.batches)
    exported = store.export(wid, 0)
    assert len(encoder.batches) == before
    saved = json.loads((archive.locate(exported["id"]) / "outputs/workspace.json").read_bytes())
    assert saved["embedding_vectors"] == workspace["embedding_vectors"]
    assert archive.verify(exported["id"])["verified"]


def test_current_recipe_resolves_available_sol_execution_and_pins_it(tmp_path):
    from agentboard.experiments.archive import atomic_json
    from agentboard.experiments.coverage_analysis import VERSION as COVERAGE_VERSION
    from agentboard.experiments.curation import DEFAULT_REFERENCE

    archive = Archive(tmp_path / "archive")
    store = CurationStore(archive)
    independent = {**DEFAULT_REFERENCE, "execution": "independent"}
    report = archive.begin("synthetic", "current-coverage", kind="report")
    report.add_json({"classifiers": [{"configuration": independent}]}, "outputs/coverage.json")
    report.add_json(
        {"format_version": COVERAGE_VERSION, "project": "synthetic", "experiment": "example"},
        "inputs/recipe.json",
    )
    report.finalize()
    atomic_json(
        archive.root / "sync/current-classification.json",
        {"coverage": archive.reference(report.id, "outputs/coverage.json")},
    )
    assert store.current_recipe()["reference_configuration"] == independent


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("Synthetic missing weights"),
        OSError("Synthetic unreadable model"),
        ValueError("Synthetic invalid model configuration"),
        RuntimeError("Synthetic unsupported architecture"),
    ],
)
def test_model_load_errors_preserve_cause_without_misdiagnosing_cache(monkeypatch, failure):
    def constructor(*args, **kwargs):
        raise failure

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor)
    )
    with pytest.raises(ValueError) as caught:
        load_encoder(embedding_config({}))
    assert caught.value.__cause__ is failure
    if isinstance(failure, FileNotFoundError):
        assert "file is missing" in str(caught.value)
        assert "cache the pinned revision" in str(caught.value)
    else:
        assert "runtime compatibility" in str(caught.value)
        assert "cache the pinned revision" not in str(caught.value)


def test_unexpected_loader_errors_are_not_reclassified(monkeypatch):
    def constructor(*args, **kwargs):
        raise KeyError("Synthetic implementation bug")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor)
    )
    with pytest.raises(KeyError, match="implementation bug"):
        load_encoder(embedding_config({}))


def test_failed_embedding_comparison_leaves_caller_rows_unchanged(monkeypatch):
    rows = [{"id": "target", "target": {"messages": [{"role": "user", "content": "Synthetic input"}]}}]
    before = deepcopy(rows)

    def fail(inputs, config):
        raise ValueError("Synthetic provider failure")

    monkeypatch.setattr("agentboard.experiments.embedding_similarity.encode_inputs", fail)
    with pytest.raises(ValueError, match="provider failure"):
        compare_inputs(rows, embedding_config({}))
    assert rows == before

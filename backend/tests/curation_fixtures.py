"""Shared synthetic curation data; callers choose their embedding test double."""

from dataclasses import asdict

from agentboard.experiments import Archive
from agentboard.experiments.curation import VERSION, CurationStore
from agentboard.experiments.embedding_similarity import EmbeddingResult


def synthetic_embeddings(inputs, config):
    return EmbeddingResult(
        vectors={key: [1.0, 0.0] for key in inputs},
        inputs={
            key: {"chunks": 1, "input_chars": len(text), "truncated": False} for key, text in inputs.items()
        },
        metadata={"version": "synthetic", "method": "embedding-cosine", "metric": "cosine", **asdict(config)},
    )


REFERENCE = {
    "model": "reference-model",
    "reasoning_effort": "high",
    "execution": "batched",
    "taxonomy": "session-purpose-v1",
}


def data():
    targets = [
        {
            "index": i,
            "original_codex_session_id": "synthetic-session",
            "original_codex_turn_id": str(i),
            "classification_input_sha256": str(i) * 64,
            "previous_turn_index": i - 1 if i else None,
            "messages": [
                {"role": "user", "content": "Please fix the broken checkout button."},
                {"role": "assistant", "content": f"Synthetic answer {i}"},
            ],
        }
        for i in range(5)
    ]
    targets[3]["messages"] = [{"role": "user", "content": " \n "}]
    targets[4]["messages"] = [{"role": "assistant", "content": "Please fix the broken checkout button."}]
    comparison = {
        "experiment": {
            "models": [
                {"model": "reference-model", "reasoning_effort": "high"},
                {"model": "small-model", "reasoning_effort": "low"},
            ]
        },
        "turn_results": [],
    }
    for i, target in enumerate(targets):
        row = {
            k: target[k]
            for k in ("original_codex_session_id", "original_codex_turn_id", "classification_input_sha256")
        }
        row["classifications"] = {
            "reference-model": {
                "category": "bug-fixing",
                "reason": "Fixes a specific failure",
                "reasoning_effort": "high",
            },
            "small-model": {
                "category": "coding" if i == 0 else "bug-fixing",
                "reason": "Software task",
                "reasoning_effort": "low",
            },
        }
        comparison["turn_results"].append(row)
    comparison["turn_results"][2]["classification_input_sha256"] = "f" * 64
    comparison["turn_results"][3]["classifications"]["reference-model"]["prompt_compliance_errors"] = [
        "invalid"
    ]
    comparison["turn_results"][4]["classifications"]["reference-model"] = None
    recipe = {
        "format_version": VERSION,
        "project": "synthetic",
        "experiment": "curation",
        "reference_configuration": REFERENCE,
        "similarity_threshold": 0.85,
    }
    return recipe, targets, [{"format": "turn-comparison-v1", "value": comparison}]


def prepare_curation(tmp_path):
    archive = Archive(tmp_path / "archive")
    recipe, targets, sources = data()
    dataset = archive.begin("synthetic", "targets", kind="dataset")
    dataset.add_json(targets, "turns.json")
    dataset.finalize()
    results = archive.begin("synthetic", "results")
    results.add_json(sources[0]["value"], "results.json")
    results.finalize()
    recipe.update(
        dataset=archive.reference(dataset.id, "turns.json"),
        results=[
            {"format": sources[0]["format"], "reference": archive.reference(results.id, "results.json")}
        ],
    )
    store = CurationStore(archive)
    wid = store.create(recipe)["id"]
    return store, wid, recipe

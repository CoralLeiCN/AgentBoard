"""Create a persistent synthetic review demo; no models or private data.

Run: uv run python examples/dataset_curation.py --data-home /private/tmp/agentboard-curation-demo
Then run the printed review command and open its loopback URL in the in-app browser.
"""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agentboard.experiments import Archive
from agentboard.experiments.curation import DEFAULT_REFERENCE, VERSION, CurationStore
from agentboard.experiments.embedding_similarity import EmbeddingResult


def create_demo(data_home):
    archive = Archive(data_home)
    prompts = [
        "Fix the checkout button: clicking it raises an error.",
        "Fix the checkout button: clicking it raises an error.",
        "Fix the checkout button: clicking it raises an error!",
        "Write a short release announcement.",
        "Calculate the mean of 4, 8 and 12.",
        "",
    ]
    targets = [
        {
            "index": i,
            "original_codex_session_id": "synthetic-demo",
            "original_codex_turn_id": f"turn-{i}",
            "classification_input_sha256": hashlib.sha256(
                f"synthetic-classifier-input-{i}".encode()
            ).hexdigest(),
            "previous_turn_index": i - 1 if i else None,
            "messages": ([{"role": "user", "content": prompt}] if prompt else [])
            + [{"role": "assistant", "content": "Synthetic saved assistant answer; no model was called."}],
        }
        for i, prompt in enumerate(prompts)
    ]
    dataset = archive.begin("synthetic", "curation-demo", kind="dataset")
    dataset.add_json(targets, "inputs/turns.json", schema_version="demo-v1")
    dataset.finalize()
    reference = archive.reference(dataset.id, "inputs/turns.json")
    results = {
        "experiment": {
            "models": [
                {"model": "gpt-6-sol", "reasoning_effort": "xhigh"},
                {"model": "synthetic-small", "reasoning_effort": "low"},
            ]
        },
        "turn_results": [],
    }
    for i, target in enumerate(targets):
        category = ["bug-fixing", "bug-fixing", "bug-fixing", "writing", "analysis", "other"][i]
        results["turn_results"].append(
            {
                **target,
                "classifications": {
                    "gpt-6-sol": {
                        "category": category,
                        "reason": "Synthetic reference label; no model was called.",
                        "reasoning_effort": "xhigh",
                    }
                    if i != 4
                    else None,
                    "synthetic-small": {
                        "category": "coding" if i < 3 else category,
                        "reason": "Synthetic comparison label.",
                        "reasoning_effort": "low",
                    },
                },
            }
        )
    run = archive.begin("synthetic", "curation-demo", inputs=[reference])
    run.add_json(results, "outputs/results.json", schema_version="turn-comparison-v1")
    run.finalize()
    recipe = {
        "format_version": VERSION,
        "project": "synthetic",
        "experiment": "curation-demo",
        "dataset": reference,
        "reference_configuration": DEFAULT_REFERENCE,
        "similarity_threshold": 0.85,
        "embedding_model": "synthetic/demo-vectors",
        "embedding_revision": "0" * 40,
        "results": [
            {"format": "turn-comparison-v1", "reference": archive.reference(run.id, "outputs/results.json")}
        ],
    }

    def synthetic_vectors(inputs, config):
        # Explicit synthetic evidence for the UI demo; never a production embedding provider.
        vectors = {
            key: [1.0, 0.0, 0.0]
            if text.startswith("Fix")
            else [0.0, 1.0, 0.0]
            if text.startswith("Write")
            else [0.0, 0.0, 1.0]
            for key, text in inputs.items()
        }
        stats = {
            key: {"chunks": 1, "input_chars": len(text), "truncated": False} for key, text in inputs.items()
        }
        return EmbeddingResult(
            vectors,
            stats,
            {
                **asdict(config),
                "version": "synthetic-demo-vectors-v1",
                "method": "embedding-cosine",
                "metric": "cosine",
                "embedding_origin": "synthetic",
                "dimensions": 3,
            },
        )

    with patch("agentboard.experiments.embedding_similarity.encode_inputs", synthetic_vectors):
        return CurationStore(archive).create(recipe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-home", required=True, type=Path)
    args = parser.parse_args()
    result = create_demo(args.data_home)
    print(json.dumps(result, indent=2))
    print(f"uv run agentboard experiments --data-home {args.data_home} review {result['id']} --port 4320")


if __name__ == "__main__":
    main()

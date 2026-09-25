"""Synthetic offline coverage report and restore; no server or model calls.

Run: uv run python examples/experiment_storage.py
Temporary archives are removed after this example verifies them.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from agentboard.experiments import Archive
from agentboard.experiments.coverage import record_coverage
from agentboard.experiments.coverage_analysis import VERSION


def main():
    with TemporaryDirectory(prefix="agentboard-experiments-") as directory:
        root = Path(directory)
        archive = Archive(root / "data")
        targets = [{"original_codex_session_id": "synthetic-session", "original_codex_turn_id": f"turn-{i}",
                    "classification_input_sha256": str(i) * 64} for i in (1, 2)]
        dataset = archive.begin("synthetic", "coverage-demo", kind="dataset")
        dataset.add_json(targets, "inputs/turns.json", role="classifier-targets", schema_version="demo-v1")
        dataset.finalize()
        dataset_ref = archive.reference(dataset.id, "inputs/turns.json")
        run = archive.begin("synthetic", "coverage-demo", inputs=[dataset_ref])
        run.add_json({"model": "synthetic-model", "reasoning_effort": "low", "classifications": [
            {**targets[0], "model": "synthetic-model", "reasoning_effort": "low", "category": "coding",
             "reason": "Synthetic result; no model was called"}]},
            "outputs/results.json", schema_version="independent-turn-v1")
        run.finalize()
        report = record_coverage(archive, {"format_version": VERSION, "project": "synthetic",
                                         "experiment": "coverage-demo", "dataset": dataset_ref, "results": [
            {"format": "independent-turn-v1", "reference": archive.reference(run.id, "outputs/results.json")}]})
        shutil.copytree(archive.home, root / "restored")
        shutil.rmtree(archive.home)
        restored = Archive(root / "restored")
        restored.verify(report["id"])
        bundle = restored.locate(report["id"])
        # Explicitly execute this example's known analysis, never arbitrary archive code.
        regenerated = root / "regenerated.json"
        subprocess.run([sys.executable, "-I", str(bundle / "inputs/analysis.py"),
                        str(bundle / "inputs/analysis-input.json"), str(regenerated)], check=True)
        assert regenerated.read_bytes() == (bundle / "outputs/coverage.json").read_bytes()
        assert (report["available"], report["pending"]) == (1, 1)
        print(json.dumps({"target_count": 2, "available": 1, "pending": 1, "restore_verified": True,
                          "offline_regeneration_identical": True, "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()

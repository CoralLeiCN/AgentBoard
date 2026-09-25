"""Save coverage as a new report with exact input references and standalone analysis."""

import json
import platform
from pathlib import Path

from .archive import sha256
from .coverage_analysis import VERSION, calculate


def record_coverage(archive, recipe):
    if recipe.get("format_version") != VERSION:
        raise ValueError("Unsupported coverage recipe")
    references = [recipe["dataset"], *[s["reference"] for s in recipe["results"]]]
    verified = {}
    paths = [archive.resolve(ref, _verified=verified) for ref in references]
    targets = json.loads(paths[0].read_bytes())
    payload = {"format_version": VERSION, "targets": targets,
               "sources": [{"format": source["format"], "value": json.loads(path.read_bytes())}
                           for source, path in zip(recipe["results"], paths[1:], strict=True)]}
    result = calculate(payload)
    script = Path(__file__).with_name("coverage_analysis.py")
    recorder = archive.begin(recipe["project"], recipe["experiment"], kind="report", inputs=references, metadata={
        "code": {"artifact": "inputs/analysis.py", "sha256": sha256(script), "calculation_version": VERSION},
        "environment": {"python": platform.python_version(), "dependencies": "Python standard library only"},
        "configuration": {"command": "python inputs/analysis.py inputs/analysis-input.json outputs/coverage.json",
                          "recipe": "inputs/recipe.json", "model_calls": 0},
        "coverage": {"requested_targets": result["target_count"], "state": "pending" if
                     result["targets_pending_any_configuration"] else "complete", "artifact": "outputs/coverage.json"},
        "metrics": {"pending_targets": {"value": result["targets_pending_any_configuration"], "unit": "turns",
                                         "origin": "calculated", "source": "outputs/coverage.json",
                                         "calculation_version": VERSION}},
        "provenance_gaps": ["Python runtime is not bundled; standalone analysis requires compatible Python 3.11+"],
    })
    recorder.add_file(script, "inputs/analysis.py", role="analysis-source")
    recorder.add_json(recipe, "inputs/recipe.json", role="configuration", schema_version=VERSION)
    recorder.add_json(payload, "inputs/analysis-input.json", role="materialized-pinned-inputs", schema_version=VERSION)
    recorder.add_json(result, "outputs/coverage.json", role="coverage-report", schema_version=VERSION)
    finalized = recorder.finalize()
    return {**finalized, "target_count": result["target_count"], "session_count": result["session_count"],
            "configuration_count": result["configuration_count"],
            "available": result["targets_available_all_configurations"],
            "pending": result["targets_pending_any_configuration"]}

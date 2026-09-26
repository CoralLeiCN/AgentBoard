"""Opt-in synthetic embedding check against the isolated private model endpoint."""

import pytest

from agentboard.experiments.embedding_similarity import cosine_scores, embedding_config, encode_inputs

pytestmark = pytest.mark.e2e


def test_private_embedding_endpoint(private_endpoint, monkeypatch):
    endpoint = private_endpoint("AGENTBOARD_E2E_EMBEDDING")
    monkeypatch.setenv("AGENTBOARD_PRIVATE_EMBEDDING_KEY", endpoint.api_key)
    recipe = {
        "embedding_provider": "remote",
        "embedding_model": endpoint.model,
        "embedding_base_url": endpoint.base_url,
        "embedding_timeout_seconds": 15,
    }
    if endpoint.api_key:
        recipe["embedding_api_key_env"] = "AGENTBOARD_PRIVATE_EMBEDDING_KEY"
    try:
        result = encode_inputs(
            {"a": "Synthetic test: sort three numbers.", "b": "Synthetic test: sort three numbers."},
            embedding_config(recipe),
        )
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert result.metadata["dimensions"] > 0 and len(result.inputs) == 2
    assert cosine_scores(result.vectors, 0.99)[0][2] == pytest.approx(1.0, abs=1e-5)

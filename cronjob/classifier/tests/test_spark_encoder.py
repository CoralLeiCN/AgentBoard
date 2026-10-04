"""Explicit synthetic GPU checks against a smoke checkpoint; never invokes Codex."""

import os
from pathlib import Path

import pytest

from classifier.turn_encoder import MAX_TOKENS, tokenize

pytestmark = pytest.mark.skipif(
    not os.environ.get("TURN_ENCODER_SMOKE_MODEL"), reason="Explicit Spark smoke checkpoint required"
)


def test_real_tokenizer_checkpoint_and_service_parity():
    from fastapi.testclient import TestClient

    from classifier.encoder_runtime import Encoder
    from classifier.encoder_service import create_app

    encoder = Encoder(Path(os.environ["TURN_ENCODER_SMOKE_MODEL"]))
    large = [{"role": "user", "content": " token" * 10000}]
    retained = tokenize(encoder.tokenizer, large, [])
    assert retained["truncated"] and len(retained["input_ids"]) == MAX_TOKENS
    assert retained["input_ids"][0] == encoder.tokenizer.cls_token_id
    assert retained["input_ids"][-1] == encoder.tokenizer.sep_token_id
    items = [
        {
            "request_id": "synthetic-code",
            "target_turn": [{"role": "user", "content": "Write a Python sort function."}],
            "preceding_turn_context": [],
        },
        {"request_id": "synthetic-empty", "target_turn": [], "preceding_turn_context": []},
    ]
    offline = [encoder.predict([item])[0] for item in items]
    offline_batch = encoder.predict(items)
    with TestClient(create_app(lambda: encoder)) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/model").json()["checkpoint_sha256"] == encoder.metadata["checkpoint_sha256"]
        response = client.post("/v1/turn-classifications", json={"items": items})
        assert response.status_code == 200
        served = response.json()["results"]
    for item, expected, batched, actual in zip(items, offline, offline_batch, served, strict=True):
        assert actual["request_id"] == item["request_id"]
        assert actual["effective_input_sha256"] == expected["effective_input_sha256"]
        assert actual["category"] == expected["category"]
        # Same batching must agree tightly. BF16 kernels vary with padded batch shape;
        # the smoke checkpoint measured 0.00418 absolute single/batch score difference.
        assert max(abs(actual["scores"][c] - batched["scores"][c]) for c in actual["scores"]) < 1e-5
        assert max(abs(actual["scores"][c] - expected["scores"][c]) for c in actual["scores"]) < 0.01

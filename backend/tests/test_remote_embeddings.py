"""Synthetic remote API contracts; no network or real model execution."""

import argparse
import json
from copy import deepcopy

import httpx
import pytest
from curation_fixtures import prepare_curation, synthetic_embeddings

from agentboard.experiments.cli import configure, execute
from agentboard.experiments.curation import CurationStore, compare_inputs
from agentboard.experiments.embedding_similarity import embedding_config, encode_inputs


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Reuse the archived classifier fixture, with explicitly synthetic initial vectors.
    with monkeypatch.context() as setup:
        setup.setattr("agentboard.experiments.embedding_similarity.encode_inputs", synthetic_embeddings)
        return prepare_curation(tmp_path)


def remote_recipe(**overrides):
    return {
        "embedding_provider": "remote",
        "embedding_model": "custom-embedding-model",
        "embedding_base_url": "http://embedding.example.test/v1/",
        **overrides,
    }


@pytest.fixture
def transport(monkeypatch):
    real_client = httpx.Client

    def install(handler):
        def client(**kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            assert 0 < kwargs["timeout"] <= 600
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", client)

    return install


def test_remote_inputs_batches_indexes_auth_and_provenance(transport, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_EMBEDDING_KEY", "synthetic-secret")
    calls = []

    def handler(request):
        assert str(request.url) == "http://embedding.example.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": "deployed-weights",
                "data": [
                    {"index": i, "embedding": [3, 4] if text.startswith("A") else [0, 2]}
                    for i, text in reversed(list(enumerate(body["input"])))
                ],
            },
        )

    transport(handler)
    rows = [
        {
            "id": key,
            "target": {
                "previous_turn_index": 77,
                "messages": [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "Do not send answers"},
                    {"role": "tool", "content": "Do not send tools"},
                ],
            },
            "label": {"category": "Never send labels"},
        }
        for key, text in [("a", " Ａ  sentence "), ("b", "B long " * 3000), ("c", "A final"), ("empty", " ")]
    ]
    before = deepcopy(rows)
    comparison = compare_inputs(
        rows,
        embedding_config(
            remote_recipe(embedding_api_key_env="SYNTHETIC_EMBEDDING_KEY", embedding_batch_size=2)
        ),
    )
    pairs, metadata, vectors = comparison.pairs, comparison.metadata, comparison.vectors
    assert rows == before
    assert calls == [
        {
            "model": "custom-embedding-model",
            "input": ["A sentence", ("B long " * 3000).strip()],
            "encoding_format": "float",
        },
        {"model": "custom-embedding-model", "input": ["A final"], "encoding_format": "float"},
    ]
    assert vectors == {"b": [0.0, 1.0], "a": [0.6, 0.8], "c": [0.6, 0.8]}
    assert [(p["left"], p["right"]) for p in pairs] == [("a", "c")]
    assert metadata["encode_batches"] == 2 and metadata["dimensions"] == 2
    assert metadata["reported_model"] == "deployed-weights"
    assert metadata["truncated"] is None and metadata["server_truncation"] == "unknown"
    assert comparison.annotations["b"]["embedding"]["input_chars"] == len(("B long " * 3000).strip())
    assert "synthetic-secret" not in json.dumps([rows, metadata, vectors])


@pytest.mark.parametrize(
    "override",
    [
        {"embedding_provider": "typo"},
        {"embedding_model": ""},
        {"embedding_model": None},
        {"embedding_base_url": "file:///tmp/model"},
        {"embedding_base_url": "http://host:bad/v1"},
        {"embedding_base_url": "https://user:secret@host/v1"},
        {"embedding_base_url": "http://host/v1?api_key=secret"},
        {"embedding_base_url": "http://host/v1#secret"},
        {"embedding_base_url": "http://host/v1/embeddings"},
        {"embedding_api_key_env": "secret-token"},
        {"embedding_timeout_seconds": 0},
        {"embedding_timeout_seconds": float("nan")},
        {"embedding_timeout_seconds": True},
        {"embedding_revision": "a" * 40},
        {"embedding_api_key": "never-save-a-token"},
    ],
)
def test_remote_config_rejects_invalid_or_secret_configuration(override):
    with pytest.raises(ValueError):
        embedding_config(remote_recipe(**override))


def test_remote_endpoint_is_explicit_and_credentials_are_optional(transport, monkeypatch):
    for recipe in (
        {"embedding_provider": "remote", "embedding_model": "m"},
        {"embedding_base_url": "http://host/v1"},
    ):
        with pytest.raises(ValueError):
            embedding_config(recipe)
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    with pytest.raises(ValueError, match="unset or empty"):
        encode_inputs(
            {"a": "text"}, embedding_config(remote_recipe(embedding_api_key_env="MISSING_TEST_KEY"))
        )

    def anonymous(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    transport(anonymous)
    metadata = encode_inputs({"a": "text"}, embedding_config(remote_recipe())).metadata
    assert metadata["reported_model"] is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"data": []},
        {"data": [{"index": 0, "embedding": [1, 0]}] * 2},
        {"data": [{"index": True, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 2, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 0]}]},
        {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0, 0]}]},
        {"data": [{"index": 0, "embedding": "base64"}, {"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": [True, 0]}, {"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": ["1", 0]}, {"index": 1, "embedding": [1, 0]}]},
    ],
)
def test_remote_malformed_responses_fail_without_substitution(transport, payload):
    transport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ValueError, match="[Ee]mbedding"):
        encode_inputs({"a": "first", "b": "second"}, embedding_config(remote_recipe()))


@pytest.mark.parametrize("status", [302, 400, 401, 413, 429, 500])
def test_http_errors_are_safe_and_never_redirect_or_retry(transport, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="private input and secret", headers={"location": "https://elsewhere.invalid"}
        )

    transport(handler)
    with pytest.raises(ValueError, match=f"HTTP {status}") as exc:
        encode_inputs({"a": "private input"}, embedding_config(remote_recipe()))
    assert "private input" not in str(exc.value) and "secret" not in str(exc.value)
    assert len(calls) == 1


def test_timeout_json_nonfinite_and_model_drift(transport):
    def timeout(request):
        raise httpx.ReadTimeout("secret request details", request=request)

    transport(timeout)
    with pytest.raises(ValueError, match="timed out") as exc:
        encode_inputs({"a": "text"}, embedding_config(remote_recipe()))
    assert "secret" not in str(exc.value)
    for body in (b"bad json", b'{"data":[{"index":0,"embedding":[NaN,1]}]}'):
        transport(lambda request: httpx.Response(200, content=body))
        with pytest.raises(ValueError):
            encode_inputs({"a": "text"}, embedding_config(remote_recipe()))
    transport(
        lambda request: httpx.Response(
            200,
            json={
                "model": json.loads(request.content)["input"][0],
                "data": [{"index": 0, "embedding": [1, 0]}],
            },
        )
    )
    with pytest.raises(ValueError, match="model changed"):
        encode_inputs({"a": "first", "b": "second"}, embedding_config(remote_recipe(embedding_batch_size=1)))


def test_remote_workspace_pins_settings_exports_offline_and_failure_is_atomic(
    prepared, transport, monkeypatch
):
    store, _, recipe = prepared
    monkeypatch.setenv("SYNTHETIC_EMBEDDING_KEY", "synthetic-secret")

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "served-model",
                "data": [
                    {"index": i, "embedding": [1, 0]}
                    for i, _ in enumerate(json.loads(request.content)["input"])
                ],
            },
        )

    transport(handler)
    recipe.update(remote_recipe(embedding_api_key_env="SYNTHETIC_EMBEDDING_KEY"))
    wid = store.create(recipe)["id"]
    value = CurationStore(store.archive).read(wid)
    assert value["recipe"]["embedding_base_url"] == "http://embedding.example.test/v1"
    assert value["recipe"]["embedding_timeout_seconds"] == 60
    assert "synthetic-secret" not in store.path(wid).read_text()
    transport(lambda request: pytest.fail("Review/export must not call an embedding model"))
    bundle = store.export(wid, 0)
    saved = json.loads(
        store.archive.resolve(store.archive.reference(bundle["id"], "outputs/workspace.json")).read_bytes()
    )
    assert saved["embedding_vectors"] == value["embedding_vectors"]
    before = set(store.root.iterdir())
    transport(lambda request: httpx.Response(500, text="secret"))
    with pytest.raises(ValueError, match="HTTP 500"):
        store.create(deepcopy(recipe))
    assert set(store.root.iterdir()) == before


def test_cli_remote_overrides_reach_workspace_and_removed_option_is_rejected(
    prepared, transport, tmp_path, capsys
):
    store, _, recipe = prepared
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe))
    parser = argparse.ArgumentParser()
    configure(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "experiments",
            "--data-home",
            str(store.archive.home),
            "curate",
            "--recipe",
            str(recipe_path),
            "--embedding-provider",
            "remote",
            "--embedding-model",
            "custom",
            "--embedding-base-url",
            "http://example.test/v1",
            "--embedding-batch-size",
            "2",
            "--embedding-timeout-seconds",
            "5",
        ]
    )
    transport(
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1, 0]}
                    for i, _ in enumerate(json.loads(request.content)["input"])
                ]
            },
        )
    )
    execute(args)
    wid = json.loads(capsys.readouterr().out)["id"]
    config = store.read(wid)["similarity"]
    assert (config["provider"], config["model"], config["batch_size"], config["timeout_seconds"]) == (
        "remote",
        "custom",
        2,
        5,
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["experiments", "curate", "--current", "--similarity-method", "jaccard"])

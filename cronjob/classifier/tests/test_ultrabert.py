"""Synthetic source, loader and fixed-rate optimizer contracts."""

import copy
import io
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from ultrabert_helpers import model_source

from classifier.artifacts import config_dict, read_json, write_json
from classifier.encoder_runtime import load_model
from classifier.taxonomy import CATEGORIES
from classifier.ultrabert import FILES, MODELS, verify_source
from download_ultrabert import download
from train_modernbert import Config, archive_entrypoint, archive_name, build_optimizer, run, validate_config
from train_ultrabert import UltraBertConfig, recipe


def test_original_defaults_and_both_optimizer_recipes_are_recorded():
    paths = [Path("unused")] * 3
    original = Config(*paths)
    assert original.learning_rates == (2e-5, 1e-5, 5e-5)
    assert (original.adam_beta1, original.adam_beta2, original.adam_epsilon) == (0.9, 0.999, 1e-8)
    config = recipe(UltraBertConfig(*paths), "baseline")
    assert config.learning_rates == (1e-5,)
    assert config.repeat_seeds == (43, 44)
    for expected, actual in ((original, config), (UltraBertConfig(*paths), recipe(config, "recommended"))):
        for key in ("warmup_ratio", "weight_decay", "adam_beta1", "adam_beta2", "adam_epsilon"):
            assert config_dict(actual)[key] == config_dict(expected)[key]


@pytest.mark.parametrize(
    "changes",
    [
        {"learning_rates": ()},
        {"learning_rates": (1e-5, 1e-5)},
        {"learning_rates": (float("nan"),)},
        {"learning_rates": (-1,)},
        {"learning_rates": (True,)},
        {"repeat_seeds": (42,)},
        {"initial_seed": -1},
        {"adam_beta2": 1.0},
        {"adam_epsilon": 0},
        {"weight_decay": -1},
        {"warmup_ratio": 1.1},
        {"adam_beta1": float("inf")},
    ],
)
def test_invalid_optimizer_or_search_fails_before_opening_inputs(changes):
    with pytest.raises(ValueError):
        validate_config(replace(Config(*([Path("unused")] * 3)), **changes))


def test_fixed_rate_smoke_and_three_runs_precede_test_selection(tmp_path, monkeypatch):
    import train_modernbert as trainer

    config = UltraBertConfig(tmp_path / "dataset", tmp_path / "model", tmp_path / "output")
    config.model.mkdir()
    write_json(config.model / "source.json", {"synthetic": True})
    monkeypatch.setattr(
        trainer,
        "verify",
        lambda *a: {"preprocessing": trainer.PREPROCESSING, "base_model": {"synthetic": True}},
    )
    calls = []

    def train(config, lr, seed, smoke=False):
        assert lr == 1e-5
        calls.append((lr, seed, smoke))
        return {"validation_macro_f1": 0.5 + seed / 1000, "validation_loss": 1.0}

    def evaluate(config, selected):
        selection = read_json(config.output / "selection.json")
        assert selection["selected_path"] == str(selected)
        assert len(selection["candidates"]) == 3
        assert calls == [(1e-5, 42, True), (1e-5, 42, False), (1e-5, 43, False), (1e-5, 44, False)]
        return {"encoder": {"macro_f1": 0.5, "accuracy": 0.6}}

    monkeypatch.setattr(trainer, "train_one", train)
    monkeypatch.setattr(trainer, "manifest_hash", lambda p: "synthetic-checksum")
    monkeypatch.setattr(trainer, "report", evaluate)
    run(config, "train")
    saved = read_json(config.output / "configuration.json")
    assert saved["learning_rates"] == [1e-5]
    assert saved["optimizer_recipe"] == "recommended"


@pytest.mark.parametrize("mutation", ["revision", "code", "missing", "traversal"])
def test_source_tampering_is_rejected_before_local_model_code(tmp_path, mutation):
    source = model_source(tmp_path / "model")
    if mutation == "revision":
        source["revision"] = "unapproved"
    elif mutation == "code":
        (tmp_path / "model/modeling_ultrabert.py").write_text("changed")
    elif mutation == "missing":
        source["files"].pop()
    else:
        source["files"][0]["path"] = "../outside"
    with pytest.raises(ValueError):
        verify_source(tmp_path / "model", source)


@pytest.mark.parametrize("ultra", [False, True])
@pytest.mark.parametrize("fresh", [False, True])
def test_loader_keeps_modernbert_local_and_enables_only_pinned_ultrabert(tmp_path, monkeypatch, ultra, fresh):
    path = tmp_path / "model"
    if ultra:
        source = model_source(path)
    else:
        path.mkdir()
        source = {"model_id": "answerdotai/ModernBERT-base", "files": []}
        write_json(path / "source.json", source)
    calls = []
    config = SimpleNamespace(
        model_type="ultrabert" if ultra else "modernbert",
        id2label=dict(enumerate(CATEGORIES)),
    )
    if not ultra:
        config.max_position_embeddings = 8192
    tokenizer = SimpleNamespace()
    model = SimpleNamespace(to=lambda device: "loaded")

    def factory(value):
        def load(*args, **kwargs):
            calls.append(kwargs)
            assert kwargs["local_files_only"] is True
            assert kwargs["trust_remote_code"] is ultra
            return value

        return SimpleNamespace(from_pretrained=load)

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoConfig=factory(config),
            AutoTokenizer=factory(tokenizer),
            AutoModelForSequenceClassification=factory(model),
        ),
    )
    assert load_model(path, "cpu", fresh=fresh, base_model=source) == ("loaded", tokenizer)
    assert len(calls) == 3 and tokenizer.truncation_side == "right"
    assert config.reference_compile is False


@pytest.mark.parametrize("recommended", [False, True])
def test_actual_adamw_updates_match_explicit_recipe(recommended):
    torch = pytest.importorskip("torch")
    config = recipe(UltraBertConfig(*([Path("unused")] * 3)), "recommended" if recommended else "baseline")
    model = torch.nn.Linear(2, 1)
    reference = copy.deepcopy(model)
    actual = build_optimizer(model, config, 1e-5)
    expected = torch.optim.AdamW(
        reference.parameters(),
        lr=1e-5,
        betas=(0.9, 0.98 if recommended else 0.999),
        eps=1e-6 if recommended else 1e-8,
        weight_decay=0.1 if recommended else 0.01,
    )
    for gradient in (0.3, -0.8, 0.000001):
        for a, b in zip(model.parameters(), reference.parameters(), strict=True):
            a.grad = torch.full_like(a, gradient)
            b.grad = torch.full_like(b, gradient)
        actual.step()
        expected.step()
    for a, b in zip(model.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(a, b, rtol=0, atol=0)


def test_archive_names_preserve_existing_models_and_identify_ultrabert():
    assert archive_name("answerdotai/ModernBERT-base") == "modernbert-turn-v1"
    assert archive_name("answerdotai/ModernBERT-large") == "modernbert-large-turn-v1"
    assert archive_name("dmis-lab/UltraBERT-380M") == "ultrabert-380m-turn-v1"
    assert archive_entrypoint("answerdotai/ModernBERT-base") == "train_modernbert.py"
    assert archive_entrypoint("dmis-lab/UltraBERT-380M") == "train_ultrabert.py"


@pytest.mark.parametrize(
    "model_id,dockerfile",
    [
        ("answerdotai/ModernBERT-base", "Dockerfile.spark"),
        ("answerdotai/ModernBERT-large", "Dockerfile.spark"),
        ("dmis-lab/UltraBERT-150M", "Dockerfile.ultrabert"),
        ("dmis-lab/UltraBERT-380M", "Dockerfile.ultrabert"),
    ],
)
def test_archive_retains_the_models_reproduction_environment(tmp_path, monkeypatch, model_id, dockerfile):
    import train_modernbert as trainer

    config = Config(tmp_path / "dataset", tmp_path / "model", tmp_path / "output", tmp_path / "archive")
    for path in (config.dataset / "sources", config.model, config.output):
        path.mkdir(parents=True)
    write_json(config.dataset / "sources/sources.json", {"synthetic": True})
    write_json(config.model / "source.json", {"model_id": model_id})
    write_json(config.output / "configuration.json", {"synthetic": True})
    code = tmp_path / "code"
    code.mkdir()
    for name in ("train_modernbert.py", "train_ultrabert.py", "pyproject.toml", "uv.lock"):
        (code / name).write_text("synthetic source")
    for name, version in (("Dockerfile.spark", "4.57.6"), ("Dockerfile.ultrabert", "5.16.1")):
        (code / name).write_text(f"RUN pip install transformers=={version}\n")
    monkeypatch.setattr(trainer, "__file__", str(code / "train_modernbert.py"))
    files, metadata = {}, {}

    class Recorder:
        id = "synthetic-archive"

        def add_file(self, source, destination):
            files[destination] = source.read_bytes()

        def finalize(self, *, outcome):
            assert outcome == "succeeded"

    class Archive:
        def __init__(self, home):
            assert home == str(config.archive_home)

        def begin(self, project, experiment, **kwargs):
            assert project == "agentboard" and experiment == archive_name(model_id)
            metadata.update(kwargs["metadata"])
            return Recorder()

    for name in ("agentboard", "agentboard.experiments", "agentboard.experiments.archive"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["agentboard.experiments.archive"].Archive = Archive
    trainer.archive_run(config, "succeeded")
    assert files[f"code/{dockerfile}"] == (code / dockerfile).read_bytes()
    assert {p for p in files if "Dockerfile" in p} == {f"code/{dockerfile}"}
    assert files["code/pyproject.toml"] == (code / "pyproject.toml").read_bytes()
    assert files["code/uv.lock"] == (code / "uv.lock").read_bytes()
    assert f"code/{metadata['code']['entrypoint']}" in files


@pytest.mark.parametrize("model_id", list(MODELS))
def test_download_pins_public_urls_and_refuses_existing_output(tmp_path, monkeypatch, model_id):
    calls = []

    def public_file(url, timeout):
        assert timeout == 120
        calls.append(url)
        return io.BytesIO(b"synthetic public file")

    monkeypatch.setattr("download_ultrabert.urllib.request.urlopen", public_file)
    download(tmp_path / "model", model_id)
    assert calls == [f"https://huggingface.co/{model_id}/resolve/{MODELS[model_id]}/{name}" for name in FILES]
    verify_source(tmp_path / "model", read_json(tmp_path / "model/source.json"))
    with pytest.raises(FileExistsError):
        download(tmp_path / "model", model_id)
    assert len(calls) == len(FILES)


def test_loader_rejects_ultrabert_inventory_before_generic_path_hashing(tmp_path, monkeypatch):
    source = model_source(tmp_path / "model")
    source["files"][0]["path"] = "../outside"
    (tmp_path / "model/source.json").unlink()
    write_json(tmp_path / "model/source.json", source)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoConfig=None, AutoTokenizer=None, AutoModelForSequenceClassification=None),
    )
    monkeypatch.setattr("classifier.encoder_runtime.sha256", lambda p: pytest.fail("Hashed unvalidated path"))
    with pytest.raises(ValueError, match="inventory"):
        load_model(tmp_path / "model", "cpu", fresh=True)

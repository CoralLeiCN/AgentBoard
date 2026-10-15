"""Frozen identities and source text survive a change of tokenizer."""

import sys
from types import SimpleNamespace

import pytest
from test_turn_encoder import CharacterTokenizer, cohort
from ultrabert_helpers import model_source

from classifier.artifacts import read_rows, seal, sha256, write_json, write_rows
from classifier.taxonomy import CATEGORIES, TAXONOMY_VERSION
from classifier.turn_encoder import PREPROCESSING, chronological_split, join_astra, tokenize
from prepare_ultrabert import prepare


def source_dataset(path):
    path.mkdir()
    turns, reference = cohort()
    splits, grouping = chronological_split(join_astra(turns, reference))
    for split, rows in splits.items():
        write_rows(
            path / f"{split}.jsonl",
            [
                dict(
                    r,
                    unknown_record={"retained": True},
                    **tokenize(CharacterTokenizer(), r["target_turn"], r["preceding_turn_context"]),
                )
                for r in rows
            ],
        )
    write_json(path / "split-assignment.json", grouping)
    (path / "sources").mkdir()
    write_json(path / "sources/sources.json", {"synthetic": True})
    (path / "sources/unknown.bin").write_bytes(b"\x00\xffsynthetic retained bytes\n")
    seal(
        path,
        "turn-encoder-dataset",
        source_pins={},
        grouping=grouping,
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
    )


class DifferentTokenizer(CharacterTokenizer):
    def __call__(self, text, **kwargs):
        result = super().__call__(text, **kwargs)
        result["input_ids"] = [i + 1000 for i in result["input_ids"]]
        return result


def test_retokenization_preserves_labels_membership_and_full_text(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: DifferentTokenizer())),
    )
    source, model, output = [tmp_path / p for p in ("source", "model", "output")]
    source_dataset(source)
    model_source(model)
    prepare(source, model, output)
    for split in ("train", "validation", "test"):
        before, after = [read_rows(p / f"{split}.jsonl") for p in (source, output)]
        for a, b in zip(before, after, strict=True):
            for key in (
                "unknown_record",
                "category",
                "session_id",
                "turn_id",
                "group_id",
                "text_sha256",
                "input_sha256",
                "target_turn",
                "preceding_turn_context",
            ):
                assert a[key] == b[key]
            assert a["input_ids"] != b["input_ids"]
            assert a["effective_input_sha256"] != b["effective_input_sha256"]
    assert sha256(source / "split-assignment.json") == sha256(output / "split-assignment.json")
    assert (source / "sources/unknown.bin").read_bytes() == (output / "sources/unknown.bin").read_bytes()


def test_internally_sealed_but_changed_text_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: CharacterTokenizer())),
    )
    source_dataset(tmp_path / "source")
    path = tmp_path / "source/train.jsonl"
    rows = read_rows(path)
    rows[0]["target_turn"][0]["content"] = "Changed synthetic text"
    path.unlink()
    write_rows(path, rows)
    (tmp_path / "source/manifest.json").unlink()
    seal(
        tmp_path / "source",
        "turn-encoder-dataset",
        source_pins={},
        grouping={},
        preprocessing=PREPROCESSING,
        taxonomy=TAXONOMY_VERSION,
        categories=list(CATEGORIES),
    )
    model = tmp_path / "model"
    model_source(model)
    with pytest.raises(ValueError, match="source text or input hash changed"):
        prepare(tmp_path / "source", model, tmp_path / "output")


def test_incompatible_frozen_preprocessing_fails_before_model_loading(tmp_path):
    source_dataset(tmp_path / "source")
    (tmp_path / "source/manifest.json").unlink()
    seal(tmp_path / "source", "turn-encoder-dataset", preprocessing="incompatible")
    with pytest.raises(ValueError, match="input contract"):
        prepare(tmp_path / "source", tmp_path / "unused-model", tmp_path / "output")

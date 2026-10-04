"""Batching must save work without changing examples or optimizer normalization."""

import copy
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from classifier.encoder_batches import microbatches, validate_memory_limits
from train_modernbert import Config, train_step, validate_config


@pytest.mark.parametrize("sort_lengths", [False, True])
def test_physical_batches_preserve_complete_inputs_and_optimizer_membership(sort_lengths):
    rows = [{"id": i, "input_ids": list(range(n))} for i, n in enumerate([7, 2, 9, 3, 4, 1, 8])]
    original = copy.deepcopy(rows)
    seen = []
    for start in range(0, len(rows), 4):
        block = rows[start : start + 4]
        parts = list(microbatches(block, size=3, token_budget=16, sort_lengths=sort_lengths))
        assert sorted(row["id"] for part in parts for row in part) == sorted(row["id"] for row in block)
        for part in parts:
            assert len(part) <= 3
            assert max(len(row["input_ids"]) for row in part) * len(part) <= 16
            for row in part:
                assert row["input_ids"] == original[row["id"]]["input_ids"]
                seen.append(row["id"])
    assert sorted(seen) == list(range(len(rows)))
    assert rows == original


def test_sorting_reduces_padding_within_one_optimizer_batch():
    rows = [{"input_ids": [1] * n} for n in [2, 9, 3, 8]]

    def padded_tokens(sorted_lengths):
        return sum(
            max(len(r["input_ids"]) for r in batch) * len(batch)
            for batch in microbatches(rows, 2, sort_lengths=sorted_lengths)
        )

    assert padded_tokens(True) == 24
    assert padded_tokens(False) == 34


@pytest.mark.parametrize("size,budget", [(0, None), (True, None), (2, 0), (2, 1)])
def test_invalid_or_insufficient_batch_budget_fails_without_truncation(size, budget):
    with pytest.raises(ValueError):
        list(microbatches([{"input_ids": [1, 2]}], size, budget))


@pytest.mark.parametrize("total,cuda", [(0, None), (80, 80), (80, 81), (None, 60), (80, -1), (True, None)])
def test_invalid_memory_budgets_are_rejected_before_torch_import(total, cuda):
    with pytest.raises(ValueError):
        validate_memory_limits(total, cuda)


@pytest.mark.parametrize(
    "changes",
    [
        {"microbatch": 17},
        {"max_padded_tokens": 8191},
        {"max_padded_tokens": 8192.0},
        {"attention": "unknown"},
        {"evaluation_policy": "unknown"},
        {"memory_limit_bytes": 80, "cuda_memory_limit_bytes": 90},
    ],
)
def test_training_configuration_fails_before_opening_artifacts(changes):
    config = Config(Path("unused"), Path("unused"), Path("unused"))
    with pytest.raises(ValueError):
        validate_config(replace(config, **changes))


@pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="Optional PyTorch is not installed")
def test_uneven_accumulation_matches_full_batch_optimizer_update(monkeypatch):
    """Compare actual gradients/weights, including a final three-example batch."""
    import torch

    class TinyClassifier(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.linear = torch.nn.Linear(1, 8)

        def forward(self, input_ids, labels):
            logits = self.linear(input_ids.float().sum(dim=1, keepdim=True) / 10)
            return SimpleNamespace(loss=torch.nn.functional.cross_entropy(logits, labels))

    def tensor_batch(tokenizer, rows, device):
        tensors = [torch.tensor(r["input_ids"], device=device) for r in rows]
        return {"input_ids": torch.nn.utils.rnn.pad_sequence(tensors, batch_first=True)}

    monkeypatch.setattr("train_modernbert.tensor_batch", tensor_batch)
    monkeypatch.setattr("train_modernbert.memory_usage", lambda limit: {})
    torch.manual_seed(17)
    full_model = TinyClassifier()
    micro_model = copy.deepcopy(full_model)
    full_optimizer = torch.optim.AdamW(full_model.parameters(), lr=0.01)
    micro_optimizer = torch.optim.AdamW(micro_model.parameters(), lr=0.01)
    rows = [
        {"input_ids": [1] * n, "category": "coding" if i % 2 else "writing"}
        for i, n in enumerate([7, 1, 5, 2, 3, 1, 4])
    ]
    config = Config(
        Path("unused"), Path("unused"), Path("unused"), device="cpu", microbatch=4, effective_batch=4
    )
    for block in (rows[:4], rows[4:]):
        expected = train_step(full_model, None, block, full_optimizer, config)
        actual = train_step(
            micro_model,
            None,
            block,
            micro_optimizer,
            replace(config, microbatch=2, max_padded_tokens=8, sort_microbatches=True),
        )
        assert actual.physical_batches > expected.physical_batches
        assert actual.loss_sum == pytest.approx(expected.loss_sum, rel=1e-6)
        assert actual.gradient_norm == pytest.approx(expected.gradient_norm, rel=1e-6)
        for actual_weight, expected_weight in zip(
            micro_model.parameters(), full_model.parameters(), strict=True
        ):
            torch.testing.assert_close(actual_weight, expected_weight, rtol=1e-6, atol=1e-7)

"""Memory-aware physical batches without changing optimizer batch membership."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def microbatches(
    rows: list[dict], size: int, token_budget: int | None = None, sort_lengths: bool = False
) -> Iterator[list[dict]]:
    if type(size) is not int or size <= 0:
        raise ValueError("Microbatch size must be a positive integer")
    if token_budget is not None and (type(token_budget) is not int or token_budget <= 0):
        raise ValueError("Padded-token budget must be a positive integer")
    ordered = sorted(rows, key=lambda r: len(r["input_ids"])) if sort_lengths else rows
    batch, longest = [], 0
    for row in ordered:
        length = len(row["input_ids"])
        if token_budget is not None and length > token_budget:
            raise ValueError("Token budget cannot fit a complete retained input")
        next_longest = max(longest, length)
        if batch and (
            len(batch) == size
            or (token_budget is not None and next_longest * (len(batch) + 1) > token_budget)
        ):
            yield batch
            batch, longest = [], 0
        batch.append(row)
        longest = max(longest, length)
    if batch:
        yield batch


def validate_memory_limits(memory_limit_bytes: int | None, cuda_limit_bytes: int | None) -> None:
    for value in (memory_limit_bytes, cuda_limit_bytes):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("Memory budgets must be positive integer byte counts")
    if cuda_limit_bytes is not None and (
        memory_limit_bytes is None or cuda_limit_bytes >= memory_limit_bytes
    ):
        raise ValueError("CUDA budget must be smaller than the total memory budget")


def configure_memory(memory_limit_bytes: int | None, cuda_limit_bytes: int | None) -> None:
    validate_memory_limits(memory_limit_bytes, cuda_limit_bytes)
    import torch

    if cuda_limit_bytes is not None:
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(cuda_limit_bytes / total, 1.0), 0)


def memory_usage(limit_bytes: int | None = None) -> dict[str, int]:
    import torch

    result = {
        "cuda_allocated_bytes": torch.cuda.memory_allocated(),
        "cuda_reserved_bytes": torch.cuda.memory_reserved(),
        "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
    }
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                result["process_rss_bytes"] = int(line.split()[1]) * 1024
    cgroup = Path("/sys/fs/cgroup/memory.current")
    if cgroup.exists():
        result["container_memory_current_bytes"] = int(cgroup.read_text())
    # Conservative sum on unified-memory machines: some allocations may overlap.
    result["reserved_plus_rss_bytes"] = result["cuda_reserved_bytes"] + result.get("process_rss_bytes", 0)
    if (
        limit_bytes is not None
        and max(result["reserved_plus_rss_bytes"], result.get("container_memory_current_bytes", 0))
        > limit_bytes
    ):
        raise MemoryError("Measured training memory exceeds configured budget")
    return result

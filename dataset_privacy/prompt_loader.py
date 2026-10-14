"""Load the bundled UTF-8 prompts relative to this portable module."""

from pathlib import Path

PROMPT_TEXT = {
    name: (Path(__file__).with_name("prompts") / name).read_bytes().decode("utf-8")
    for name in ("detection.txt", "batch.txt")
}
INSTRUCTIONS = PROMPT_TEXT["detection.txt"]
BATCH_INSTRUCTIONS = INSTRUCTIONS + "\n" + PROMPT_TEXT["batch.txt"]

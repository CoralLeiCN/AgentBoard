"""Load shared prompt templates from package resources."""

from importlib.resources import files
from string import Template


def render_prompt(name: str, **variables: str) -> str:
    """Render a bundled UTF-8 template; missing substitutions raise an error."""
    template = files(__package__).joinpath(f"{name}.txt").read_text(encoding="utf-8").strip()
    return Template(template).substitute(variables)

"""Renders the extraction prompts (Jinja templates under ``prompts/``).

Bump ``PROMPT_VERSION`` whenever a template changes (CLAUDE.md).
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from owl.domain.catalog import FieldGroup
from owl.domain.context import ExtractionContext

PROMPT_VERSION = "1"

_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "prompts"),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)


def render_system(group: FieldGroup) -> str:
    return _ENV.get_template("extraction_system.j2").render(group=group).strip()


def render_user(context: ExtractionContext) -> str:
    return _ENV.get_template("extraction_user.j2").render(context=context).strip()

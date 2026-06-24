"""Markdown report assembly helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .tables import render_markdown_table


def heading(text: str, *, level: int = 1) -> str:
    """Render a Markdown heading."""

    if level < 1 or level > 6:
        raise ValueError("Markdown heading level must be between 1 and 6")
    return f"{'#' * level} {text.strip()}"


def bullet_list(items: Iterable[object]) -> str:
    """Render a simple Markdown bullet list."""

    return "\n".join(f"- {item}" for item in items)


def key_value_section(values: Mapping[str, object]) -> str:
    """Render key/value pairs as a Markdown table."""

    return render_markdown_table(["Key", "Value"], [[key, value] for key, value in values.items()])


@dataclass
class MarkdownReport:
    """Incrementally build a Markdown report."""

    title: str
    _blocks: list[str] = field(default_factory=list)

    def add_heading(self, text: str, *, level: int = 2) -> MarkdownReport:
        self._blocks.append(heading(text, level=level))
        return self

    def add_paragraph(self, text: str) -> MarkdownReport:
        self._blocks.append(text.strip())
        return self

    def add_bullets(self, items: Iterable[object]) -> MarkdownReport:
        self._blocks.append(bullet_list(items))
        return self

    def add_table(self, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> MarkdownReport:
        self._blocks.append(render_markdown_table(headers, rows))
        return self

    def render(self) -> str:
        blocks = [heading(self.title, level=1), *[block for block in self._blocks if block]]
        return "\n\n".join(blocks).rstrip() + "\n"

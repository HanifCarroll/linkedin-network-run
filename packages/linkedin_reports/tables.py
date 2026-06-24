"""Markdown table rendering."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

Alignment = Literal["left", "right", "center"]


def render_markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    alignments: Sequence[Alignment] | None = None,
) -> str:
    """Render a GitHub-flavored Markdown table."""

    if not headers:
        raise ValueError("Markdown table requires at least one header")
    column_count = len(headers)
    if alignments is not None and len(alignments) != column_count:
        raise ValueError("alignment count must match header count")
    for row in rows:
        if len(row) != column_count:
            raise ValueError("row cell count must match header count")
    separator = [
        _separator_cell(alignments[index] if alignments else "left")
        for index in range(column_count)
    ]
    lines = [
        _render_row(headers),
        _render_row(separator),
    ]
    lines.extend(_render_row(row) for row in rows)
    return "\n".join(lines)


def _separator_cell(alignment: Alignment) -> str:
    if alignment == "right":
        return "---:"
    if alignment == "center":
        return ":---:"
    return "---"


def _render_row(cells: Sequence[object]) -> str:
    return "| " + " | ".join(_format_cell(cell) for cell in cells) + " |"


def _format_cell(value: object) -> str:
    if value is None:
        text = ""
    else:
        text = str(value)
    return text.replace("|", r"\|").replace("\n", "<br>").strip()

"""Shared report rendering primitives."""

from .markdown import MarkdownReport, bullet_list, heading, key_value_section
from .tables import render_markdown_table

__all__ = [
    "MarkdownReport",
    "bullet_list",
    "heading",
    "key_value_section",
    "render_markdown_table",
]

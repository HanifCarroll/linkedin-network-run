from __future__ import annotations

import pytest

from packages.linkedin_reports import MarkdownReport, key_value_section, render_markdown_table


def test_render_markdown_table_escapes_pipes_and_newlines() -> None:
    table = render_markdown_table(
        ["Name", "Note"],
        [["Jane", "uses CSV | JSONL"], ["Ana", "line one\nline two"]],
    )

    assert table == (
        "| Name | Note |\n"
        "| --- | --- |\n"
        "| Jane | uses CSV \\| JSONL |\n"
        "| Ana | line one<br>line two |"
    )


def test_render_markdown_table_validates_shape() -> None:
    with pytest.raises(ValueError, match="cell count"):
        render_markdown_table(["A", "B"], [["only one cell"]])


def test_markdown_report_assembles_blocks() -> None:
    report = (
        MarkdownReport("Run Report")
        .add_paragraph("Summary.")
        .add_table(["Metric", "Value"], [["valid_comments", 100]])
        .render()
    )

    assert report.startswith("# Run Report\n\nSummary.")
    assert "| valid_comments | 100 |" in report


def test_key_value_section_uses_table_shape() -> None:
    assert "| Key | Value |" in key_value_section({"status": "running"})

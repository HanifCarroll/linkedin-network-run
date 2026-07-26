from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from apps.cli import main
from apps.content_analytics.exporter import export_content_analytics


def test_analytics_export_downloads_verified_xlsx_without_guarded_preflight(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_TOOLS_ANALYTICS_DOWNLOAD_DIR", str(tmp_path))
    playwriter = _fake_analytics_playwriter(tmp_path)
    out = tmp_path / "linkedin.xlsx"

    assert (
        main(
            [
                "analytics",
                "export",
                "--out",
                str(out),
                "--playwriter-bin",
                str(playwriter),
            ]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "exported"
    assert result["date_range"] == {
        "preset": "7 days",
        "start": "2026-07-20",
        "end": "2026-07-26",
    }
    assert result["workbook_path"] == str(out)
    assert result["workbook_sha256"].startswith("sha256:")
    assert zipfile.is_zipfile(out)

    calls = json.loads((tmp_path / "calls.json").read_text(encoding="utf-8"))
    flattened = [" ".join(call) for call in calls]
    assert not any("incident" in call or "preflight" in call for call in flattened)
    assert any("session new" in call for call in flattened)
    assert any("export_content_analytics.js" in call for call in flattened)


def test_analytics_export_requires_xlsx_extension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end in .xlsx"):
        export_content_analytics(
            out=tmp_path / "analytics.csv",
            playwriter_bin=str(tmp_path / "unused"),
        )


def _fake_analytics_playwriter(tmp_path: Path) -> Path:
    executable = tmp_path / "playwriter"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path({str(tmp_path)!r})
CALLS = ROOT / "calls.json"

calls = json.loads(CALLS.read_text()) if CALLS.exists() else []
calls.append(sys.argv[1:])
CALLS.write_text(json.dumps(calls))

args = sys.argv[1:]
if args == ["session", "new"]:
    print("Session 91 created")
    raise SystemExit(0)
if "-e" in args:
    expression = args[args.index("-e") + 1]
    (ROOT / "config-path.json").write_text(expression.split("=", 1)[1].strip())
    raise SystemExit(0)
if "-f" in args:
    config_path = Path(json.loads((ROOT / "config-path.json").read_text()))
    config = json.loads(config_path.read_text())
    native_download = ROOT / "AggregateAnalytics_Test Account_2026-07-20_2026-07-26.xlsx"
    with zipfile.ZipFile(native_download, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types/>")
        workbook.writestr("xl/workbook.xml", "<workbook/>")
    Path(config["receiptOut"]).write_text(json.dumps({{
        "status": "confirmation_clicked",
        "dateRange": "7 days",
        "selectorContract": {{"export": {{"role": "link", "name": "Export"}}}}
    }}))
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable

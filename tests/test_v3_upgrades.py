"""Tests for Brain Autopilot v3.0 upgrades:
- Environment doctor & static IDE/Pyrefly typing check (`py.typed` + `.pth`)
- Accessibility & performance detectors (`a11y_missing_alt`, `a11y_unlabeled_control`, `large_dom`, `slow_lcp`)
- Self-contained HTML report generator (`report_html.generate_html`)
- Warm shared CDP session reuse (`shared_cdp` + `reset_page`)
- MCP `doctor` tool registration & invocation
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from brain_autopilot import detectors, doctor, mcp, memory, report_html
from brain_autopilot.engines.web import WebEngine
from brain_autopilot.kit import App


def test_doctor_diagnostics_and_py_typed() -> None:
    diag = doctor.diagnose(fix=False)
    assert diag["version"].startswith("3.")
    by_name = {c["name"]: c for c in diag["checks"]}
    assert by_name["python_version"]["ok"] is True
    assert by_name["package_import"]["ok"] is True
    assert "py.typed=True" in by_name["package_import"]["detail"]
    assert "ide_static_typing" in by_name
    assert "cdp_browser" in by_name


def test_v3_a11y_and_perf_detectors_and_mute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mem_dir = str(Path(tmp) / "mem")
        memory.ensure_scaffold(mem_dir)
        eng = WebEngine(use_cdp=False)
        eng._metrics.update(
            {
                "missing_alt_images": 2,
                "unlabeled_controls": 3,
                "dom_nodes": 4500,
                "lcp_ms": 3200.0,
            }
        )
        app = App(eng, mode="probe")
        found = {f["detector"] for f in app.findings(mem_dir=mem_dir)}
        assert {"a11y_missing_alt", "a11y_unlabeled_control", "large_dom", "slow_lcp"} <= found

        detectors.mute(mem_dir, "large_dom")
        detectors.mute(mem_dir, "a11y_missing_alt:img")
        after = {f["detector"] for f in app.findings(mem_dir=mem_dir)}
        assert "large_dom" not in after
        assert "a11y_missing_alt" not in after
        assert "a11y_unlabeled_control" in after
        assert "slow_lcp" in after


def test_report_html_generates_self_contained_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run_v3"
        run_dir.mkdir()
        (run_dir / "report.json").write_text(
            json.dumps({"outcome": "CONFIRMED", "summary": "All flows verified"}),
            encoding="utf-8",
        )
        (run_dir / "trace1.jsonl").write_text(
            json.dumps(
                {
                    "type": "decision",
                    "t": 1.2,
                    "step": "login",
                    "option": "submit_login",
                    "ok": True,
                    "findings": [
                        {
                            "detector": "a11y_missing_alt",
                            "signature": "a11y_missing_alt:img",
                            "severity": "warning",
                            "message": "1 <img> missing alt",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "trace1_summary.txt").write_text("Step login: PASS\n", encoding="utf-8")
        # Minimal 1x1 PNG bytes
        (run_dir / "frame_01.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

        out_path = Path(report_html.generate_html(run_dir))
        assert out_path.is_file()
        html_text = out_path.read_text(encoding="utf-8")
        assert "CONFIRMED" in html_text
        assert "submit_login" in html_text
        assert "a11y_missing_alt" in html_text
        assert "data:image/png;base64," in html_text


def test_shared_cdp_ownership_does_not_close_shared_session() -> None:
    class _FakeCDP:
        connected = True

        def __init__(self) -> None:
            self.closed = False
            self.reset_count = 0

        def reset_page(self) -> None:
            self.reset_count += 1

        def close(self) -> None:
            self.closed = True

    fake = _FakeCDP()
    eng = WebEngine(use_cdp=False, shared_cdp=fake)  # type: ignore[arg-type]
    eng.close()
    assert fake.closed is False
    assert fake.reset_count == 1


def test_mcp_exposes_and_runs_doctor_tool() -> None:
    names = {t["name"] for t in mcp.tools_list()}
    assert "doctor" in names
    out = io.StringIO()
    srv = mcp.MCPServer(out=out)
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"fix": False}},
        }
    )
    resp = json.loads(out.getvalue().strip())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "checks" in payload
    assert payload["version"].startswith("3.")

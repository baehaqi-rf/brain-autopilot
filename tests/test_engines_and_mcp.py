"""Tests for Engine Auto-Detection (Web, Mobile, 3D), Blind Vision isolation, and Stdio MCP Server."""

from __future__ import annotations

import io
import json
from pathlib import Path

from brain_autopilot import engines, mcp, vision


def test_engine_auto_detection(tmp_path: Path) -> None:
    # 1. Expo / React Native -> MobileEngine
    expo_dir = tmp_path / "expo-app"
    expo_dir.mkdir()
    (expo_dir / "package.json").write_text('{"dependencies": {"expo": "~52.0.0", "react-native": "0.76.0"}}', encoding="utf-8")
    assert engines.detect_engine(expo_dir).name == "mobile"

    # 2. Flutter -> MobileEngine
    flutter_dir = tmp_path / "flutter-app"
    flutter_dir.mkdir()
    (flutter_dir / "pubspec.yaml").write_text("name: my_flutter_app\n", encoding="utf-8")
    assert engines.detect_engine(flutter_dir).name == "mobile"

    # 3. Three.js / R3F -> WebGL3DEngine
    webgl_dir = tmp_path / "three-app"
    webgl_dir.mkdir()
    (webgl_dir / "package.json").write_text('{"dependencies": {"three": "^0.170.0", "@react-three/fiber": "^8.0.0"}}', encoding="utf-8")
    assert engines.detect_engine(webgl_dir).name == "webgl3d"

    # 4. Next.js -> WebEngine
    next_dir = tmp_path / "next-app"
    next_dir.mkdir()
    (next_dir / "package.json").write_text('{"dependencies": {"next": "15.1.0", "react": "19.0.0"}}', encoding="utf-8")
    assert engines.detect_engine(next_dir).name == "web"


def test_web_dom_and_mobile_uiautomator_parsers() -> None:
    web = engines.WebEngine()
    web.load_html(
        '<html><body><h1>Checkout</h1>'
        '<input id="email" placeholder="Email" />'
        '<button data-testid="pay-btn">Pay Now</button>'
        '<button id="disabled-btn" disabled>Locked</button>'
        "</body></html>",
        route="/checkout",
    )
    assert web.get_route() == "/checkout"
    assert web.element_exists("[data-testid='pay-btn']")
    assert web.get_property("#disabled-btn", "disabled") is True
    assert web.act_on_ui("#disabled-btn", "click") is False
    assert web.act_on_ui("#email", "fill", "user@example.com") is True
    assert web.get_property("#email", "value") == "user@example.com"

    mob = engines.MobileEngine()
    mob.load_uiautomator_xml(
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<hierarchy>"
        '<node text="Sign In" resource-id="btn_login" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,200][300,260]" />'
        '<node text="Tiny" resource-id="btn_tiny" class="android.widget.Button" clickable="true" enabled="true" bounds="[10,10][30,30]" />'
        "</hierarchy>"
    )
    assert mob.element_exists("btn_login")
    assert mob.get_metrics()["touch_target_violations"] == 1


def test_blind_vision_prompt_isolation() -> None:
    frames = vision.Frames()
    frames.add(b"fake_jpeg_1", app_time=1.2)
    frames.add(b"fake_jpeg_2", app_time=2.4)
    selected = frames.select()

    payload = vision.build_blind_vision_payload(
        ["What text and buttons are visible in the header?", "Is the button green?"],
        selected,
    )
    assert payload["frame_count"] == 2
    assert len(payload["warnings"]) == 1  # "Is the button green?" flagged as leading/yes-no
    assert "visually broken or anomalous" in payload["prompt"]


def test_mcp_server_initialize_and_start_run(tmp_path: Path) -> None:
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name": "demo"}', encoding="utf-8")

    out = io.StringIO()
    srv = mcp.MCPServer(out=out)
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "start_run",
                "arguments": {
                    "project_root": str(app_dir),
                    "request": "Verify homepage loads",
                    "kind": "verify",
                },
            },
        }
    )
    responses = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert len(responses) == 3
    assert responses[0]["result"]["serverInfo"]["name"] == "brain-autopilot"
    tool_names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert {"start_run", "wait", "probe", "check_harness", "run_harness", "describe_frames", "report"} <= tool_names
    assert responses[2]["result"]["isError"] is False

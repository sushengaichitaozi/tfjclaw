from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "desktop_operator" / "runtime.py"
MODULE_NAMES = (
    "desktop_operator",
    "desktop_operator.browser",
    "desktop_operator.config",
    "desktop_operator.controller",
    "desktop_operator.ocr",
    "desktop_operator.runtime",
    "desktop_operator.ui_automation",
)


class _FakeBrowserManager:
    def __init__(self, config, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.status_calls: list[dict[str, object]] = []

    def status(self, include_snapshot: bool = False, max_elements: int | None = None):
        self.status_calls.append(
            {"include_snapshot": include_snapshot, "max_elements": max_elements}
        )
        return {
            "available": True,
            "connected": True,
            "connected_via": "cdp",
            "title": "Example Browser",
            "url": "https://example.com",
            "tabs": [{"index": 0, "title": "Example Browser", "url": "https://example.com"}],
        }

    def availability(self):
        return {"available": True, "connected": True}

    def cached_snapshot_summary(self, max_elements: int = 8):
        return f"Recent cached DOM snapshot: {max_elements}"

    def close(self):
        return {"closed": True}


class _FakeDesktopController:
    def __init__(self, config, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir

    def dump_action_log(self) -> str:
        return "[]"

    def get_status_snapshot(self):
        return {"screen_width": 1920, "screen_height": 1080}


class _FakeOcrEngine:
    def __init__(self, config, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir

    def status(self):
        return {"available": True}


class _FakeUIAutomationBridge:
    def __init__(self, config) -> None:
        self.config = config
        self.describe_calls: list[dict[str, object]] = []

    def status(self):
        return {"available": True}

    def describe_window(self, title_substring: str, depth: int = 1, limit: int = 10):
        self.describe_calls.append(
            {"title_substring": title_substring, "depth": depth, "limit": limit}
        )
        return {"available": True, "found": True, "controls": []}


def _fake_browser_tool_definitions():
    return []


def _fake_ocr_tool_definitions():
    return []


def _fake_uia_tool_definitions():
    return []


def _install_runtime_stubs() -> dict[str, types.ModuleType | None]:
    saved = {name: sys.modules.get(name) for name in MODULE_NAMES}

    package = types.ModuleType("desktop_operator")
    package.__path__ = [str(ROOT / "desktop_operator")]
    sys.modules["desktop_operator"] = package

    browser_module = types.ModuleType("desktop_operator.browser")
    browser_module.BrowserManager = _FakeBrowserManager
    browser_module.browser_tool_definitions = _fake_browser_tool_definitions
    sys.modules["desktop_operator.browser"] = browser_module

    config_module = types.ModuleType("desktop_operator.config")
    config_module.AgentConfig = object
    sys.modules["desktop_operator.config"] = config_module

    controller_module = types.ModuleType("desktop_operator.controller")
    controller_module.DesktopController = _FakeDesktopController
    controller_module.Observation = object
    sys.modules["desktop_operator.controller"] = controller_module

    ocr_module = types.ModuleType("desktop_operator.ocr")
    ocr_module.OcrEngine = _FakeOcrEngine
    ocr_module.ocr_tool_definitions = _fake_ocr_tool_definitions
    sys.modules["desktop_operator.ocr"] = ocr_module

    uia_module = types.ModuleType("desktop_operator.ui_automation")
    uia_module.UIAutomationBridge = _FakeUIAutomationBridge
    uia_module.uia_tool_definitions = _fake_uia_tool_definitions
    sys.modules["desktop_operator.ui_automation"] = uia_module

    return saved


def _restore_modules(saved: dict[str, types.ModuleType | None]) -> None:
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_runtime_module():
    saved = _install_runtime_stubs()
    spec = importlib.util.spec_from_file_location("desktop_operator.runtime", RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, saved


class RuntimePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls._saved_modules = _load_runtime_module()

    @classmethod
    def tearDownClass(cls) -> None:
        _restore_modules(cls._saved_modules)

    def test_browser_window_prompt_uses_cached_dom_and_skips_uia_dump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            screenshot_path = run_dir / "screen.png"
            Image.new("RGB", (32, 24), color="white").save(screenshot_path)
            config = types.SimpleNamespace(
                max_browser_elements=20,
                include_uia_in_prompt=True,
                prompt_image_quality=70,
                prefer_existing_browser_window=True,
            )
            runtime = self.module.AgentRuntime(config=config, run_dir=run_dir)
            observation = types.SimpleNamespace(
                screenshot_path=screenshot_path,
                width=1920,
                height=1080,
                cursor_x=100,
                cursor_y=200,
                active_window="Chrome - Example",
                active_window_bounds={"left": 10, "top": 20, "width": 1200, "height": 800},
                visible_windows=["Chrome - Example", "Notepad"],
                recent_actions=[],
            )

            message = runtime.build_visual_message(step=1, observation=observation)

            self.assertEqual(
                [{"include_snapshot": False, "max_elements": None}],
                runtime.browser.status_calls,
            )
            text = message["content"][0]["text"]
            self.assertIn("Recent cached DOM snapshot: 8", text)
            self.assertIn("Call browser_snapshot when you need a fresh DOM target list.", text)
            self.assertIn("UI Automation prompt dump skipped", text)
            self.assertEqual([], runtime.uia.describe_calls)


if __name__ == "__main__":
    unittest.main()

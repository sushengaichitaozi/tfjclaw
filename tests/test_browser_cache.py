from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BROWSER_PATH = ROOT / "desktop_operator" / "browser.py"
MODULE_NAMES = (
    "desktop_operator",
    "desktop_operator.config",
    "desktop_operator.browser",
)


def _install_browser_stubs() -> dict[str, types.ModuleType | None]:
    saved = {name: sys.modules.get(name) for name in MODULE_NAMES}

    package = types.ModuleType("desktop_operator")
    package.__path__ = [str(ROOT / "desktop_operator")]
    sys.modules["desktop_operator"] = package

    config_module = types.ModuleType("desktop_operator.config")
    config_module.AgentConfig = object
    sys.modules["desktop_operator.config"] = config_module

    return saved


def _restore_modules(saved: dict[str, types.ModuleType | None]) -> None:
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_browser_module():
    saved = _install_browser_stubs()
    spec = importlib.util.spec_from_file_location("desktop_operator.browser", BROWSER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, saved


class BrowserCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls._saved_modules = _load_browser_module()

    @classmethod
    def tearDownClass(cls) -> None:
        _restore_modules(cls._saved_modules)

    def test_cached_snapshot_summary_uses_last_snapshot_without_live_browser_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = types.SimpleNamespace(
                browser_engine="chromium",
                max_browser_elements=20,
            )
            manager = self.module.BrowserManager(config=config, run_dir=Path(temp_dir))
            manager._last_snapshot = {
                "title": "Example Page",
                "url": "https://example.com",
                "text_excerpt": "alpha beta gamma",
                "elements": [
                    {
                        "agent_id": "dom-1",
                        "tag": "button",
                        "text": "Apply now",
                        "selector": "#apply",
                    }
                ],
            }

            summary = manager.cached_snapshot_summary(max_elements=5)

            self.assertIsNotNone(summary)
            self.assertIn("Recent cached DOM snapshot:", summary)
            self.assertIn("Example Page", summary)
            self.assertIn("dom-1", summary)
            self.assertIn("browser_snapshot", summary)


if __name__ == "__main__":
    unittest.main()

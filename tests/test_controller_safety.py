from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "desktop_operator" / "controller.py"
MODULE_NAMES = (
    "desktop_operator",
    "desktop_operator.config",
    "desktop_operator.controller",
    "pyautogui",
    "pygetwindow",
    "pyperclip",
)


def _install_controller_stubs() -> dict[str, types.ModuleType | None]:
    saved = {name: sys.modules.get(name) for name in MODULE_NAMES}

    package = types.ModuleType("desktop_operator")
    package.__path__ = [str(ROOT / "desktop_operator")]
    sys.modules["desktop_operator"] = package

    config_module = types.ModuleType("desktop_operator.config")
    config_module.AgentConfig = object
    sys.modules["desktop_operator.config"] = config_module

    pyautogui_module = types.ModuleType("pyautogui")
    pyautogui_module.FAILSAFE = True
    pyautogui_module.PAUSE = 0.0
    pyautogui_module.size = lambda: (1920, 1080)
    pyautogui_module.position = lambda: (10, 20)
    pyautogui_module.click = lambda **_: None
    pyautogui_module.moveTo = lambda **_: None
    pyautogui_module.dragTo = lambda **_: None
    pyautogui_module.write = lambda *_, **__: None
    pyautogui_module.press = lambda *_: None
    pyautogui_module.hotkey = lambda *_: None
    pyautogui_module.scroll = lambda *_: None
    sys.modules["pyautogui"] = pyautogui_module

    pygetwindow_module = types.ModuleType("pygetwindow")
    pygetwindow_module.getAllTitles = lambda: []
    pygetwindow_module.getAllWindows = lambda: []
    pygetwindow_module.getActiveWindow = lambda: None
    sys.modules["pygetwindow"] = pygetwindow_module

    pyperclip_module = types.ModuleType("pyperclip")
    pyperclip_module.copy = lambda *_: None
    pyperclip_module.paste = lambda: ""
    sys.modules["pyperclip"] = pyperclip_module

    return saved


def _restore_modules(saved: dict[str, types.ModuleType | None]) -> None:
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_controller_module():
    saved = _install_controller_stubs()
    spec = importlib.util.spec_from_file_location(
        "desktop_operator.controller",
        CONTROLLER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, saved


class ControllerSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls._saved_modules = _load_controller_module()

    @classmethod
    def tearDownClass(cls) -> None:
        _restore_modules(cls._saved_modules)

    def _make_controller(
        self,
        *,
        dry_run: bool = True,
        allow_shell_launch: bool = True,
        allowed_launch_prefixes: tuple[str, ...] = (),
    ):
        config = types.SimpleNamespace(
            dry_run=dry_run,
            action_pause_seconds=0.0,
            allow_shell_launch=allow_shell_launch,
            allowed_launch_prefixes=allowed_launch_prefixes,
            max_saved_screenshots=50,
        )
        temp_dir = tempfile.TemporaryDirectory()
        controller = self.module.DesktopController(config=config, run_dir=Path(temp_dir.name))
        self.addCleanup(temp_dir.cleanup)
        return controller

    def test_open_url_blocks_non_web_schemes(self) -> None:
        controller = self._make_controller()

        result = controller.open_url("file:///C:/Windows/System32/calc.exe")

        self.assertTrue(result["blocked"])
        self.assertIn("http and https", result["reason"])

    def test_open_url_allows_https_in_dry_run(self) -> None:
        controller = self._make_controller(dry_run=True)

        result = controller.open_url("https://example.com/path")

        self.assertEqual("https://example.com/path", result["url"])
        self.assertTrue(result["dry_run"])
        self.assertNotIn("blocked", result)

    def test_allowlist_requires_argument_prefix_match(self) -> None:
        controller = self._make_controller(
            allowed_launch_prefixes=("python.exe -m http.server",),
        )

        rejected = controller._prepare_launch_command("python.exe evil.py")
        allowed = controller._prepare_launch_command("python.exe -m http.server 9000")

        self.assertIn("reason", rejected)
        self.assertNotIn("argv", rejected)
        self.assertEqual(["python.exe", "-m", "http.server", "9000"], allowed["argv"])

    def test_allowlist_does_not_allow_different_path_with_same_basename(self) -> None:
        controller = self._make_controller(
            allowed_launch_prefixes=(r"C:\Tools\safe.exe",),
        )

        rejected = controller._prepare_launch_command(r"C:\Other\safe.exe")

        self.assertIn("reason", rejected)
        self.assertIn("ALLOWED_COMMAND_PREFIXES", rejected["reason"])

    def test_allowlist_still_supports_basename_entries(self) -> None:
        controller = self._make_controller(
            allowed_launch_prefixes=("notepad.exe",),
        )

        allowed = controller._prepare_launch_command(
            r"C:\Windows\System32\notepad.exe notes.txt"
        )

        self.assertEqual(
            [r"C:\Windows\System32\notepad.exe", "notes.txt"],
            allowed["argv"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "desktop_operator" / "config.py"
MODULE_NAME = "desktop_operator_config_test"


def _load_config_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AgentConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_config_module()
        self.original_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        sys.modules.pop(MODULE_NAME, None)

    def test_env_file_does_not_leak_between_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_env = temp_path / "first.env"
            second_env = temp_path / "second.env"

            first_env.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=first-key",
                        "DESKTOP_AGENT_MODEL=model-a",
                        "OPENAI_BASE_URL=https://a.example/v1",
                    ]
                ),
                encoding="utf-8",
            )
            second_env.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=second-key",
                        "DESKTOP_AGENT_MODEL=model-b",
                    ]
                ),
                encoding="utf-8",
            )

            for key in (
                "OPENAI_API_KEY",
                "DESKTOP_AGENT_MODEL",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
            ):
                os.environ.pop(key, None)

            first = self.module.AgentConfig.from_env(first_env)
            second = self.module.AgentConfig.from_env(second_env)

            self.assertEqual("https://a.example/v1", first.base_url)
            self.assertIsNone(second.base_url)
            self.assertEqual("model-b", second.model)

    def test_parses_new_runtime_budget_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "runtime.env"
            env_file.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-key",
                        "DESKTOP_AGENT_MODEL=test-model",
                        "DESKTOP_AGENT_MAX_HISTORY_MESSAGES=77",
                        "DESKTOP_AGENT_MAX_SAVED_SCREENSHOTS=15",
                        "DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES=notepad.exe, calc.exe",
                        "DESKTOP_AGENT_AUTO_RUN_DOCTOR=true",
                    ]
                ),
                encoding="utf-8",
            )

            config = self.module.AgentConfig.from_env(env_file)

            self.assertEqual(77, config.max_history_messages)
            self.assertEqual(15, config.max_saved_screenshots)
            self.assertEqual(("notepad.exe", "calc.exe"), config.allowed_launch_prefixes)
            self.assertTrue(config.auto_run_doctor)

    def test_resolves_relative_paths_from_env_file_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_root = Path(temp_dir) / "nested" / "config"
            env_root.mkdir(parents=True)
            env_file = env_root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-key",
                        "DESKTOP_AGENT_MODEL=test-model",
                        "DESKTOP_AGENT_RUNS_DIR=./runs-here",
                        "DESKTOP_AGENT_BROWSER_USER_DATA_DIR=./browser-state",
                        "DESKTOP_AGENT_TESSERACT_CMD=./tools/tesseract.exe",
                        "TESSDATA_PREFIX=./ocr-data",
                    ]
                ),
                encoding="utf-8",
            )

            config = self.module.AgentConfig.from_env(env_file)

            self.assertEqual((env_root / "runs-here").resolve(), config.runs_dir)
            self.assertEqual(
                (env_root / "browser-state").resolve(),
                config.browser_user_data_dir,
            )
            self.assertEqual(
                str((env_root / "tools" / "tesseract.exe").resolve()),
                config.tesseract_cmd,
            )
            self.assertEqual((env_root / "ocr-data").resolve(), config.tessdata_prefix)

    def test_env_file_can_set_tessdata_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_root = Path(temp_dir)
            env_file = env_root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-key",
                        "DESKTOP_AGENT_MODEL=test-model",
                        "TESSDATA_PREFIX=./tessdata",
                    ]
                ),
                encoding="utf-8",
            )

            config = self.module.AgentConfig.from_env(env_file)

            self.assertEqual((env_root / "tessdata").resolve(), config.tessdata_prefix)


if __name__ == "__main__":
    unittest.main()

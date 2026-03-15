from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OCR_PATH = ROOT / "desktop_operator" / "ocr.py"
MODULE_NAMES = (
    "desktop_operator",
    "desktop_operator.config",
    "desktop_operator.ocr",
    "pyautogui",
    "pytesseract",
    "PIL",
    "PIL.Image",
)


def _install_ocr_stubs() -> dict[str, types.ModuleType | None]:
    saved = {name: sys.modules.get(name) for name in MODULE_NAMES}

    package = types.ModuleType("desktop_operator")
    package.__path__ = [str(ROOT / "desktop_operator")]
    sys.modules["desktop_operator"] = package

    config_module = types.ModuleType("desktop_operator.config")
    config_module.AgentConfig = object
    sys.modules["desktop_operator.config"] = config_module

    pyautogui_module = types.ModuleType("pyautogui")
    pyautogui_module.screenshot = lambda *_, **__: None
    sys.modules["pyautogui"] = pyautogui_module

    pytesseract_module = types.ModuleType("pytesseract")
    pytesseract_module.Output = types.SimpleNamespace(DICT="DICT")
    pytesseract_module.TesseractNotFoundError = type(
        "TesseractNotFoundError",
        (Exception,),
        {},
    )
    pytesseract_module.pytesseract = types.SimpleNamespace(tesseract_cmd="")
    pytesseract_module.get_tesseract_version = lambda: "5.0"
    sys.modules["pytesseract"] = pytesseract_module

    pil_module = types.ModuleType("PIL")
    image_module = types.ModuleType("PIL.Image")
    image_module.Image = object
    pil_module.Image = image_module
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = image_module

    return saved


def _restore_modules(saved: dict[str, types.ModuleType | None]) -> None:
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_ocr_module():
    saved = _install_ocr_stubs()
    spec = importlib.util.spec_from_file_location("desktop_operator.ocr", OCR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, saved


class OcrEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls._saved_modules = _load_ocr_module()

    @classmethod
    def tearDownClass(cls) -> None:
        _restore_modules(cls._saved_modules)

    def setUp(self) -> None:
        self.original_tessdata_prefix = os.environ.get("TESSDATA_PREFIX")

    def tearDown(self) -> None:
        if self.original_tessdata_prefix is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = self.original_tessdata_prefix

    def test_init_applies_tessdata_prefix_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tessdata_path = Path(temp_dir) / "tessdata"
            config = types.SimpleNamespace(
                tesseract_cmd=None,
                tessdata_prefix=tessdata_path,
                ocr_lang="eng",
            )

            self.module.OcrEngine(config=config, run_dir=Path(temp_dir) / "run")

            self.assertEqual(str(tessdata_path), os.environ["TESSDATA_PREFIX"])


if __name__ == "__main__":
    unittest.main()

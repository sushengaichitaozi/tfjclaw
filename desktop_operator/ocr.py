from __future__ import annotations

from pathlib import Path
from typing import Any

import pyautogui
import pytesseract
from PIL import Image
from pytesseract import Output
from pytesseract import TesseractNotFoundError

from .config import AgentConfig


class OcrEngine:
    def __init__(self, config: AgentConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

    def status(self) -> dict[str, Any]:
        try:
            version = str(pytesseract.get_tesseract_version())
            return {
                "available": True,
                "tesseract_cmd": pytesseract.pytesseract.tesseract_cmd,
                "version": version,
                "default_lang": self.config.ocr_lang,
            }
        except TesseractNotFoundError as exc:
            return {
                "available": False,
                "tesseract_cmd": pytesseract.pytesseract.tesseract_cmd,
                "error": str(exc),
            }

    def extract_text(
        self,
        image_path: str | None = None,
        region: list[int] | None = None,
        lang: str | None = None,
        psm: int | None = None,
    ) -> dict[str, Any]:
        image = self._load_image(image_path=image_path, region=region)
        available = self.status()
        if not available["available"]:
            return available

        text = pytesseract.image_to_string(
            image,
            lang=lang or self.config.ocr_lang,
            config=self._tesseract_config(psm=psm),
        )
        return {
            "available": True,
            "text": text.strip(),
            "lang": lang or self.config.ocr_lang,
            "region": region,
            "image_path": image_path,
        }

    def find_text(
        self,
        text: str,
        image_path: str | None = None,
        region: list[int] | None = None,
        lang: str | None = None,
        partial_match: bool = True,
        psm: int | None = None,
    ) -> dict[str, Any]:
        image = self._load_image(image_path=image_path, region=region)
        available = self.status()
        if not available["available"]:
            return available

        data = pytesseract.image_to_data(
            image,
            lang=lang or self.config.ocr_lang,
            config=self._tesseract_config(psm=psm),
            output_type=Output.DICT,
        )

        query = text.strip().lower()
        matches: list[dict[str, Any]] = []
        for index, raw_text in enumerate(data["text"]):
            candidate = raw_text.strip()
            if not candidate:
                continue

            normalized = candidate.lower()
            matched = normalized == query
            if partial_match:
                matched = matched or query in normalized

            if matched:
                matches.append(
                    {
                        "text": candidate,
                        "left": int(data["left"][index]),
                        "top": int(data["top"][index]),
                        "width": int(data["width"][index]),
                        "height": int(data["height"][index]),
                        "confidence": float(data["conf"][index]),
                    }
                )

        return {
            "available": True,
            "query": text,
            "matches": matches,
            "count": len(matches),
            "lang": lang or self.config.ocr_lang,
            "region": region,
            "image_path": image_path,
        }

    def _load_image(
        self,
        image_path: str | None = None,
        region: list[int] | None = None,
    ) -> Image.Image:
        if image_path:
            return Image.open(image_path)
        if region:
            left, top, width, height = region
            return pyautogui.screenshot(region=(left, top, width, height))
        return pyautogui.screenshot()

    def _tesseract_config(self, psm: int | None) -> str:
        if psm is None:
            return ""
        return f"--psm {psm}"


def ocr_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "ocr_extract_text",
                "description": "Run OCR on the screen, a screenshot path, or a screen region.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_path": {"type": "string"},
                        "region": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "[left, top, width, height]",
                        },
                        "lang": {"type": "string"},
                        "psm": {"type": "integer", "minimum": 0, "maximum": 13},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ocr_find_text",
                "description": "Find matching text on the screen, a screenshot path, or a screen region.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "image_path": {"type": "string"},
                        "region": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "[left, top, width, height]",
                        },
                        "lang": {"type": "string"},
                        "partial_match": {"type": "boolean", "default": True},
                        "psm": {"type": "integer", "minimum": 0, "maximum": 13},
                    },
                    "required": ["text"],
                },
            },
        },
    ]

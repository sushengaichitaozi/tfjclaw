from __future__ import annotations

from typing import Any

import pyautogui
import pyperclip

try:
    from pywinauto import Desktop

    UIA_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on runtime
    Desktop = None
    UIA_IMPORT_ERROR = str(exc)

from .config import AgentConfig


class UIAutomationBridge:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def status(self) -> dict[str, Any]:
        available = Desktop is not None
        result = {"available": available}
        if not available:
            result["error"] = UIA_IMPORT_ERROR or "pywinauto is not installed"
            return result

        try:
            desktop = Desktop(backend="uia")
            windows = []
            for window in desktop.windows()[:5]:
                title = (window.window_text() or "").strip()
                if title:
                    windows.append(title)
            result["sample_windows"] = windows
        except Exception as exc:  # pragma: no cover - depends on COM state
            result["error"] = str(exc)
        return result

    def list_windows(self, limit: int = 20) -> dict[str, Any]:
        desktop = self._desktop()
        windows = []
        for window in desktop.windows():
            title = (window.window_text() or "").strip()
            if not title:
                continue
            windows.append(self._control_to_dict(window, depth=0))
            if len(windows) >= limit:
                break
        return {"available": True, "windows": windows, "count": len(windows)}

    def describe_window(
        self,
        title_substring: str,
        depth: int = 1,
        limit: int = 30,
    ) -> dict[str, Any]:
        window = self._find_window(title_substring)
        if window is None:
            return {
                "available": True,
                "found": False,
                "title_substring": title_substring,
            }

        controls: list[dict[str, Any]] = []
        self._walk_controls(window, controls, current_depth=0, max_depth=depth, limit=limit)
        return {
            "available": True,
            "found": True,
            "window": self._control_to_dict(window, depth=0),
            "controls": controls[:limit],
            "count": len(controls[:limit]),
        }

    def click_control(
        self,
        window_title_substring: str,
        name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        index: int = 0,
    ) -> dict[str, Any]:
        window = self._find_window(window_title_substring)
        if window is None:
            return {"available": True, "found": False, "window_title_substring": window_title_substring}

        matches = self._find_controls(
            window=window,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
        )
        if index < 0 or index >= len(matches):
            return {
                "available": True,
                "found": False,
                "window_title_substring": window_title_substring,
                "match_count": len(matches),
            }

        target = matches[index]
        if self.config.dry_run:
            return {
                "available": True,
                "found": True,
                "dry_run": True,
                "target": self._control_to_dict(target, depth=1),
            }

        target.set_focus()
        target.click_input()
        return {
            "available": True,
            "found": True,
            "target": self._control_to_dict(target, depth=1),
        }

    def type_into_control(
        self,
        window_title_substring: str,
        text: str,
        name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        index: int = 0,
        clear: bool = True,
    ) -> dict[str, Any]:
        window = self._find_window(window_title_substring)
        if window is None:
            return {"available": True, "found": False, "window_title_substring": window_title_substring}

        matches = self._find_controls(
            window=window,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
        )
        if index < 0 or index >= len(matches):
            return {
                "available": True,
                "found": False,
                "window_title_substring": window_title_substring,
                "match_count": len(matches),
            }

        target = matches[index]
        if self.config.dry_run:
            return {
                "available": True,
                "found": True,
                "dry_run": True,
                "text": text,
                "target": self._control_to_dict(target, depth=1),
            }

        target.set_focus()
        try:
            if clear and hasattr(target, "set_edit_text"):
                target.set_edit_text(text)
            else:
                target.click_input()
                if clear:
                    pyautogui.hotkey("ctrl", "a")
                    pyautogui.press("backspace")
                previous = pyperclip.paste()
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                pyperclip.copy(previous)
        except Exception:
            target.click_input()
            if clear:
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
            previous = pyperclip.paste()
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            pyperclip.copy(previous)

        return {
            "available": True,
            "found": True,
            "text": text,
            "target": self._control_to_dict(target, depth=1),
        }

    def _desktop(self):
        if Desktop is None:
            raise RuntimeError(UIA_IMPORT_ERROR or "pywinauto is not installed")
        return Desktop(backend="uia")

    def _find_window(self, title_substring: str):
        lowered = title_substring.lower()
        for window in self._desktop().windows():
            title = (window.window_text() or "").strip()
            if title and lowered in title.lower():
                return window
        return None

    def _find_controls(
        self,
        window,
        name: str | None,
        automation_id: str | None,
        control_type: str | None,
    ) -> list[Any]:
        matches = []
        for control in window.descendants():
            info = self._control_to_dict(control, depth=1)
            if name and name.lower() not in info["name"].lower():
                continue
            if automation_id and automation_id != info["automation_id"]:
                continue
            if control_type and control_type.lower() != info["control_type"].lower():
                continue
            matches.append(control)
        return matches

    def _walk_controls(
        self,
        wrapper,
        output: list[dict[str, Any]],
        current_depth: int,
        max_depth: int,
        limit: int,
    ) -> None:
        if len(output) >= limit:
            return
        if current_depth > 0:
            output.append(self._control_to_dict(wrapper, depth=current_depth))
        if current_depth >= max_depth:
            return
        try:
            children = wrapper.children()
        except Exception:  # pragma: no cover - depends on control tree
            children = []
        for child in children:
            self._walk_controls(
                child,
                output=output,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                limit=limit,
            )
            if len(output) >= limit:
                return

    def _control_to_dict(self, wrapper, depth: int) -> dict[str, Any]:
        try:
            rectangle = wrapper.rectangle()
            bounds = {
                "left": rectangle.left,
                "top": rectangle.top,
                "right": rectangle.right,
                "bottom": rectangle.bottom,
            }
        except Exception:  # pragma: no cover - depends on wrapper type
            bounds = None

        info = getattr(wrapper, "element_info", None)
        name = ""
        automation_id = ""
        control_type = ""
        class_name = ""
        if info is not None:
            name = (getattr(info, "name", "") or "").strip()
            automation_id = (getattr(info, "automation_id", "") or "").strip()
            control_type = (getattr(info, "control_type", "") or "").strip()
            class_name = (getattr(info, "class_name", "") or "").strip()

        if not name:
            try:
                name = (wrapper.window_text() or "").strip()
            except Exception:  # pragma: no cover - depends on wrapper type
                name = ""

        return {
            "name": name,
            "automation_id": automation_id,
            "control_type": control_type,
            "class_name": class_name,
            "bounds": bounds,
            "depth": depth,
        }


def uia_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "uia_list_windows",
                "description": "List top-level Windows UI Automation windows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uia_describe_window",
                "description": "Inspect a window and return its controls from the Windows UI Automation tree.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title_substring": {"type": "string"},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                    },
                    "required": ["title_substring"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uia_click_control",
                "description": "Click a Windows UI Automation control by name, automation id, or control type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window_title_substring": {"type": "string"},
                        "name": {"type": "string"},
                        "automation_id": {"type": "string"},
                        "control_type": {"type": "string"},
                        "index": {"type": "integer", "minimum": 0, "default": 0},
                    },
                    "required": ["window_title_substring"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uia_type_into_control",
                "description": "Type into a Windows UI Automation control by name, automation id, or control type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window_title_substring": {"type": "string"},
                        "text": {"type": "string"},
                        "name": {"type": "string"},
                        "automation_id": {"type": "string"},
                        "control_type": {"type": "string"},
                        "index": {"type": "integer", "minimum": 0, "default": 0},
                        "clear": {"type": "boolean", "default": True},
                    },
                    "required": ["window_title_substring", "text"],
                },
            },
        },
    ]

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from .browser import BrowserManager, browser_tool_definitions
from .config import AgentConfig
from .controller import DesktopController, Observation
from .ocr import OcrEngine, ocr_tool_definitions
from .ui_automation import UIAutomationBridge, uia_tool_definitions

BROWSER_WINDOW_HINTS = (
    "chrome",
    "edge",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "browser",
)


def desktop_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "click",
                "description": "Click a position on the screen.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "default": "left",
                        },
                        "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                    },
                    "required": ["x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "move_mouse",
                "description": "Move the mouse cursor to a position.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "duration": {"type": "number", "default": 0.08},
                    },
                    "required": ["x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "drag_mouse",
                "description": "Drag the mouse to a position while holding a button.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "duration": {"type": "number", "default": 0.12},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "default": "left",
                        },
                    },
                    "required": ["x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "type_text",
                "description": "Type or paste text into the focused input on the Windows desktop.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "interval": {"type": "number", "default": 0.02},
                        "method": {
                            "type": "string",
                            "enum": ["auto", "type", "paste"],
                            "default": "auto",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "press_key",
                "description": "Press a single keyboard key.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "hotkey",
                "description": "Press a keyboard shortcut such as ctrl+s.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    "required": ["keys"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scroll",
                "description": "Scroll on the Windows desktop. Positive values scroll up, negative values scroll down.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["amount"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wait",
                "description": "Wait for UI changes or network loading.",
                "parameters": {
                    "type": "object",
                    "properties": {"seconds": {"type": "number"}},
                    "required": ["seconds"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_url",
                "description": "Open a URL in the default browser.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "launch_program",
                "description": "Launch a local Windows program or shell command if shell launching is enabled in config.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_windows",
                "description": "List currently visible window titles from the desktop layer.",
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
                "name": "focus_window",
                "description": "Bring a window to the foreground using part of its title.",
                "parameters": {
                    "type": "object",
                    "properties": {"title_substring": {"type": "string"}},
                    "required": ["title_substring"],
                },
            },
        },
    ]


class AgentRuntime:
    def __init__(self, config: AgentConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.controller = DesktopController(config=config, run_dir=run_dir / "desktop")
        self.browser = BrowserManager(config=config, run_dir=run_dir / "browser")
        self.ocr = OcrEngine(config=config, run_dir=run_dir / "ocr")
        self.uia = UIAutomationBridge(config=config)
        self.latest_observation: Observation | None = None
        self._uia_cache_key: tuple[str, tuple[int, int, int, int] | None] | None = None
        self._uia_cache_section = ""
        self._tool_map = {
            "click": lambda arguments: self.controller.click(**arguments),
            "move_mouse": lambda arguments: self.controller.move_mouse(**arguments),
            "drag_mouse": lambda arguments: self.controller.drag_mouse(**arguments),
            "type_text": lambda arguments: self.controller.type_text(**arguments),
            "press_key": lambda arguments: self.controller.press_key(**arguments),
            "hotkey": lambda arguments: self.controller.hotkey(**arguments),
            "scroll": lambda arguments: self.controller.scroll(**arguments),
            "wait": lambda arguments: self.controller.wait(**arguments),
            "open_url": lambda arguments: self.controller.open_url(**arguments),
            "launch_program": lambda arguments: self.controller.launch_program(**arguments),
            "list_windows": lambda arguments: self.controller.list_windows(**arguments),
            "focus_window": lambda arguments: self.controller.focus_window(**arguments),
            "browser_launch": lambda arguments: self._browser_launch(arguments),
            "browser_connect_cdp": lambda arguments: self.browser.connect_cdp(**arguments),
            "browser_status": lambda arguments: self.browser.status(**arguments),
            "browser_navigate": lambda arguments: self.browser.navigate(**arguments),
            "browser_snapshot": lambda arguments: self.browser.snapshot(**arguments),
            "browser_click": lambda arguments: self.browser.click(**arguments),
            "browser_type": lambda arguments: self.browser.type_text(**arguments),
            "browser_press": lambda arguments: self.browser.press(**arguments),
            "browser_scroll": lambda arguments: self.browser.scroll(**arguments),
            "browser_list_tabs": lambda arguments: self.browser.list_tabs(),
            "browser_switch_tab": lambda arguments: self.browser.switch_tab(**arguments),
            "browser_close": lambda arguments: self.browser.close(),
            "ocr_extract_text": lambda arguments: self._ocr_extract_text(arguments),
            "ocr_find_text": lambda arguments: self._ocr_find_text(arguments),
            "uia_list_windows": lambda arguments: self.uia.list_windows(**arguments),
            "uia_describe_window": lambda arguments: self.uia.describe_window(**arguments),
            "uia_click_control": lambda arguments: self.uia.click_control(**arguments),
            "uia_type_into_control": lambda arguments: self.uia.type_into_control(**arguments),
        }

    def capture_observation(self, step: int) -> Observation:
        self.latest_observation = self.controller.capture_observation(step=step)
        return self.latest_observation

    def tool_definitions(self) -> list[dict[str, Any]]:
        return (
            desktop_tool_definitions()
            + browser_tool_definitions()
            + ocr_tool_definitions()
            + uia_tool_definitions()
        )

    def execute_tool_call(self, tool_name: str, raw_arguments: str) -> dict[str, Any]:
        if tool_name not in self._tool_map:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return {
                "error": f"Invalid JSON arguments: {exc.msg}",
                "tool_name": tool_name,
                "raw_arguments": raw_arguments,
            }

        if not isinstance(arguments, dict):
            return {
                "error": "Tool arguments must decode to a JSON object.",
                "tool_name": tool_name,
                "arguments": arguments,
            }

        try:
            return self._tool_map[tool_name](arguments)
        except Exception as exc:  # pragma: no cover - depends on runtime
            return {"error": str(exc), "tool_name": tool_name, "arguments": arguments}

    def close(self) -> dict[str, Any]:
        result = {"browser_closed": False}
        try:
            if (
                getattr(self.browser, "_context", None) is not None
                or getattr(self.browser, "_playwright", None) is not None
            ):
                browser_result = self.browser.close()
                result["browser_closed"] = bool(browser_result.get("closed"))
        except Exception as exc:  # pragma: no cover - depends on runtime
            result["browser_close_error"] = str(exc)
        return result

    def build_visual_message(self, step: int, observation: Observation) -> dict[str, Any]:
        sections: list[str] = [
            f"Current step: {step}",
            f"Screen size: {observation.width}x{observation.height}",
            "Screenshot mapping: the attached screenshot is sent without spatial resizing, so coordinates map 1:1 to screen pixels.",
            f"Cursor: ({observation.cursor_x}, {observation.cursor_y})",
            f"Active window: {observation.active_window or '<none>'}",
            f"Visible windows: {', '.join(observation.visible_windows) or '<none>'}",
            f"Recent actions: {json.dumps(observation.recent_actions, ensure_ascii=False)}",
        ]
        browser_windows = self._visible_browser_windows(observation)
        sections.append(
            "Visible browser windows: "
            + (", ".join(browser_windows) if browser_windows else "<none>")
        )
        if browser_windows:
            sections.append(
                "A browser window is already open on screen. Reuse that existing window with focus_window plus desktop, OCR, or UI Automation tools unless the user explicitly asked for a separate Playwright browser."
            )

        if observation.active_window_bounds:
            bounds = observation.active_window_bounds
            sections.append(
                "Active window bounds: "
                f"left={bounds['left']} top={bounds['top']} width={bounds['width']} height={bounds['height']}"
            )

        browser_status = self.browser.status(include_snapshot=False)
        sections.append(self._format_browser_section(browser_status))
        cached_browser_snapshot = self.browser.cached_snapshot_summary(
            max_elements=min(8, self.config.max_browser_elements)
        )
        if cached_browser_snapshot:
            sections.append(cached_browser_snapshot)

        should_include_uia = (
            self.config.include_uia_in_prompt
            and observation.active_window
            and observation.active_window not in browser_windows
        )
        if should_include_uia:
            sections.append(
                self._uia_section_for_window(
                    observation.active_window,
                    observation.active_window_bounds,
                )
            )
        elif observation.active_window and observation.active_window in browser_windows:
            sections.append(
                "Windows UI Automation prompt dump skipped for the active browser window. Use browser_snapshot for DOM inspection first."
            )
        else:
            sections.append(
                f"Windows UI Automation available: {self.uia.status().get('available', False)}"
            )

        sections.append(f"OCR available: {self.ocr.status().get('available', False)}")
        sections.append(
            "Use DOM tools or UI Automation before blind coordinate clicks whenever possible."
        )

        return {
            "role": "user",
            "content": [
                {"type": "text", "text": "\n\n".join(sections)},
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_path_to_data_url(observation.screenshot_path)},
                },
            ],
        }

    def doctor_report(self) -> dict[str, Any]:
        return {
            "desktop": self.controller.get_status_snapshot(),
            "browser": self.browser.availability(),
            "ocr": self.ocr.status(),
            "uia": self.uia.status(),
        }

    def dump_action_log(self) -> str:
        return self.controller.dump_action_log()

    def _browser_launch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = dict(arguments)
        force = bool(payload.pop("force", False))
        if self.config.prefer_existing_browser_window and not force:
            browser_windows = self._visible_browser_windows()
            if browser_windows:
                return {
                    "blocked": True,
                    "reason": (
                        "A browser window is already visible on the desktop. "
                        "Reuse the existing browser window instead of launching a separate Playwright browser."
                    ),
                    "visible_browser_windows": browser_windows,
                    "suggested_actions": [
                        "focus_window",
                        "click",
                        "type_text",
                        "ocr_find_text",
                        "uia_describe_window",
                        "browser_connect_cdp",
                    ],
                    "override": "browser_launch(force=true)",
                }
        return self.browser.launch(**payload)

    def _ocr_extract_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(arguments)
        arguments.setdefault("image_path", self._default_ocr_image_path(arguments))
        return self.ocr.extract_text(**arguments)

    def _ocr_find_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(arguments)
        arguments.setdefault("image_path", self._default_ocr_image_path(arguments))
        return self.ocr.find_text(**arguments)

    def _default_ocr_image_path(self, arguments: dict[str, Any]) -> str | None:
        if arguments.get("image_path") or arguments.get("region"):
            return arguments.get("image_path")
        if self.latest_observation is None:
            return None
        return str(self.latest_observation.screenshot_path)

    def _visible_browser_windows(self, observation: Observation | None = None) -> list[str]:
        snapshot = observation or self.latest_observation
        if snapshot is None:
            return []

        windows: list[str] = []
        candidates = [snapshot.active_window, *snapshot.visible_windows]
        seen: set[str] = set()
        for raw_title in candidates:
            title = (raw_title or "").strip()
            if not title:
                continue
            lowered = title.lower()
            if title in seen:
                continue
            if any(hint in lowered for hint in BROWSER_WINDOW_HINTS):
                windows.append(title)
                seen.add(title)
        return windows

    def _format_browser_section(self, browser_status: dict[str, Any]) -> str:
        if not browser_status.get("connected"):
            if browser_status.get("error"):
                return f"Browser automation unavailable: {browser_status['error']}"
            if self.config.prefer_existing_browser_window and self._visible_browser_windows():
                return (
                    "Browser automation not connected. An existing browser window is already visible, "
                    "so reuse that window first. Only use browser_connect_cdp or browser_launch(force=true) "
                    "if you explicitly need DOM control in a separate managed browser."
                )
            return (
                "Browser automation not connected. Use browser_launch or browser_connect_cdp "
                "only if no suitable browser window is already open."
            )

        tabs = browser_status.get("tabs", [])
        tabs_summary = ", ".join(
            f"[{tab['index']}] {tab['title'] or '<untitled>'}" for tab in tabs[:5]
        ) or "<none>"
        lines = [
            f"Browser connected via {browser_status.get('connected_via')}.",
            f"Browser page: {browser_status.get('title') or '<untitled>'}",
            f"Browser URL: {browser_status.get('url') or '<none>'}",
            f"Browser tabs: {tabs_summary}",
            "Call browser_snapshot when you need a fresh DOM target list.",
        ]
        return "\n".join(lines)

    def _format_uia_section(self, uia_status: dict[str, Any]) -> str:
        if not uia_status.get("available"):
            return f"Windows UI Automation unavailable: {uia_status.get('error', 'unknown error')}"
        if not uia_status.get("found"):
            return "Windows UI Automation found no matching controls for the active window."

        controls = uia_status.get("controls", [])
        control_lines = []
        for control in controls[:10]:
            label = control.get("name") or "<unnamed>"
            control_lines.append(
                f"{control['control_type'] or '<unknown>'}:{label} automation_id={control['automation_id'] or '<none>'}"
            )
        return "Windows UI Automation controls: " + " | ".join(control_lines)

    def _uia_section_for_window(
        self,
        title: str,
        bounds: dict[str, int] | None = None,
    ) -> str:
        normalized = title.strip()
        bounds_key = None
        if bounds:
            bounds_key = (
                int(bounds.get("left", 0)),
                int(bounds.get("top", 0)),
                int(bounds.get("width", 0)),
                int(bounds.get("height", 0)),
            )
        cache_key = (normalized, bounds_key)

        if normalized and cache_key == self._uia_cache_key and self._uia_cache_section:
            return self._uia_cache_section

        section = self._format_uia_section(
            self.uia.describe_window(
                title_substring=normalized,
                depth=1,
                limit=10,
            )
        )
        self._uia_cache_key = cache_key
        self._uia_cache_section = section
        return section

    def _image_path_to_data_url(self, path: Path) -> str:
        with Image.open(path) as image:
            rendered = image.convert("RGB")
            buffer = io.BytesIO()
            rendered.save(
                buffer,
                format="JPEG",
                quality=self.config.prompt_image_quality,
                optimize=True,
            )

        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

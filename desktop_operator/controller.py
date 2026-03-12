from __future__ import annotations

import json
import os
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyautogui
import pygetwindow as gw
import pyperclip

from .config import AgentConfig

try:
    from pywinauto import Desktop

    UIA_FOCUS_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on runtime
    Desktop = None
    UIA_FOCUS_IMPORT_ERROR = str(exc)


pyautogui.FAILSAFE = True


@dataclass
class Observation:
    screenshot_path: Path
    width: int
    height: int
    cursor_x: int
    cursor_y: int
    active_window: str
    active_window_bounds: dict[str, int] | None
    visible_windows: list[str]
    recent_actions: list[dict[str, Any]]


class DesktopController:
    def __init__(self, config: AgentConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.action_log: list[dict[str, Any]] = []
        pyautogui.PAUSE = self.config.action_pause_seconds

    def capture_observation(self, step: int) -> Observation:
        screenshot_path = self.run_dir / f"step_{step:02d}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(screenshot_path)

        width, height = pyautogui.size()
        cursor_x, cursor_y = pyautogui.position()
        active_window = gw.getActiveWindow()
        active_window_title = ""
        active_window_bounds = None
        if active_window and active_window.title:
            active_window_title = active_window.title.strip()
            active_window_bounds = {
                "left": active_window.left,
                "top": active_window.top,
                "width": active_window.width,
                "height": active_window.height,
            }

        return Observation(
            screenshot_path=screenshot_path,
            width=width,
            height=height,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            active_window=active_window_title,
            active_window_bounds=active_window_bounds,
            visible_windows=self._list_window_titles(limit=10),
            recent_actions=self.action_log[-5:],
        )

    def click(
        self, x: int, y: int, button: str = "left", clicks: int = 1
    ) -> dict[str, Any]:
        x, y = self._clamp_point(x, y)
        if self.config.dry_run:
            return self._record("click", x=x, y=y, button=button, clicks=clicks, dry_run=True)

        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        return self._record("click", x=x, y=y, button=button, clicks=clicks)

    def move_mouse(self, x: int, y: int, duration: float = 0.2) -> dict[str, Any]:
        x, y = self._clamp_point(x, y)
        if self.config.dry_run:
            return self._record("move_mouse", x=x, y=y, duration=duration, dry_run=True)

        pyautogui.moveTo(x=x, y=y, duration=max(0.0, duration))
        return self._record("move_mouse", x=x, y=y, duration=duration)

    def drag_mouse(
        self, x: int, y: int, duration: float = 0.2, button: str = "left"
    ) -> dict[str, Any]:
        x, y = self._clamp_point(x, y)
        if self.config.dry_run:
            return self._record(
                "drag_mouse",
                x=x,
                y=y,
                duration=duration,
                button=button,
                dry_run=True,
            )

        pyautogui.dragTo(x=x, y=y, duration=max(0.0, duration), button=button)
        return self._record("drag_mouse", x=x, y=y, duration=duration, button=button)

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        method: str = "auto",
    ) -> dict[str, Any]:
        input_mode = self._choose_input_mode(text=text, method=method.lower())
        if self.config.dry_run:
            return self._record(
                "type_text",
                text=text,
                interval=interval,
                characters=len(text),
                method=input_mode,
                dry_run=True,
            )

        if input_mode == "paste":
            self._paste_text(text)
        else:
            pyautogui.write(text, interval=max(0.0, interval))

        return self._record(
            "type_text",
            text=text,
            interval=interval,
            characters=len(text),
            method=input_mode,
        )

    def press_key(self, key: str) -> dict[str, Any]:
        if self.config.dry_run:
            return self._record("press_key", key=key, dry_run=True)

        pyautogui.press(key)
        return self._record("press_key", key=key)

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        if self.config.dry_run:
            return self._record("hotkey", keys=keys, dry_run=True)

        pyautogui.hotkey(*keys)
        return self._record("hotkey", keys=keys)

    def scroll(
        self, amount: int, x: int | None = None, y: int | None = None
    ) -> dict[str, Any]:
        if x is not None and y is not None and not self.config.dry_run:
            x, y = self._clamp_point(x, y)
            pyautogui.moveTo(x=x, y=y, duration=0.1)

        if self.config.dry_run:
            return self._record("scroll", amount=amount, x=x, y=y, dry_run=True)

        pyautogui.scroll(amount)
        return self._record("scroll", amount=amount, x=x, y=y)

    def wait(self, seconds: float) -> dict[str, Any]:
        seconds = max(0.0, seconds)
        if not self.config.dry_run:
            time.sleep(seconds)
        return self._record("wait", seconds=seconds, dry_run=self.config.dry_run)

    def open_url(self, url: str) -> dict[str, Any]:
        if self.config.dry_run:
            return self._record("open_url", url=url, dry_run=True)

        if hasattr(os, "startfile"):
            os.startfile(url)
        else:
            webbrowser.open(url)
        return self._record("open_url", url=url)

    def launch_program(self, command: str) -> dict[str, Any]:
        if not self.config.allow_shell_launch:
            return self._record(
                "launch_program",
                command=command,
                blocked=True,
                reason="DESKTOP_AGENT_ALLOW_SHELL is disabled",
            )

        if self.config.dry_run:
            return self._record("launch_program", command=command, dry_run=True)

        process = subprocess.Popen(command, shell=True)
        return self._record("launch_program", command=command, pid=process.pid)

    def list_windows(self, limit: int = 20) -> dict[str, Any]:
        titles = self._list_window_titles(limit=limit)
        return self._record("list_windows", titles=titles, limit=limit)

    def focus_window(self, title_substring: str) -> dict[str, Any]:
        matches = []
        lowered = title_substring.lower()

        for window in gw.getAllWindows():
            title = (window.title or "").strip()
            if not title:
                continue
            if lowered in title.lower():
                matches.append(window)

        if not matches:
            return self._record(
                "focus_window",
                title_substring=title_substring,
                found=False,
            )

        target = matches[0]
        if self.config.dry_run:
            return self._record(
                "focus_window",
                title_substring=title_substring,
                found=True,
                target=target.title,
                dry_run=True,
            )

        try:
            if target.isMinimized:
                target.restore()
            target.activate()
            time.sleep(0.2)
            outcome = self._focus_outcome(
                title_substring=title_substring,
                target_title=target.title,
                method="pygetwindow",
            )
        except Exception as exc:  # pragma: no cover - depends on window state
            outcome = self._focus_outcome(
                title_substring=title_substring,
                target_title=target.title,
                method="pygetwindow",
                error=str(exc),
            )

        if not outcome.get("focused", False):
            fallback = self._focus_window_uia(target.title or title_substring)
            if fallback.get("focused", False):
                outcome = {
                    "title_substring": title_substring,
                    "found": True,
                    "target": fallback.get("target", target.title),
                    "focused": True,
                    "method": "uia",
                    "fallback": True,
                }
            elif fallback.get("error"):
                outcome = {
                    "title_substring": title_substring,
                    "found": True,
                    "target": target.title,
                    "focused": False,
                    "method": "pygetwindow",
                    "error": outcome.get("error") or fallback["error"],
                }

        return self._record("focus_window", **outcome)

    def get_status_snapshot(self) -> dict[str, Any]:
        width, height = pyautogui.size()
        cursor_x, cursor_y = pyautogui.position()
        return {
            "screen_width": width,
            "screen_height": height,
            "cursor_x": cursor_x,
            "cursor_y": cursor_y,
            "active_window": self._get_active_window_title(),
            "visible_windows": self._list_window_titles(limit=10),
            "dry_run": self.config.dry_run,
            "allow_shell_launch": self.config.allow_shell_launch,
        }

    def dump_action_log(self) -> str:
        return json.dumps(self.action_log, ensure_ascii=False, indent=2)

    def _record(self, action: str, **details: Any) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            **details,
        }
        self.action_log.append(event)
        return event

    def _list_window_titles(self, limit: int) -> list[str]:
        seen: set[str] = set()
        titles: list[str] = []
        for raw_title in gw.getAllTitles():
            title = raw_title.strip()
            if not title or title in seen:
                continue
            seen.add(title)
            titles.append(title)
            if len(titles) >= limit:
                break
        return titles

    def _get_active_window_title(self) -> str:
        active = gw.getActiveWindow()
        if active and active.title:
            return active.title.strip()
        return ""

    def _clamp_point(self, x: int, y: int) -> tuple[int, int]:
        width, height = pyautogui.size()
        return max(0, min(int(x), width - 1)), max(0, min(int(y), height - 1))

    def _choose_input_mode(self, text: str, method: str) -> str:
        if method in {"type", "paste"}:
            return method
        if any(ord(char) > 127 for char in text):
            return "paste"
        if "\n" in text or "\t" in text or len(text) > 80:
            return "paste"
        return "type"

    def _paste_text(self, text: str) -> None:
        previous = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        try:
            pyperclip.copy(previous)
        except Exception:  # pragma: no cover - clipboard races
            pass

    def _focus_outcome(
        self,
        title_substring: str,
        target_title: str,
        method: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        active_title = self._get_active_window_title().lower()
        normalized_target = target_title.strip().lower()
        focused = normalized_target in active_title or active_title in normalized_target
        outcome = {
            "title_substring": title_substring,
            "found": True,
            "target": target_title,
            "focused": focused,
            "method": method,
        }
        if error:
            outcome["error"] = error
        return outcome

    def _focus_window_uia(self, title_substring: str) -> dict[str, Any]:
        if Desktop is None:
            return {"focused": False, "error": UIA_FOCUS_IMPORT_ERROR or "pywinauto is not installed"}

        lowered = title_substring.lower()
        try:
            for window in Desktop(backend="uia").windows():
                title = (window.window_text() or "").strip()
                if not title or lowered not in title.lower():
                    continue
                window.set_focus()
                time.sleep(0.2)
                return {
                    "focused": title.lower() in self._get_active_window_title().lower(),
                    "target": title,
                }
        except Exception as exc:  # pragma: no cover - depends on COM state
            return {"focused": False, "error": str(exc)}
        return {"focused": False}

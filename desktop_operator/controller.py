from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
        self._prune_old_screenshots()

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

    def move_mouse(self, x: int, y: int, duration: float = 0.08) -> dict[str, Any]:
        x, y = self._clamp_point(x, y)
        if self.config.dry_run:
            return self._record("move_mouse", x=x, y=y, duration=duration, dry_run=True)

        pyautogui.moveTo(x=x, y=y, duration=max(0.0, duration))
        return self._record("move_mouse", x=x, y=y, duration=duration)

    def drag_mouse(
        self, x: int, y: int, duration: float = 0.12, button: str = "left"
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
        normalized = url.strip()
        validation = self._validate_url(normalized)
        if validation is not None:
            return self._record("open_url", url=url, blocked=True, reason=validation)

        if self.config.dry_run:
            return self._record("open_url", url=normalized, dry_run=True)

        if hasattr(os, "startfile"):
            os.startfile(normalized)
        else:
            webbrowser.open(normalized)
        return self._record("open_url", url=normalized)

    def launch_program(self, command: str) -> dict[str, Any]:
        if not self.config.allow_shell_launch:
            return self._record(
                "launch_program",
                command=command,
                blocked=True,
                reason="DESKTOP_AGENT_ALLOW_SHELL is disabled",
            )

        if self.config.dry_run:
            validated = self._prepare_launch_command(command)
            if "reason" in validated:
                return self._record(
                    "launch_program",
                    command=command,
                    blocked=True,
                    reason=validated["reason"],
                )
            return self._record(
                "launch_program",
                command=command,
                argv=validated["argv"],
                dry_run=True,
            )

        validated = self._prepare_launch_command(command)
        if "reason" in validated:
            return self._record(
                "launch_program",
                command=command,
                blocked=True,
                reason=validated["reason"],
            )

        process = subprocess.Popen(validated["argv"], shell=False)
        return self._record(
            "launch_program",
            command=command,
            argv=validated["argv"],
            pid=process.pid,
        )

    def list_windows(self, limit: int = 20) -> dict[str, Any]:
        titles = self._list_window_titles(limit=limit)
        return self._record("list_windows", titles=titles, limit=limit)

    def focus_window(self, title_substring: str) -> dict[str, Any]:
        target, candidate_titles = self._select_window_match(title_substring)
        if target is None:
            return self._record(
                "focus_window",
                title_substring=title_substring,
                found=False,
                candidate_count=0,
            )

        if self.config.dry_run:
            return self._record(
                "focus_window",
                title_substring=title_substring,
                found=True,
                target=target.title,
                candidate_count=len(candidate_titles),
                candidates=candidate_titles[:5],
                dry_run=True,
            )

        try:
            if target.isMinimized:
                target.restore()
            target.activate()
            time.sleep(0.1)
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

        outcome["candidate_count"] = len(candidate_titles)
        outcome["candidates"] = candidate_titles[:5]
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
                    "candidate_count": len(candidate_titles),
                    "candidates": candidate_titles[:5],
                }
            elif fallback.get("error"):
                outcome = {
                    "title_substring": title_substring,
                    "found": True,
                    "target": target.title,
                    "focused": False,
                    "method": "pygetwindow",
                    "error": outcome.get("error") or fallback["error"],
                    "candidate_count": len(candidate_titles),
                    "candidates": candidate_titles[:5],
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

    def _prune_old_screenshots(self) -> None:
        if self.config.max_saved_screenshots <= 0:
            return

        screenshots = sorted(
            self.run_dir.glob("step_*.png"),
            key=lambda path: int(path.stem.split("_")[-1]),
        )
        if len(screenshots) <= self.config.max_saved_screenshots:
            return

        for old_path in screenshots[: -self.config.max_saved_screenshots]:
            try:
                old_path.unlink()
            except OSError:  # pragma: no cover - depends on filesystem state
                pass

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
        focused = bool(active_title) and (
            normalized_target in active_title or active_title in normalized_target
        )
        outcome = {
            "title_substring": title_substring,
            "found": True,
            "target": target_title,
            "focused": focused,
            "method": method,
            "candidate_count": 1,
        }
        if error:
            outcome["error"] = error
        return outcome

    def _select_window_match(self, title_substring: str) -> tuple[Any | None, list[str]]:
        normalized_query = title_substring.strip().lower()
        if not normalized_query:
            return None, []

        scored_matches: list[tuple[tuple[int, int, int], Any, str]] = []
        active_title = self._get_active_window_title().lower()

        for window in gw.getAllWindows():
            title = (window.title or "").strip()
            if not title:
                continue

            lowered = title.lower()
            if normalized_query not in lowered:
                continue

            if lowered == normalized_query:
                match_rank = 3
            elif lowered.startswith(normalized_query):
                match_rank = 2
            else:
                match_rank = 1

            minimized_penalty = 1 if getattr(window, "isMinimized", False) else 0
            active_bonus = 1 if lowered == active_title else 0
            score = (
                match_rank,
                active_bonus,
                -minimized_penalty,
                -abs(len(lowered) - len(normalized_query)),
            )
            scored_matches.append((score, window, title))

        if not scored_matches:
            return None, []

        scored_matches.sort(key=lambda item: item[0], reverse=True)
        candidate_titles = [title for _, _, title in scored_matches]
        return scored_matches[0][1], candidate_titles

    def _prepare_launch_command(self, command: str) -> dict[str, Any]:
        normalized = command.strip()
        if not normalized:
            return {"reason": "Command is empty."}

        if any(token in normalized for token in ("&&", "||", "|", ">", "<", ";")):
            return {
                "reason": "Shell metacharacters are blocked. Launch a direct executable instead.",
            }

        try:
            argv = shlex.split(normalized, posix=False)
        except ValueError as exc:
            return {"reason": f"Command could not be parsed: {exc}"}

        if not argv:
            return {"reason": "Command is empty."}

        executable = argv[0].strip().strip('"')
        if not executable:
            return {"reason": "Command is empty."}

        allowlisted = self._command_matches_allowed_prefixes(argv)
        if self.config.allowed_launch_prefixes and not allowlisted:
            return {
                "reason": (
                    "Command is not in DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES. "
                    f"Allowed prefixes: {', '.join(self.config.allowed_launch_prefixes)}"
                )
            }

        if self._is_high_risk_launcher(executable) and not allowlisted:
            return {
                "reason": (
                    "High-risk launchers and script hosts are blocked unless allowlisted in "
                    "DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES."
                )
            }

        return {"argv": argv}

    def _command_matches_allowed_prefixes(self, argv: list[str]) -> bool:
        if not argv:
            return False

        for raw_prefix in self.config.allowed_launch_prefixes:
            prefix = raw_prefix.strip()
            if not prefix:
                continue
            try:
                prefix_argv = shlex.split(prefix, posix=False)
            except ValueError:
                continue
            if not prefix_argv or len(prefix_argv) > len(argv):
                continue

            if not self._executables_match(prefix_argv[0], argv[0]):
                continue

            if all(
                self._normalize_argument_token(prefix_argv[index])
                == self._normalize_argument_token(argv[index])
                for index in range(1, len(prefix_argv))
            ):
                return True
        return False

    def _validate_url(self, url: str) -> str | None:
        if not url:
            return "URL is empty."
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return "Only http and https URLs are allowed."
        if not parsed.netloc:
            return "URL must include a network location."
        return None

    def _executables_match(self, allowed_executable: str, actual_executable: str) -> bool:
        allowed_token = allowed_executable.strip().strip('"')
        actual_token = actual_executable.strip().strip('"')
        if not allowed_token or not actual_token:
            return False

        if self._token_has_path(allowed_token):
            return self._normalize_path_token(allowed_token) == self._normalize_path_token(
                actual_token
            )
        return Path(actual_token).name.lower() == Path(allowed_token).name.lower()

    def _normalize_argument_token(self, token: str) -> str:
        return token.strip().strip('"').lower()

    def _normalize_path_token(self, token: str) -> str:
        expanded = os.path.expandvars(token.strip().strip('"'))
        return str(Path(expanded).expanduser()).lower()

    def _token_has_path(self, token: str) -> bool:
        stripped = token.strip().strip('"')
        return bool(Path(stripped).anchor) or "\\" in stripped or "/" in stripped

    def _is_high_risk_launcher(self, executable: str) -> bool:
        executable_name = Path(executable).name.lower()
        if executable_name in {
            "cmd",
            "cmd.exe",
            "powershell",
            "powershell.exe",
            "pwsh",
            "pwsh.exe",
            "wscript",
            "wscript.exe",
            "cscript",
            "cscript.exe",
            "mshta",
            "mshta.exe",
            "python",
            "python.exe",
        }:
            return True
        return Path(executable).suffix.lower() in {
            ".bat",
            ".cmd",
            ".ps1",
            ".vbs",
            ".js",
            ".wsf",
        }

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
                time.sleep(0.1)
                return {
                    "focused": title.lower() in self._get_active_window_title().lower(),
                    "target": title,
                }
        except Exception as exc:  # pragma: no cover - depends on COM state
            return {"focused": False, "error": str(exc)}
        return {"focused": False}

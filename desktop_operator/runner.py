from __future__ import annotations

import json
from itertools import count
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI

from .config import AgentConfig
from .prompts import SYSTEM_PROMPT
from .runtime import AgentRuntime

RunEventHandler = Callable[[dict[str, Any]], None]
TASK_COMPLETE_PREFIX = "TASK_COMPLETE:"
TASK_BLOCKED_PREFIX = "TASK_BLOCKED:"


@dataclass
class AgentRunResult:
    status: str
    run_dir: Path
    final_text: str = ""
    error: str = ""
    steps_completed: int = 0


class AgentRunner:
    def __init__(
        self,
        task: str,
        env_file: Path,
        max_steps: int | None = None,
        dry_run: bool = False,
        allow_shell_launch: bool | None = None,
        prefer_existing_browser_window: bool | None = None,
        event_handler: RunEventHandler | None = None,
    ) -> None:
        self.task = task
        self.env_file = env_file
        self.max_steps_override = max_steps
        self.dry_run_override = dry_run
        self.allow_shell_launch_override = allow_shell_launch
        self.prefer_existing_browser_window_override = prefer_existing_browser_window
        self.event_handler = event_handler
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True
        self._emit({"type": "stop_requested"})

    def run(self) -> AgentRunResult:
        runtime: AgentRuntime | None = None
        run_dir = Path.cwd()

        try:
            config = AgentConfig.from_env(self.env_file if self.env_file.exists() else None)
            config.require_api_settings()

            if self.max_steps_override is not None:
                config.max_steps = max(0, int(self.max_steps_override))
            if self.dry_run_override:
                config.dry_run = True
            if self.allow_shell_launch_override is not None:
                config.allow_shell_launch = self.allow_shell_launch_override
            if self.prefer_existing_browser_window_override is not None:
                config.prefer_existing_browser_window = (
                    self.prefer_existing_browser_window_override
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = config.runs_dir / f"run_{timestamp}"
            runtime = AgentRuntime(config=config, run_dir=run_dir)

            client_kwargs: dict[str, Any] = {"api_key": config.api_key}
            if config.base_url:
                client_kwargs["base_url"] = config.base_url

            with httpx.Client(
                trust_env=config.openai_trust_env,
                timeout=60.0,
            ) as http_client:
                client_kwargs["http_client"] = http_client
                client = OpenAI(**client_kwargs)

                history: list[dict[str, Any]] = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": self.task},
                ]

                self._emit(
                    {
                        "type": "run_started",
                        "run_dir": str(run_dir),
                        "model": config.model,
                        "dry_run": config.dry_run,
                        "allow_shell_launch": config.allow_shell_launch,
                        "prefer_existing_browser_window": config.prefer_existing_browser_window,
                        "max_steps": config.max_steps,
                        "base_url": config.base_url or "",
                        "openai_trust_env": config.openai_trust_env,
                        "task": self.task,
                    }
                )

                step_iterator = count(1)
                for step in step_iterator:
                    if config.max_steps > 0 and step > config.max_steps:
                        break
                    if self.stop_requested:
                        return self._finish(
                            status="stopped",
                            run_dir=run_dir,
                            runtime=runtime,
                            steps_completed=step - 1,
                        )

                    observation = runtime.capture_observation(step=step)
                    visual_message = runtime.build_visual_message(step=step, observation=observation)
                    request_messages = history + [visual_message]

                    self._emit(
                        {
                            "type": "step_started",
                            "step": step,
                            "run_dir": str(run_dir),
                            "screenshot_path": str(observation.screenshot_path),
                            "active_window": observation.active_window or "<none>",
                            "cursor_x": observation.cursor_x,
                            "cursor_y": observation.cursor_y,
                            "visible_windows": observation.visible_windows,
                            "recent_actions": observation.recent_actions,
                        }
                    )

                    response = client.chat.completions.create(
                        model=config.model,
                        messages=request_messages,
                        tools=runtime.tool_definitions(),
                        tool_choice="auto",
                    )

                    message = response.choices[0].message
                    assistant_text = _message_to_text(message.content)
                    if assistant_text:
                        self._emit(
                            {
                                "type": "assistant_message",
                                "step": step,
                                "content": assistant_text,
                            }
                        )

                    if not message.tool_calls:
                        final_text = assistant_text.strip()
                        final_state = _parse_final_state(final_text)
                        if final_state is not None:
                            return self._finish(
                                status=final_state["status"],
                                run_dir=run_dir,
                                runtime=runtime,
                                final_text=final_state["text"],
                                steps_completed=step,
                            )

                        history.append(
                            {
                                "role": "assistant",
                                "content": assistant_text,
                            }
                        )
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "You stopped without a valid final marker. "
                                    "If the task is fully complete, reply with "
                                    f"{TASK_COMPLETE_PREFIX} followed by a Chinese summary. "
                                    "If you are blocked, reply with "
                                    f"{TASK_BLOCKED_PREFIX} followed by the blocker and remaining work. "
                                    "Otherwise continue using tools."
                                ),
                            }
                        )
                        self._emit(
                            {
                                "type": "runner_warning",
                                "step": step,
                                "warning": "assistant_stopped_without_valid_final_marker",
                            }
                        )
                        continue

                    history.append(
                        {
                            "role": "assistant",
                            "content": assistant_text,
                            "tool_calls": [
                                {
                                    "id": call.id,
                                    "type": call.type,
                                    "function": {
                                        "name": call.function.name,
                                        "arguments": call.function.arguments,
                                    },
                                }
                                for call in message.tool_calls
                            ],
                        }
                    )

                    for call in message.tool_calls:
                        if self.stop_requested:
                            return self._finish(
                                status="stopped",
                                run_dir=run_dir,
                                runtime=runtime,
                                steps_completed=step,
                            )

                        tool_result = runtime.execute_tool_call(
                            call.function.name,
                            call.function.arguments,
                        )
                        self._emit(
                            {
                                "type": "tool_result",
                                "step": step,
                                "tool_name": call.function.name,
                                "arguments": call.function.arguments,
                                "result": tool_result,
                            }
                        )
                        history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(tool_result, ensure_ascii=False),
                            }
                        )

                return self._finish(
                    status="max_steps",
                    run_dir=run_dir,
                    runtime=runtime,
                    steps_completed=config.max_steps,
                )
        except Exception as exc:
            error_message = str(exc)
            self._emit(
                {
                    "type": "run_finished",
                    "status": "error",
                    "run_dir": str(run_dir),
                    "steps_completed": 0,
                    "final_text": "",
                    "error": error_message,
                    "action_log": runtime.dump_action_log() if runtime is not None else "",
                }
            )
            return AgentRunResult(
                status="error",
                run_dir=run_dir,
                error=error_message,
                steps_completed=0,
            )

    def _finish(
        self,
        status: str,
        run_dir: Path,
        runtime: AgentRuntime,
        final_text: str = "",
        error: str = "",
        steps_completed: int = 0,
    ) -> AgentRunResult:
        payload = {
            "type": "run_finished",
            "status": status,
            "run_dir": str(run_dir),
            "steps_completed": steps_completed,
            "final_text": final_text,
            "error": error,
            "action_log": runtime.dump_action_log(),
        }
        self._emit(payload)
        return AgentRunResult(
            status=status,
            run_dir=run_dir,
            final_text=final_text,
            error=error,
            steps_completed=steps_completed,
        )

    def _emit(self, event: dict[str, Any]) -> None:
        if self.event_handler is not None:
            self.event_handler(event)


def _message_to_text(content: str | list[Any] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    fragments: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text":
                fragments.append(str(item.get("text", "")))
            continue
        text_value = getattr(item, "text", None)
        if text_value:
            fragments.append(str(text_value))
    return "\n".join(fragment for fragment in fragments if fragment).strip()


def _parse_final_state(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith(TASK_COMPLETE_PREFIX):
        summary = stripped[len(TASK_COMPLETE_PREFIX) :].strip() or "任务已完成。"
        return {"status": "completed", "text": summary}
    if stripped.startswith(TASK_BLOCKED_PREFIX):
        summary = stripped[len(TASK_BLOCKED_PREFIX) :].strip() or "任务被阻塞。"
        return {"status": "blocked", "text": summary}
    return None

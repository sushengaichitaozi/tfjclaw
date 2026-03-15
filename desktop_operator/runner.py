from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import BadRequestError, OpenAI

from .config import AgentConfig
from .prompts import SYSTEM_PROMPT
from .runtime import AgentRuntime

RunEventHandler = Callable[[dict[str, Any]], None]
TASK_COMPLETE_PREFIX = "TASK_COMPLETE:"
TASK_BLOCKED_PREFIX = "TASK_BLOCKED:"
READ_ONLY_TOOL_NAMES = {
    "list_windows",
    "browser_status",
    "browser_snapshot",
    "browser_list_tabs",
    "ocr_extract_text",
    "ocr_find_text",
    "uia_list_windows",
    "uia_describe_window",
}


@dataclass
class AgentRunResult:
    status: str
    run_dir: Path
    final_text: str = ""
    error: str = ""
    steps_completed: int = 0


@dataclass
class _CompatFunctionCall:
    name: str
    arguments: str


@dataclass
class _CompatToolCall:
    id: str
    type: str
    function: _CompatFunctionCall


@dataclass
class _CompatMessage:
    content: str
    tool_calls: list[_CompatToolCall]


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
        steps_completed = 0
        runtime_closed = False

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
                stream_required = False

                history: list[dict[str, Any]] = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": self.task},
                ]
                recent_state_action_signatures: list[tuple[str, str, str]] = []

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

                for step in count(1):
                    if config.max_steps > 0 and step > config.max_steps:
                        break

                    if self.stop_requested:
                        close_result = self._close_runtime(runtime)
                        runtime_closed = True
                        return self._finish(
                            status="stopped",
                            run_dir=run_dir,
                            runtime=runtime,
                            steps_completed=steps_completed,
                            close_result=close_result,
                        )

                    observation = runtime.capture_observation(step=step)
                    visual_message = runtime.build_visual_message(
                        step=step,
                        observation=observation,
                    )
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

                    streamed_assistant = False
                    streamed_parts: list[str] = []

                    def on_text_delta(delta: str) -> None:
                        nonlocal streamed_assistant
                        if not delta:
                            return
                        streamed_assistant = True
                        streamed_parts.append(delta)
                        self._emit(
                            {
                                "type": "assistant_message_delta",
                                "step": step,
                                "content": "".join(streamed_parts),
                            }
                        )

                    message, stream_required = _create_chat_message(
                        client=client,
                        model=config.model,
                        messages=request_messages,
                        tools=runtime.tool_definitions(),
                        tool_choice="auto",
                        stream_required=stream_required,
                        on_text_delta=on_text_delta,
                    )

                    assistant_text = _message_to_text(message.content)
                    if assistant_text:
                        self._emit(
                            {
                                "type": "assistant_message",
                                "step": step,
                                "content": assistant_text,
                                "streamed": streamed_assistant,
                            }
                        )

                    if not message.tool_calls:
                        final_text = assistant_text.strip()
                        final_state = _parse_final_state(final_text)
                        if final_state is not None:
                            steps_completed = step
                            close_result = self._close_runtime(runtime)
                            runtime_closed = True
                            return self._finish(
                                status=final_state["status"],
                                run_dir=run_dir,
                                runtime=runtime,
                                final_text=final_state["text"],
                                steps_completed=steps_completed,
                                close_result=close_result,
                            )

                        history.append({"role": "assistant", "content": assistant_text})
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
                        _trim_history(history, config.max_history_messages)
                        continue

                    critic_reason = _critic_block_reason(message.tool_calls)
                    if critic_reason is not None:
                        if assistant_text:
                            history.append({"role": "assistant", "content": assistant_text})
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "Critic rejected your last tool plan for this screenshot. "
                                    f"Reason: {critic_reason} "
                                    f"Proposed tools: {_summarize_tool_calls(message.tool_calls)}. "
                                    "Replan from the same task. You may batch read-only inspection tools, "
                                    "but any state-changing tool must be the only state-changing action "
                                    "in the turn and it must be the final tool call."
                                ),
                            }
                        )
                        self._emit(
                            {
                                "type": "runner_warning",
                                "step": step,
                                "warning": f"critic_blocked_plan: {critic_reason}",
                            }
                        )
                        _trim_history(history, config.max_history_messages)
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

                    executed_state_actions: list[tuple[str, str]] = []
                    for call in message.tool_calls:
                        if self.stop_requested:
                            close_result = self._close_runtime(runtime)
                            runtime_closed = True
                            return self._finish(
                                status="stopped",
                                run_dir=run_dir,
                                runtime=runtime,
                                steps_completed=steps_completed,
                                close_result=close_result,
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
                        if _is_state_changing_tool(call.function.name):
                            executed_state_actions.append(
                                (call.function.name, (call.function.arguments or "").strip())
                            )

                    steps_completed = step
                    if len(executed_state_actions) == 1:
                        recent_state_action_signatures.append(
                            (
                                executed_state_actions[0][0],
                                executed_state_actions[0][1],
                                str(observation.active_window or ""),
                            )
                        )
                        if len(recent_state_action_signatures) > 5:
                            recent_state_action_signatures = recent_state_action_signatures[-5:]
                        repeat_reason = _repeated_action_loop_reason(
                            recent_state_action_signatures
                        )
                        if repeat_reason is not None:
                            history.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Loop guard warning. "
                                        f"{repeat_reason} "
                                        "Do not repeat the same state-changing action again immediately. "
                                        "Use read-only tools to inspect what changed, try a different action, "
                                        f"or reply with {TASK_BLOCKED_PREFIX} if the UI is preventing progress."
                                    ),
                                }
                            )
                            self._emit(
                                {
                                    "type": "runner_warning",
                                    "step": step,
                                    "warning": f"loop_guard: {repeat_reason}",
                                }
                            )
                    else:
                        recent_state_action_signatures.clear()
                    _trim_history(history, config.max_history_messages)

                close_result = self._close_runtime(runtime)
                runtime_closed = True
                return self._finish(
                    status="max_steps",
                    run_dir=run_dir,
                    runtime=runtime,
                    steps_completed=steps_completed,
                    close_result=close_result,
                )
        except Exception as exc:
            error_message = str(exc)
            close_result = self._close_runtime(runtime)
            runtime_closed = True
            if close_result.get("browser_close_error"):
                self._emit(
                    {
                        "type": "runner_warning",
                        "warning": (
                            "runtime_close_failed: "
                            f"{close_result['browser_close_error']}"
                        ),
                    }
                )
            self._emit(
                {
                    "type": "run_finished",
                    "status": "error",
                    "run_dir": str(run_dir),
                    "steps_completed": steps_completed,
                    "final_text": "",
                    "error": error_message,
                    "action_log": runtime.dump_action_log() if runtime is not None else "",
                }
            )
            return AgentRunResult(
                status="error",
                run_dir=run_dir,
                error=error_message,
                steps_completed=steps_completed,
            )
        finally:
            if runtime is not None and not runtime_closed:
                self._close_runtime(runtime)

    def _finish(
        self,
        status: str,
        run_dir: Path,
        runtime: AgentRuntime,
        final_text: str = "",
        error: str = "",
        steps_completed: int = 0,
        close_result: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        close_result = close_result or {}
        if close_result.get("browser_close_error"):
            self._emit(
                {
                    "type": "runner_warning",
                    "warning": (
                        "runtime_close_failed: "
                        f"{close_result['browser_close_error']}"
                    ),
                }
            )
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

    def _close_runtime(self, runtime: AgentRuntime | None) -> dict[str, Any]:
        if runtime is None:
            return {}
        return runtime.close()


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


def _create_chat_message(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str,
    stream_required: bool,
    on_text_delta: Callable[[str], None] | None = None,
) -> tuple[Any, bool]:
    request_kwargs = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
    }

    if stream_required:
        return _create_chat_message_from_stream(
            client,
            **request_kwargs,
            on_text_delta=on_text_delta,
        ), True

    try:
        response = client.chat.completions.create(**request_kwargs)
        return response.choices[0].message, False
    except BadRequestError as exc:
        if not _error_requires_stream(exc):
            raise
        return _create_chat_message_from_stream(
            client,
            **request_kwargs,
            on_text_delta=on_text_delta,
        ), True


def _create_chat_message_from_stream(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str,
    on_text_delta: Callable[[str], None] | None = None,
) -> _CompatMessage:
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        stream=True,
    )

    for chunk in stream:
        for choice in getattr(chunk, "choices", []) or []:
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            delta_content = getattr(delta, "content", None)
            if delta_content:
                text_delta = str(delta_content)
                content_parts.append(text_delta)
                if on_text_delta is not None:
                    on_text_delta(text_delta)

            for tool_delta in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_delta, "index", 0) or 0)
                entry = tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {
                            "name": "",
                            "arguments": "",
                        },
                    },
                )

                tool_id = getattr(tool_delta, "id", None)
                if tool_id:
                    entry["id"] = str(tool_id)

                tool_type = getattr(tool_delta, "type", None)
                if tool_type:
                    entry["type"] = str(tool_type)

                function_delta = getattr(tool_delta, "function", None)
                if function_delta is None:
                    continue

                function_name = getattr(function_delta, "name", None)
                if function_name:
                    entry["function"]["name"] += str(function_name)

                function_arguments = getattr(function_delta, "arguments", None)
                if function_arguments:
                    entry["function"]["arguments"] += str(function_arguments)

    compact_tool_calls = [
        _CompatToolCall(
            id=payload["id"] or f"tool_call_{index}",
            type=payload["type"] or "function",
            function=_CompatFunctionCall(
                name=payload["function"]["name"],
                arguments=payload["function"]["arguments"],
            ),
        )
        for index, payload in sorted(tool_calls.items())
    ]
    return _CompatMessage(
        content="".join(content_parts).strip(),
        tool_calls=compact_tool_calls,
    )


def _error_requires_stream(exc: BadRequestError) -> bool:
    candidates: list[str] = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        detail = body.get("detail")
        if detail is not None:
            candidates.append(str(detail))
        error = body.get("error")
        if error is not None:
            candidates.append(str(error))
    lowered = " ".join(candidates).lower()
    return "stream must be set to true" in lowered


def _trim_history(history: list[dict[str, Any]], max_history_messages: int) -> None:
    if max_history_messages <= 0 or len(history) <= max_history_messages:
        return

    anchor_count = min(2, len(history))
    head = history[:anchor_count]
    tail = history[anchor_count:]

    while len(head) + len(tail) > max_history_messages and tail:
        first = tail[0]
        remove_count = 1

        if first.get("role") == "assistant" and first.get("tool_calls"):
            remove_count = 1
            while remove_count < len(tail) and tail[remove_count].get("role") == "tool":
                remove_count += 1
        elif (
            first.get("role") == "assistant"
            and remove_count < len(tail)
            and len(tail) > 1
            and tail[1].get("role") == "user"
        ):
            remove_count = 2

        tail = tail[remove_count:]

    history[:] = head + tail


def _critic_block_reason(tool_calls: list[Any]) -> str | None:
    if len(tool_calls) <= 1:
        return None

    state_changing_indexes = [
        index
        for index, call in enumerate(tool_calls)
        if _is_state_changing_tool(call.function.name)
    ]
    if not state_changing_indexes:
        return None

    if len(state_changing_indexes) > 1:
        names = ", ".join(tool_calls[index].function.name for index in state_changing_indexes[:4])
        return f"Multiple state-changing tool calls were proposed from one screenshot: {names}."

    first_state_change = state_changing_indexes[0]
    if first_state_change != len(tool_calls) - 1:
        trailing = ", ".join(
            call.function.name for call in tool_calls[first_state_change + 1 :]
        )
        return (
            f"The state-changing tool {tool_calls[first_state_change].function.name} is "
            f"followed by additional tool calls ({trailing}), which would run on a stale UI."
        )
    return None


def _is_state_changing_tool(tool_name: str) -> bool:
    return tool_name not in READ_ONLY_TOOL_NAMES


def _summarize_tool_calls(tool_calls: list[Any]) -> str:
    parts: list[str] = []
    for call in tool_calls:
        name = getattr(call.function, "name", "")
        arguments = getattr(call.function, "arguments", "") or "{}"
        parts.append(f"{name}({arguments})")
    return ", ".join(parts) or "<none>"


def _repeated_action_loop_reason(
    recent_state_action_signatures: list[tuple[str, str, str]]
) -> str | None:
    if len(recent_state_action_signatures) < 3:
        return None
    tail = recent_state_action_signatures[-3:]
    if tail[0] == tail[1] == tail[2]:
        tool_name, arguments, active_window = tail[-1]
        window_label = active_window or "<none>"
        return (
            f"The same state-changing action {tool_name}({arguments or '{}'}) "
            f"was repeated in the same active window ({window_label}) for three consecutive steps."
        )
    return None


def _parse_final_state(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith(TASK_COMPLETE_PREFIX):
        summary = stripped[len(TASK_COMPLETE_PREFIX) :].strip() or "任务已完成。"
        return {"status": "completed", "text": summary}
    if stripped.startswith(TASK_BLOCKED_PREFIX):
        summary = stripped[len(TASK_BLOCKED_PREFIX) :].strip() or "任务已阻塞。"
        return {"status": "blocked", "text": summary}
    return None

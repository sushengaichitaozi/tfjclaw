from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "desktop_operator" / "runner.py"
MODULE_NAMES = (
    "desktop_operator",
    "desktop_operator.config",
    "desktop_operator.prompts",
    "desktop_operator.runtime",
    "desktop_operator.runner",
    "httpx",
    "openai",
)


def _install_runner_stubs() -> dict[str, types.ModuleType | None]:
    saved = {name: sys.modules.get(name) for name in MODULE_NAMES}

    package = types.ModuleType("desktop_operator")
    package.__path__ = [str(ROOT / "desktop_operator")]
    sys.modules["desktop_operator"] = package

    config_module = types.ModuleType("desktop_operator.config")
    config_module.AgentConfig = object
    sys.modules["desktop_operator.config"] = config_module

    prompts_module = types.ModuleType("desktop_operator.prompts")
    prompts_module.SYSTEM_PROMPT = "system"
    sys.modules["desktop_operator.prompts"] = prompts_module

    runtime_module = types.ModuleType("desktop_operator.runtime")
    runtime_module.AgentRuntime = object
    sys.modules["desktop_operator.runtime"] = runtime_module

    httpx_module = types.ModuleType("httpx")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    httpx_module.Client = Client
    sys.modules["httpx"] = httpx_module

    openai_module = types.ModuleType("openai")

    class BadRequestError(Exception):
        pass

    class OpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    openai_module.BadRequestError = BadRequestError
    openai_module.OpenAI = OpenAI
    sys.modules["openai"] = openai_module

    return saved


def _restore_modules(saved: dict[str, types.ModuleType | None]) -> None:
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_runner_module():
    saved = _install_runner_stubs()
    spec = importlib.util.spec_from_file_location("desktop_operator.runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, saved


class _ConfigInstance:
    def __init__(self, runs_dir: Path, max_steps: int = 5) -> None:
        self.api_key = "test-key"
        self.base_url = None
        self.model = "test-model"
        self.openai_trust_env = False
        self.max_steps = max_steps
        self.dry_run = False
        self.allow_shell_launch = False
        self.prefer_existing_browser_window = True
        self.runs_dir = runs_dir
        self.max_history_messages = 50

    def require_api_settings(self) -> None:
        return None


class _FakeAgentConfig:
    next_instance: _ConfigInstance | None = None

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> _ConfigInstance:
        assert cls.next_instance is not None
        return cls.next_instance


class _FakeRuntime:
    instances: list["_FakeRuntime"] = []
    close_result: dict[str, object] = {}

    def __init__(self, config, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.execute_calls: list[tuple[str, str]] = []
        _FakeRuntime.instances.append(self)

    def capture_observation(self, step: int):
        return types.SimpleNamespace(
            screenshot_path=self.run_dir / f"step_{step:02d}.png",
            active_window="Test Window",
            cursor_x=100,
            cursor_y=200,
            visible_windows=["Test Window"],
            recent_actions=[],
        )

    def build_visual_message(self, step: int, observation) -> dict[str, object]:
        return {"role": "user", "content": f"visual-{step}"}

    def tool_definitions(self) -> list[dict[str, object]]:
        return []

    def execute_tool_call(self, tool_name: str, raw_arguments: str) -> dict[str, object]:
        self.execute_calls.append((tool_name, raw_arguments))
        return {"ok": True, "tool_name": tool_name}

    def dump_action_log(self) -> str:
        return json.dumps(self.execute_calls)

    def close(self) -> dict[str, object]:
        return dict(self.close_result)


class RunnerHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module, cls._saved_modules = _load_runner_module()

    @classmethod
    def tearDownClass(cls) -> None:
        _restore_modules(cls._saved_modules)

    def setUp(self) -> None:
        self.module.AgentConfig = _FakeAgentConfig
        self.module.AgentRuntime = _FakeRuntime
        self.original_create_chat_message = self.module._create_chat_message
        _FakeRuntime.instances = []
        _FakeRuntime.close_result = {}

    def tearDown(self) -> None:
        self.module._create_chat_message = self.original_create_chat_message

    def _make_config(self) -> _ConfigInstance:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return _ConfigInstance(runs_dir=Path(temp_dir.name))

    def _message(self, content: str, tool_calls: list[tuple[str, str]] | None = None):
        tool_calls = tool_calls or []
        return self.module._CompatMessage(
            content=content,
            tool_calls=[
                self.module._CompatToolCall(
                    id=f"call-{index}",
                    type="function",
                    function=self.module._CompatFunctionCall(
                        name=tool_name,
                        arguments=arguments,
                    ),
                )
                for index, (tool_name, arguments) in enumerate(tool_calls, start=1)
            ],
        )

    def test_trim_history_keeps_initial_messages_and_removes_old_turns(self) -> None:
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tool-1"}],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": "{}"},
            {"role": "assistant", "content": "follow-up"},
            {"role": "user", "content": "continue"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tool-2"}],
            },
            {"role": "tool", "tool_call_id": "tool-2", "content": "{}"},
        ]

        self.module._trim_history(history, 5)

        self.assertEqual("system", history[0]["content"])
        self.assertEqual("task", history[1]["content"])
        self.assertLessEqual(len(history), 5)
        self.assertNotEqual("tool", history[2]["role"])

    def test_trim_history_is_disabled_with_zero_limit(self) -> None:
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
        ]

        self.module._trim_history(history, 0)

        self.assertEqual(4, len(history))

    def test_critic_blocks_multi_action_tool_plan(self) -> None:
        _FakeAgentConfig.next_instance = self._make_config()
        responses = iter(
            [
                self._message(
                    "planning",
                    tool_calls=[
                        ("click", '{"x": 10, "y": 20}'),
                        ("type_text", '{"text": "hello"}'),
                    ],
                ),
                self._message("TASK_COMPLETE: 完成"),
            ]
        )

        def fake_create_chat_message(**kwargs):
            return next(responses), False

        self.module._create_chat_message = fake_create_chat_message
        events: list[dict[str, object]] = []
        runner = self.module.AgentRunner(
            task="test-task",
            env_file=Path("dummy.env"),
            event_handler=events.append,
        )

        result = runner.run()

        self.assertEqual("completed", result.status)
        self.assertEqual([], _FakeRuntime.instances[0].execute_calls)
        self.assertTrue(
            any(
                event.get("type") == "runner_warning"
                and "critic_blocked_plan" in str(event.get("warning", ""))
                for event in events
            )
        )

    def test_streamed_assistant_updates_emit_delta_events(self) -> None:
        _FakeAgentConfig.next_instance = self._make_config()
        responses = iter(
            [
                self._message("Inspecting the window", tool_calls=[("list_windows", "{}")]),
                self._message("TASK_COMPLETE: done"),
            ]
        )
        emitted_stream_update = False

        def fake_create_chat_message(**kwargs):
            nonlocal emitted_stream_update
            on_text_delta = kwargs.get("on_text_delta")
            if on_text_delta is not None and not emitted_stream_update:
                emitted_stream_update = True
                on_text_delta("Inspecting")
                on_text_delta(" the window")
            return next(responses), False

        self.module._create_chat_message = fake_create_chat_message
        events: list[dict[str, object]] = []
        runner = self.module.AgentRunner(
            task="test-task",
            env_file=Path("dummy.env"),
            event_handler=events.append,
        )

        result = runner.run()

        self.assertEqual("completed", result.status)
        delta_events = [
            event for event in events if event.get("type") == "assistant_message_delta"
        ]
        self.assertEqual(
            ["Inspecting", "Inspecting the window"],
            [str(event.get("content", "")) for event in delta_events],
        )
        streamed_messages = [
            event for event in events if event.get("type") == "assistant_message"
        ]
        self.assertTrue(any(event.get("streamed") for event in streamed_messages))

    def test_run_finished_is_last_event_even_when_runtime_close_warns(self) -> None:
        _FakeAgentConfig.next_instance = self._make_config()
        _FakeRuntime.close_result = {"browser_close_error": "close failed"}
        responses = iter([self._message("TASK_COMPLETE: done")])

        def fake_create_chat_message(**kwargs):
            return next(responses), False

        self.module._create_chat_message = fake_create_chat_message
        events: list[dict[str, object]] = []
        runner = self.module.AgentRunner(
            task="test-task",
            env_file=Path("dummy.env"),
            event_handler=events.append,
        )

        result = runner.run()

        self.assertEqual("completed", result.status)
        run_finished_indexes = [
            index for index, event in enumerate(events) if event.get("type") == "run_finished"
        ]
        self.assertEqual(1, len(run_finished_indexes))
        self.assertEqual(len(events) - 1, run_finished_indexes[0])
        self.assertTrue(
            any(event.get("type") == "runner_warning" for event in events[: run_finished_indexes[0]])
        )

    def test_loop_guard_warns_after_three_identical_state_actions(self) -> None:
        _FakeAgentConfig.next_instance = self._make_config()
        responses = iter(
            [
                self._message("step-1", tool_calls=[("click", '{"x": 10, "y": 20}')]),
                self._message("step-2", tool_calls=[("click", '{"x": 10, "y": 20}')]),
                self._message("step-3", tool_calls=[("click", '{"x": 10, "y": 20}')]),
                self._message("TASK_COMPLETE: done"),
            ]
        )

        def fake_create_chat_message(**kwargs):
            return next(responses), False

        self.module._create_chat_message = fake_create_chat_message
        events: list[dict[str, object]] = []
        runner = self.module.AgentRunner(
            task="test-task",
            env_file=Path("dummy.env"),
            event_handler=events.append,
        )

        result = runner.run()

        self.assertEqual("completed", result.status)
        self.assertEqual(
            [
                ("click", '{"x": 10, "y": 20}'),
                ("click", '{"x": 10, "y": 20}'),
                ("click", '{"x": 10, "y": 20}'),
            ],
            _FakeRuntime.instances[0].execute_calls,
        )
        self.assertTrue(
            any(
                event.get("type") == "runner_warning"
                and "loop_guard:" in str(event.get("warning", ""))
                for event in events
            )
        )


if __name__ == "__main__":
    unittest.main()

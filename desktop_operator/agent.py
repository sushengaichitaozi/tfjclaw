from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import AgentConfig
from .runner import AgentRunner
from .runtime import AgentRuntime


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "doctor":
        run_doctor(args.env_file)
        return

    if args.command == "run":
        run_agent(
            task=args.task,
            env_file=args.env_file,
            max_steps=args.max_steps,
            dry_run=args.dry_run,
        )
        return

    parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local Windows desktop agent against an OpenAI-compatible API."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the .env file containing API settings.",
    )

    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check local desktop automation prerequisites."
    )
    doctor_parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the .env file containing API settings.",
    )

    run_parser = subparsers.add_parser(
        "run", help="Run the desktop agent on a single task."
    )
    run_parser.add_argument("task", help="The task for the agent to complete.")
    run_parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override the maximum number of agent steps.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without moving the mouse or typing.",
    )
    run_parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the .env file containing API settings.",
    )

    return parser


def run_doctor(env_file: Path) -> None:
    config = AgentConfig.from_env(env_file if env_file.exists() else None)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = config.runs_dir / f"doctor_{timestamp}"
    runtime = AgentRuntime(config=config, run_dir=run_dir)
    observation = runtime.capture_observation(step=0)
    report = runtime.doctor_report()
    status = report["desktop"]

    print("Desktop agent doctor")
    print(f"Python executable: {sys.executable}")
    print(f"Env file: {env_file.resolve()}")
    print(f"Model configured: {bool(config.model)}")
    print(f"API key configured: {bool(config.api_key)}")
    print(f"Base URL: {config.base_url or '<OpenAI default>'}")
    print(f"Dry run: {config.dry_run}")
    print(f"Allow shell launch: {config.allow_shell_launch}")
    print(f"Screen: {status['screen_width']} x {status['screen_height']}")
    print(f"Cursor: ({status['cursor_x']}, {status['cursor_y']})")
    print(_safe_console_text(f"Active window: {status['active_window'] or '<none>'}"))
    print(f"Visible windows: {len(status['visible_windows'])}")
    print(f"Browser available: {report['browser'].get('available', False)}")
    print(f"Browser executable: {report['browser'].get('executable_path', '<unknown>')}")
    print(f"OCR available: {report['ocr'].get('available', False)}")
    if report["ocr"].get("error"):
        print(_safe_console_text(f"OCR error: {report['ocr']['error']}"))
    print(f"UI Automation available: {report['uia'].get('available', False)}")
    if report["uia"].get("sample_windows"):
        print(
            _safe_console_text(
                f"UIA sample windows: {', '.join(report['uia']['sample_windows'])}"
            )
        )
    print(f"Screenshot saved to: {observation.screenshot_path}")


def run_agent(
    task: str,
    env_file: Path,
    max_steps: int | None,
    dry_run: bool,
) -> None:
    reporter = _ConsoleReporter()
    runner = AgentRunner(
        task=task,
        env_file=env_file,
        max_steps=max_steps,
        dry_run=dry_run,
        event_handler=reporter.handle,
    )
    runner.run()


class _ConsoleReporter:
    def handle(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))

        if event_type == "run_started":
            print(f"Run directory: {event.get('run_dir', '')}")
            print(f"Model: {event.get('model', '')}")
            print(f"Dry run: {event.get('dry_run', False)}")
            print("Tip: move the mouse to the top-left corner to trigger PyAutoGUI failsafe.")
            print(
                "Tip: use browser_launch for DOM automation and "
                "browser_connect_cdp to attach to an existing Chrome session."
            )
            return

        if event_type == "step_started":
            print(
                _safe_console_text(
                    f"\n[step {event.get('step', '?')}] "
                    f"active_window={event.get('active_window', '<none>')} "
                    f"cursor=({event.get('cursor_x', '?')}, {event.get('cursor_y', '?')})"
                )
            )
            return

        if event_type == "tool_result":
            print(
                _safe_console_text(
                    f"  tool {event.get('tool_name', '')}: {event.get('result', {})}"
                )
            )
            return

        if event_type != "run_finished":
            return

        status = str(event.get("status", ""))
        final_text = str(event.get("final_text", "") or "").strip()
        error = str(event.get("error", "") or "").strip()
        action_log = str(event.get("action_log", "") or "").strip()

        if final_text:
            print("\nFinal answer:")
            print(_safe_console_text(final_text))
            return

        if error:
            print("\nRun failed:")
            print(_safe_console_text(error))
            return

        if status == "max_steps":
            print("\nStopped because max steps was reached.")
            print("Action log:")
            print(_safe_console_text(action_log))
            return

        if status == "stopped":
            print("\nStopped because a stop request was received.")
            print("Action log:")
            print(_safe_console_text(action_log))
            return

        print("\nThe model returned no tool calls and no final text.")


def _safe_console_text(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


if __name__ == "__main__":
    main()

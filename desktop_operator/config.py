from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class AgentConfig:
    api_key: str
    base_url: str | None
    model: str
    openai_trust_env: bool
    max_steps: int
    action_pause_seconds: float
    dry_run: bool
    allow_shell_launch: bool
    runs_dir: Path
    browser_headless: bool
    browser_engine: str
    browser_channel: str | None
    browser_user_data_dir: Path
    browser_executable_path: str | None
    browser_start_url: str | None
    browser_timeout_ms: int
    max_browser_elements: int
    prefer_existing_browser_window: bool
    tesseract_cmd: str | None
    ocr_lang: str
    include_uia_in_prompt: bool

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "AgentConfig":
        load_dotenv(dotenv_path=env_file)

        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        if base_url:
            base_url = base_url.rstrip("/")

        runs_dir = Path(
            os.getenv("DESKTOP_AGENT_RUNS_DIR", Path.cwd() / "runs")
        ).expanduser()
        browser_user_data_dir = Path(
            os.getenv("DESKTOP_AGENT_BROWSER_USER_DATA_DIR", Path.cwd() / ".browser-state")
        ).expanduser()

        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=base_url,
            model=os.getenv("DESKTOP_AGENT_MODEL", os.getenv("OPENAI_MODEL", "")).strip(),
            openai_trust_env=_env_bool("DESKTOP_AGENT_OPENAI_TRUST_ENV", default=False),
            max_steps=max(0, int(os.getenv("DESKTOP_AGENT_MAX_STEPS", "60"))),
            action_pause_seconds=float(
                os.getenv("DESKTOP_AGENT_ACTION_PAUSE_SECONDS", "0.25")
            ),
            dry_run=_env_bool("DESKTOP_AGENT_DRY_RUN", default=False),
            allow_shell_launch=_env_bool("DESKTOP_AGENT_ALLOW_SHELL", default=False),
            runs_dir=runs_dir,
            browser_headless=_env_bool("DESKTOP_AGENT_BROWSER_HEADLESS", default=False),
            browser_engine=os.getenv("DESKTOP_AGENT_BROWSER_ENGINE", "chromium").strip()
            or "chromium",
            browser_channel=_env_optional_str("DESKTOP_AGENT_BROWSER_CHANNEL"),
            browser_user_data_dir=browser_user_data_dir,
            browser_executable_path=_env_optional_str("DESKTOP_AGENT_BROWSER_EXECUTABLE"),
            browser_start_url=_env_optional_str("DESKTOP_AGENT_BROWSER_START_URL"),
            browser_timeout_ms=int(os.getenv("DESKTOP_AGENT_BROWSER_TIMEOUT_MS", "15000")),
            max_browser_elements=int(os.getenv("DESKTOP_AGENT_MAX_BROWSER_ELEMENTS", "20")),
            prefer_existing_browser_window=_env_bool(
                "DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW", default=True
            ),
            tesseract_cmd=_env_optional_str("DESKTOP_AGENT_TESSERACT_CMD"),
            ocr_lang=os.getenv("DESKTOP_AGENT_OCR_LANG", "eng").strip() or "eng",
            include_uia_in_prompt=_env_bool(
                "DESKTOP_AGENT_INCLUDE_UIA_IN_PROMPT", default=True
            ),
        )

    def require_api_settings(self) -> None:
        missing: list[str] = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.model:
            missing.append("DESKTOP_AGENT_MODEL or OPENAI_MODEL")

        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required settings: {joined}")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None

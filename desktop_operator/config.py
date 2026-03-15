from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


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
    prompt_image_max_side: int
    prompt_image_quality: int
    tesseract_cmd: str | None
    tessdata_prefix: Path | None
    ocr_lang: str
    include_uia_in_prompt: bool
    max_history_messages: int
    max_saved_screenshots: int
    allowed_launch_prefixes: tuple[str, ...]
    auto_run_doctor: bool

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "AgentConfig":
        env_values = _load_env_values(env_file)
        env_base_dir = _env_base_dir(env_file)

        base_url = _env_raw("OPENAI_BASE_URL", env_values) or _env_raw(
            "OPENAI_API_BASE",
            env_values,
        )
        if base_url:
            base_url = base_url.rstrip("/")

        runs_dir = _env_path(
            "DESKTOP_AGENT_RUNS_DIR",
            env_values,
            env_base_dir,
            default=Path.cwd() / "runs",
        )
        browser_user_data_dir = _env_path(
            "DESKTOP_AGENT_BROWSER_USER_DATA_DIR",
            env_values,
            env_base_dir,
            default=Path.cwd() / ".browser-state",
        )

        return cls(
            api_key=_env_str("OPENAI_API_KEY", env_values, "").strip(),
            base_url=base_url,
            model=(
                _env_raw("DESKTOP_AGENT_MODEL", env_values)
                or _env_raw("OPENAI_MODEL", env_values)
                or ""
            ).strip(),
            openai_trust_env=_env_bool(
                "DESKTOP_AGENT_OPENAI_TRUST_ENV",
                env_values,
                default=False,
            ),
            max_steps=max(0, int(_env_str("DESKTOP_AGENT_MAX_STEPS", env_values, "60"))),
            action_pause_seconds=float(
                _env_str("DESKTOP_AGENT_ACTION_PAUSE_SECONDS", env_values, "0.25")
            ),
            dry_run=_env_bool("DESKTOP_AGENT_DRY_RUN", env_values, default=False),
            allow_shell_launch=_env_bool(
                "DESKTOP_AGENT_ALLOW_SHELL",
                env_values,
                default=False,
            ),
            runs_dir=runs_dir,
            browser_headless=_env_bool(
                "DESKTOP_AGENT_BROWSER_HEADLESS",
                env_values,
                default=False,
            ),
            browser_engine=_env_str(
                "DESKTOP_AGENT_BROWSER_ENGINE",
                env_values,
                "chromium",
            ).strip()
            or "chromium",
            browser_channel=_env_optional_str("DESKTOP_AGENT_BROWSER_CHANNEL", env_values),
            browser_user_data_dir=browser_user_data_dir,
            browser_executable_path=_env_optional_path_str(
                "DESKTOP_AGENT_BROWSER_EXECUTABLE",
                env_values,
                env_base_dir,
            ),
            browser_start_url=_env_optional_str(
                "DESKTOP_AGENT_BROWSER_START_URL",
                env_values,
            ),
            browser_timeout_ms=int(
                _env_str("DESKTOP_AGENT_BROWSER_TIMEOUT_MS", env_values, "15000")
            ),
            max_browser_elements=int(
                _env_str("DESKTOP_AGENT_MAX_BROWSER_ELEMENTS", env_values, "20")
            ),
            prefer_existing_browser_window=_env_bool(
                "DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW",
                env_values,
                default=True,
            ),
            prompt_image_max_side=max(
                640,
                int(_env_str("DESKTOP_AGENT_PROMPT_IMAGE_MAX_SIDE", env_values, "1440")),
            ),
            prompt_image_quality=min(
                95,
                max(
                    35,
                    int(_env_str("DESKTOP_AGENT_PROMPT_IMAGE_QUALITY", env_values, "70")),
                ),
            ),
            tesseract_cmd=_env_optional_path_str(
                "DESKTOP_AGENT_TESSERACT_CMD",
                env_values,
                env_base_dir,
            ),
            tessdata_prefix=_env_optional_path(
                "TESSDATA_PREFIX",
                env_values,
                env_base_dir,
            ),
            ocr_lang=_env_str("DESKTOP_AGENT_OCR_LANG", env_values, "eng").strip() or "eng",
            include_uia_in_prompt=_env_bool(
                "DESKTOP_AGENT_INCLUDE_UIA_IN_PROMPT",
                env_values,
                default=True,
            ),
            max_history_messages=max(
                0,
                int(_env_str("DESKTOP_AGENT_MAX_HISTORY_MESSAGES", env_values, "60")),
            ),
            max_saved_screenshots=max(
                0,
                int(
                    _env_str(
                        "DESKTOP_AGENT_MAX_SAVED_SCREENSHOTS",
                        env_values,
                        "200",
                    )
                ),
            ),
            allowed_launch_prefixes=_env_csv(
                "DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES",
                env_values,
            ),
            auto_run_doctor=_env_bool(
                "DESKTOP_AGENT_AUTO_RUN_DOCTOR",
                env_values,
                default=False,
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


def _load_env_values(env_file: Path | None) -> dict[str, str]:
    if env_file is None:
        return {}
    return {
        key: value
        for key, value in dotenv_values(env_file).items()
        if isinstance(value, str)
    }


def _env_base_dir(env_file: Path | None) -> Path:
    if env_file is None:
        return Path.cwd()
    return env_file.expanduser().resolve().parent


def _env_raw(name: str, env_values: dict[str, str]) -> str | None:
    raw = os.getenv(name)
    if raw is not None:
        return raw
    return env_values.get(name)


def _env_str(name: str, env_values: dict[str, str], default: str) -> str:
    raw = _env_raw(name, env_values)
    if raw is None:
        return default
    return raw


def _env_bool(name: str, env_values: dict[str, str], default: bool) -> bool:
    raw = _env_raw(name, env_values)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_str(name: str, env_values: dict[str, str]) -> str | None:
    raw = _env_raw(name, env_values)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _resolve_path(value: str, base_dir: Path) -> Path:
    expanded = Path(os.path.expandvars(value)).expanduser()
    if expanded.is_absolute():
        return expanded
    return (base_dir / expanded).resolve()


def _env_path(
    name: str,
    env_values: dict[str, str],
    base_dir: Path,
    default: Path,
) -> Path:
    raw = _env_raw(name, env_values)
    if raw is None or not raw.strip():
        return default.expanduser().resolve()
    return _resolve_path(raw.strip(), base_dir)


def _env_optional_path(name: str, env_values: dict[str, str], base_dir: Path) -> Path | None:
    raw = _env_optional_str(name, env_values)
    if raw is None:
        return None
    return _resolve_path(raw, base_dir)


def _env_optional_path_str(
    name: str,
    env_values: dict[str, str],
    base_dir: Path,
) -> str | None:
    resolved = _env_optional_path(name, env_values, base_dir)
    if resolved is None:
        return None
    return str(resolved)


def _env_csv(name: str, env_values: dict[str, str]) -> tuple[str, ...]:
    raw = _env_raw(name, env_values)
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())

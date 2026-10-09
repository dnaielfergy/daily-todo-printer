from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelegramUser:
    alias: str
    print_on_create: bool = False


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    users: dict[int, TelegramUser]
    printer_name: str | None = None
    poll_timeout: int = 30


@dataclass(frozen=True)
class DailySettings:
    max_items: int = 10
    printer_name: str | None = None


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False
    model: str = "jaredpalmer/kev-0.8b@v1.0"
    python_path: Path = Path(".ai/kev/.venv/Scripts/python.exe")
    bridge_path: Path = Path("scripts/kev_rank.py")
    device: str = "auto"
    timeout_seconds: int = 180


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


def load_telegram_settings(path: Path) -> TelegramSettings:
    data = _load_config(path)
    telegram = data.get("telegram")
    if not isinstance(telegram, dict):
        raise ValueError("config must contain a [telegram] section")

    token = os.getenv("TELEGRAM_BOT_TOKEN") or str(telegram.get("bot_token", "")).strip()
    if not token:
        raise ValueError("set telegram.bot_token or TELEGRAM_BOT_TOKEN")

    printer_name = str(telegram.get("printer_name", "")).strip() or None
    poll_timeout = int(telegram.get("poll_timeout", 30))
    if poll_timeout < 1 or poll_timeout > 50:
        raise ValueError("telegram.poll_timeout must be between 1 and 50 seconds")

    raw_users = telegram.get("users", {})
    if not isinstance(raw_users, dict):
        raise ValueError("telegram.users must be a table")

    users: dict[int, TelegramUser] = {}
    for raw_id, value in raw_users.items():
        if not isinstance(value, dict):
            raise ValueError(f"telegram.users.{raw_id} must be a table")
        try:
            user_id = int(raw_id)
        except ValueError as exc:
            raise ValueError(f"invalid Telegram user ID: {raw_id}") from exc
        alias = str(value.get("alias", "")).strip()
        if not alias:
            raise ValueError(f"telegram.users.{raw_id}.alias is required")
        users[user_id] = TelegramUser(
            alias=alias,
            print_on_create=bool(value.get("print_on_create", False)),
        )

    return TelegramSettings(
        bot_token=token,
        users=users,
        printer_name=printer_name,
        poll_timeout=poll_timeout,
    )


def load_daily_settings(path: Path) -> DailySettings:
    data = _load_config(path)
    daily = data.get("daily", {})
    if not isinstance(daily, dict):
        raise ValueError("daily must be a table")

    max_items = int(daily.get("max_items", 10))
    if max_items < 1:
        raise ValueError("daily.max_items must be at least 1")

    printer_name = str(daily.get("printer_name", "")).strip() or None
    if printer_name is None:
        telegram = data.get("telegram", {})
        if isinstance(telegram, dict):
            printer_name = str(telegram.get("printer_name", "")).strip() or None

    return DailySettings(max_items=max_items, printer_name=printer_name)


def load_ai_settings(path: Path) -> AISettings:
    data = _load_config(path)
    ai = data.get("ai", {})
    if not isinstance(ai, dict):
        raise ValueError("ai must be a table")

    device = str(ai.get("device", "auto")).strip().lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("ai.device must be auto, cpu, or cuda")

    timeout_seconds = int(ai.get("timeout_seconds", 180))
    if timeout_seconds < 1:
        raise ValueError("ai.timeout_seconds must be at least 1")

    model = str(ai.get("model", "jaredpalmer/kev-0.8b@v1.0")).strip()
    if not model:
        raise ValueError("ai.model cannot be empty")

    return AISettings(
        enabled=bool(ai.get("enabled", False)),
        model=model,
        python_path=Path(
            str(ai.get("python_path", ".ai/kev/.venv/Scripts/python.exe")).strip()
        ),
        bridge_path=Path(str(ai.get("bridge_path", "scripts/kev_rank.py")).strip()),
        device=device,
        timeout_seconds=timeout_seconds,
    )

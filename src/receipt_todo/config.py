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


def load_telegram_settings(path: Path) -> TelegramSettings:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
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

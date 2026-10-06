from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .config import TelegramSettings, TelegramUser
from .db import (
    add_telegram_task_once,
    cancel_task,
    complete_task,
    get_state_int,
    get_telegram_response,
    list_open_tasks,
    record_telegram_update,
    set_state_int,
)
from .printer import print_raw_windows
from .receipt import escpos_receipt, render_daily_text, render_incoming_task

HELP_TEXT = "\n".join([
    "Send any message to add a todo.",
    "",
    "/add Buy milk",
    "/done 12",
    "/cancel 12",
    "/list",
    "/print",
    "/help",
])


@dataclass(frozen=True)
class HandleResult:
    chat_id: int | None
    reply: str | None
    duplicate: bool = False


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, payload: dict, *, timeout: int = 20):
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=body,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed")
        return data.get("result")

    def get_updates(self, *, offset: int, timeout: int) -> list[dict]:
        result = self._post(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=timeout + 10,
        )
        return result or []

    def send_message(self, chat_id: int, text: str) -> None:
        self._post("sendMessage", {"chat_id": chat_id, "text": text})


def _command_name(token: str) -> str:
    return token[1:].split("@", 1)[0].lower()


def _task_ids(parts: list[str], command: str) -> list[int]:
    if not parts:
        raise ValueError(f"Usage: /{command} <id> [id ...]")
    ids: list[int] = []
    for raw in parts:
        try:
            task_id = int(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid task ID: {raw}") from exc
        if task_id < 1:
            raise ValueError(f"Invalid task ID: {raw}")
        ids.append(task_id)
    return ids


class TelegramHandler:
    def __init__(
        self,
        conn,
        settings: TelegramSettings,
        *,
        print_func: Callable[[bytes, str | None], None] = print_raw_windows,
    ) -> None:
        self.conn = conn
        self.settings = settings
        self.print_func = print_func

    def handle(self, update: dict) -> HandleResult:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return HandleResult(None, None)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        text = message.get("text")

        existing = get_telegram_response(self.conn, update_id)
        if existing is not None:
            return HandleResult(chat_id if isinstance(chat_id, int) else None, existing, duplicate=True)

        if not isinstance(chat_id, int) or not isinstance(user_id, int) or not isinstance(text, str):
            record_telegram_update(self.conn, update_id, "")
            return HandleResult(None, None)

        user = self.settings.users.get(user_id)
        if user is None:
            reply = f"Not authorized. Your Telegram user ID is {user_id}."
            record_telegram_update(self.conn, update_id, reply)
            return HandleResult(chat_id, reply)

        try:
            reply = self._handle_text(update_id, text.strip(), user_id, user)
        except (KeyError, ValueError) as exc:
            reply = str(exc).strip("'")
            record_telegram_update(self.conn, update_id, reply)
        return HandleResult(chat_id, reply)

    def _handle_text(self, update_id: int, text: str, user_id: int, user: TelegramUser) -> str:
        if not text:
            reply = "Send a todo or /help for commands."
            record_telegram_update(self.conn, update_id, reply)
            return reply

        if not text.startswith("/"):
            return self._create_task(update_id, text, user)

        parts = text.split()
        command = _command_name(parts[0])
        args = parts[1:]

        if command in {"help", "start"}:
            record_telegram_update(self.conn, update_id, HELP_TEXT)
            return HELP_TEXT

        if command == "whoami":
            reply = f"Your Telegram user ID is {user_id}. Alias: {user.alias}."
            record_telegram_update(self.conn, update_id, reply)
            return reply

        if command == "add":
            task_text = text[len(parts[0]):].strip()
            if not task_text:
                raise ValueError("Usage: /add <todo>")
            return self._create_task(update_id, task_text, user)

        if command in {"done", "cancel"}:
            ids = _task_ids(args, command)
            changed = []
            for task_id in ids:
                task = complete_task(self.conn, task_id) if command == "done" else cancel_task(self.conn, task_id)
                changed.append(f"#{task.id} {task.text}")
            verb = "Done" if command == "done" else "Cancelled"
            reply = verb + ": " + "; ".join(changed)
            record_telegram_update(self.conn, update_id, reply)
            return reply

        if command == "list":
            tasks = list_open_tasks(self.conn)
            if not tasks:
                reply = "No open tasks."
            else:
                lines = ["OPEN TASKS", ""]
                lines.extend(f"#{task.id} {task.text}" for task in tasks)
                lines.extend(["", f"{len(tasks)} open"])
                reply = "\n".join(lines)
            record_telegram_update(self.conn, update_id, reply)
            return reply

        if command == "print":
            tasks = list_open_tasks(self.conn)
            try:
                self.print_func(escpos_receipt(render_daily_text(tasks)), self.settings.printer_name)
                reply = f"Printed {len(tasks)} open tasks."
            except Exception:
                reply = "Print failed. The task list was not changed."
            record_telegram_update(self.conn, update_id, reply)
            return reply

        raise ValueError(f"Unknown command: /{command}. Try /help.")

    def _create_task(self, update_id: int, text: str, user: TelegramUser) -> str:
        task, reply, created = add_telegram_task_once(
            self.conn,
            update_id,
            text,
            created_by=user.alias,
            response_factory=lambda task: f"Added #{task.id}: {task.text}",
        )
        if created and user.print_on_create:
            try:
                self.print_func(escpos_receipt(render_incoming_task(task)), self.settings.printer_name)
            except Exception:
                reply = reply + " (ticket print failed)"
        return reply


def run_listener(conn, settings: TelegramSettings) -> None:
    client = TelegramClient(settings.bot_token)
    handler = TelegramHandler(conn, settings)
    offset = get_state_int(conn, "telegram_offset", 0)
    print("Telegram listener running. Press Ctrl+C to stop.")

    while True:
        try:
            updates = client.get_updates(offset=offset, timeout=settings.poll_timeout)
            for update in updates:
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                result = handler.handle(update)
                if result.chat_id is not None and result.reply:
                    client.send_message(result.chat_id, result.reply)
                offset = max(offset, update_id + 1)
                set_state_int(conn, "telegram_offset", offset)
        except KeyboardInterrupt:
            raise
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            print(f"Telegram listener error: {type(exc).__name__}")
            time.sleep(3)

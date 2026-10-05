from pathlib import Path
import tempfile
import unittest

from receipt_todo.config import TelegramSettings, TelegramUser, load_telegram_settings
from receipt_todo.db import connect, get_task, list_open_tasks
from receipt_todo.telegram import TelegramHandler


def update(update_id: int, user_id: int, text: str, chat_id: int = 99) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": user_id},
            "text": text,
        },
    }


class TelegramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "tasks.db")
        self.prints: list[tuple[bytes, str | None]] = []
        self.settings = TelegramSettings(
            bot_token="test-token",
            users={
                101: TelegramUser(alias="daniel"),
                202: TelegramUser(alias="alex", print_on_create=True),
            },
            printer_name="TEST PRINTER",
        )
        self.handler = TelegramHandler(
            self.conn,
            self.settings,
            print_func=lambda data, name: self.prints.append((data, name)),
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_config_maps_allowed_user_to_alias(self) -> None:
        path = Path(self.tmp.name) / "config.toml"
        path.write_text(
            """
[telegram]
bot_token = "abc"

[telegram.users."123456"]
alias = "sam"
print_on_create = true
""".strip(),
            encoding="utf-8",
        )
        settings = load_telegram_settings(path)
        self.assertEqual(settings.users[123456].alias, "sam")
        self.assertTrue(settings.users[123456].print_on_create)

    def test_unauthorized_user_cannot_read_or_mutate_tasks(self) -> None:
        result = self.handler.handle(update(1, 999, "/list"))
        self.assertIn("Not authorized", result.reply or "")
        self.assertIn("999", result.reply or "")
        self.assertEqual(list_open_tasks(self.conn), [])

    def test_plain_message_creates_task_with_alias(self) -> None:
        result = self.handler.handle(update(2, 101, "Buy dog food"))
        tasks = list_open_tasks(self.conn)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].text, "Buy dog food")
        self.assertEqual(tasks[0].created_by, "daniel")
        self.assertEqual(tasks[0].source, "telegram")
        self.assertEqual(result.reply, f"Added #{tasks[0].id}: Buy dog food")

    def test_done_and_cancel_commands(self) -> None:
        first = self.handler.handle(update(3, 101, "First task"))
        second = self.handler.handle(update(4, 101, "Second task"))
        first_id = int(first.reply.split("#", 1)[1].split(":", 1)[0])
        second_id = int(second.reply.split("#", 1)[1].split(":", 1)[0])

        self.handler.handle(update(5, 101, f"/done {first_id}"))
        self.handler.handle(update(6, 101, f"/cancel {second_id}"))

        self.assertEqual(get_task(self.conn, first_id).status, "done")
        self.assertEqual(get_task(self.conn, second_id).status, "cancelled")

    def test_duplicate_update_does_not_create_duplicate_task(self) -> None:
        first = self.handler.handle(update(7, 101, "Only once"))
        second = self.handler.handle(update(7, 101, "Only once"))
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(list_open_tasks(self.conn)), 1)

    def test_configured_user_prints_incoming_ticket(self) -> None:
        self.handler.handle(update(8, 202, "Pick up prescription"))
        self.assertEqual(len(self.prints), 1)
        data, printer_name = self.prints[0]
        self.assertEqual(printer_name, "TEST PRINTER")
        self.assertIn(b"Pick up prescription", data)
        self.assertIn(b"from alex", data)

    def test_non_printing_user_does_not_print_on_create(self) -> None:
        self.handler.handle(update(9, 101, "Silent task"))
        self.assertEqual(self.prints, [])

    def test_print_command_uses_daily_receipt(self) -> None:
        self.handler.handle(update(10, 101, "Task to print"))
        result = self.handler.handle(update(11, 101, "/print"))
        self.assertEqual(result.reply, "Printed 1 open tasks.")
        self.assertEqual(len(self.prints), 1)
        self.assertIn(b"Task to print", self.prints[0][0])


if __name__ == "__main__":
    unittest.main()

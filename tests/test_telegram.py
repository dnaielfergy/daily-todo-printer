from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from receipt_todo.config import TelegramSettings, TelegramUser, load_telegram_settings
from receipt_todo.db import connect, get_task, list_open_tasks
from receipt_todo.recipes import submit_recipe_job
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
                101: TelegramUser(alias="sam"),
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

    def test_config_accepts_utf8_bom_from_windows_powershell(self) -> None:
        path = Path(self.tmp.name) / "config-bom.toml"
        path.write_text(
            """[telegram]
bot_token = "abc"

[telegram.users."123456"]
alias = "sam"
""",
            encoding="utf-8-sig",
        )
        settings = load_telegram_settings(path)
        self.assertEqual(settings.users[123456].alias, "sam")

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
        self.assertEqual(tasks[0].created_by, "sam")
        self.assertEqual(tasks[0].source, "telegram")
        self.assertEqual(tasks[0].priority, "medium")
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

    def test_priority_and_due_commands(self) -> None:
        created = self.handler.handle(update(12, 101, "Plan the trip"))
        task_id = int(created.reply.split("#", 1)[1].split(":", 1)[0])

        priority = self.handler.handle(update(13, 101, f"/priority {task_id} high"))
        due = self.handler.handle(update(14, 101, f"/due {task_id} 2026-10-10"))

        self.assertEqual(priority.reply, f"Priority #{task_id}: high")
        self.assertEqual(due.reply, f"Due #{task_id}: 2026-10-10")
        task = get_task(self.conn, task_id)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.due_at, "2026-10-10")

        cleared = self.handler.handle(update(15, 101, f"/due {task_id} clear"))
        self.assertEqual(cleared.reply, f"Due #{task_id}: none")
        self.assertIsNone(get_task(self.conn, task_id).due_at)

    def test_print_command_uses_daily_receipt(self) -> None:
        self.handler.handle(update(10, 101, "Task to print"))
        result = self.handler.handle(update(11, 101, "/print"))
        self.assertEqual(result.reply, "Printed 1 open tasks.")
        self.assertEqual(len(self.prints), 1)
        self.assertIn(b"Task to print", self.prints[0][0])


    def _published_meal(self, when: date, meal: str = "lunch", *, active: bool = True) -> str:
        slot = when.isoformat()
        job_id = f"test-{slot}-{meal}"
        payload = {
            "schema_version": 1,
            "job_id": job_id,
            "date": slot,
            "meal": meal,
            "recipes": [
                {"title": "Burger", "servings": 2,
                 "ingredients": [{"name": "Ground beef", "amount": "1/2 lb"}],
                 "steps": ["Cook burger."]},
                {"title": "Potatoes", "servings": 2,
                 "ingredients": [{"name": "Potatoes", "amount": "2"}],
                 "steps": ["Roast potatoes."]},
            ],
        }
        submit_recipe_job(self.conn, payload)
        week = when - timedelta(days=when.weekday())
        with self.conn:
            self.conn.execute(
                "UPDATE recipe_print_jobs SET inbox_week=?, inbox_active=? WHERE job_id=?",
                (week.isoformat(), int(active), job_id),
            )
        return job_id

    def test_recipe_print_today_is_one_receipt_and_idempotent(self) -> None:
        job_id = self._published_meal(date.today())
        with patch("receipt_todo.recipes.print_raw_windows", side_effect=lambda b, n: self.prints.append((b, n))):
            first = self.handler.handle(update(101, 101, "/recipe-print lunch"))
            repeated_update = self.handler.handle(update(101, 101, "/recipe-print lunch"))
            repeated_command = self.handler.handle(update(102, 101, "/recipe-print lunch"))
        self.assertIn("Printed lunch", first.reply or "")
        self.assertIn("Burger + Potatoes", first.reply or "")
        self.assertTrue(repeated_update.duplicate)
        self.assertIn("Already printed", repeated_command.reply or "")
        self.assertEqual(len(self.prints), 1)
        self.assertEqual(self.prints[0][1], "TEST PRINTER")
        self.assertIn(b"Burger", self.prints[0][0])
        self.assertIn(b"Potatoes", self.prints[0][0])
        row = self.conn.execute("SELECT status, print_count FROM recipe_print_jobs WHERE job_id=?", (job_id,)).fetchone()
        self.assertEqual((row["status"], row["print_count"]), ("printed", 1))

    def test_recipe_print_uses_explicit_date_tomorrow_and_weekday(self) -> None:
        tomorrow = date.today() + timedelta(days=1)
        job_id = self._published_meal(tomorrow, "dinner")
        with patch("receipt_todo.recipes.print_raw_windows", side_effect=lambda b, n: self.prints.append((b, n))):
            self.assertIn("Printed dinner", self.handler.handle(update(103, 101, "/recipe-print dinner tomorrow")).reply or "")
            self.assertIn("Already printed", self.handler.handle(update(104, 101, f"/recipe-print dinner {tomorrow.isoformat()}")).reply or "")
        self.assertEqual(len(self.prints), 1)
        self.assertEqual(self.conn.execute("SELECT print_count FROM recipe_print_jobs WHERE job_id=?", (job_id,)).fetchone()[0], 1)

        monday = date.today() - timedelta(days=date.today().weekday())
        if monday != tomorrow:
            self._published_meal(monday, "lunch")
            with patch("receipt_todo.recipes.print_raw_windows", side_effect=lambda b, n: self.prints.append((b, n))):
                result = self.handler.handle(update(105, 101, "/recipe-print lunch MONDAY"))
            self.assertIn("Printed lunch", result.reply or "")
            self.assertEqual(len(self.prints), 2)

    def test_recipe_print_rejects_unauthorized_invalid_and_missing_meals(self) -> None:
        self._published_meal(date.today())
        self.assertIn("Not authorized", self.handler.handle(update(106, 999, "/recipe-print lunch")).reply or "")
        for number, command in enumerate((
            "/recipe-print", "/recipe-print breakfast",
            "/recipe-print lunch --force", "/recipe-print lunch 2026-99-99",
            "/recipe-print dinner tomorrow extra",
        ), start=107):
            self.assertTrue(self.handler.handle(update(number, 101, command)).reply)
        self.assertIn("No published dinner", self.handler.handle(update(112, 101, "/recipe-print dinner")).reply or "")
        self.assertEqual(self.prints, [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM recipe_print_jobs WHERE status='printed'").fetchone()[0], 0)

    def test_recipe_print_excludes_withdrawn_and_manual_jobs(self) -> None:
        self._published_meal(date.today(), active=False)
        self.assertIn("No published", self.handler.handle(update(113, 101, "/recipe-print lunch")).reply or "")
        with self.conn:
            self.conn.execute("UPDATE recipe_print_jobs SET inbox_active=1, inbox_week=NULL")
        self.assertIn("No published", self.handler.handle(update(114, 101, "/recipe-print lunch")).reply or "")
        self.assertEqual(self.prints, [])

    def test_recipe_print_failure_needs_review_without_auto_retry(self) -> None:
        job_id = self._published_meal(date.today())
        with patch("receipt_todo.recipes.print_raw_windows", side_effect=OSError("spooler disconnected")) as spool:
            reply = self.handler.handle(update(115, 101, "/recipe-print lunch"))
            again = self.handler.handle(update(116, 101, "/recipe-print lunch"))
        self.assertIn("needs review", reply.reply or "")
        self.assertIn("needs review", again.reply or "")
        self.assertEqual(spool.call_count, 1)
        status = self.conn.execute("SELECT status FROM recipe_print_jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        self.assertEqual(status, "needs_review")


if __name__ == "__main__":
    unittest.main()

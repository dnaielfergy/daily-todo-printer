from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from receipt_todo.db import (
    add_task,
    complete_task,
    connect,
    get_task,
    list_open_tasks,
    set_task_due_date,
    set_task_priority,
)
from receipt_todo.receipt import escpos_receipt, render_daily_text


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "tasks.db")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_add_complete_and_list(self) -> None:
        first = add_task(self.conn, "Call dentist")
        second = add_task(self.conn, "Send invoice", priority="high")
        self.assertEqual(first.priority, "medium")
        self.assertEqual(second.priority, "high")
        self.assertEqual([t.id for t in list_open_tasks(self.conn)], [first.id, second.id])
        complete_task(self.conn, second.id)
        self.assertEqual([t.id for t in list_open_tasks(self.conn)], [first.id])

    def test_priority_and_due_date_are_explicit_task_truth(self) -> None:
        task = add_task(self.conn, "File paperwork")
        task = set_task_priority(self.conn, task.id, "low")
        self.assertEqual(task.priority, "low")

        task = set_task_due_date(self.conn, task.id, "2026-10-10")
        self.assertEqual(task.due_at, "2026-10-10")

        task = set_task_due_date(self.conn, task.id, None)
        self.assertIsNone(task.due_at)

        with self.assertRaises(ValueError):
            set_task_priority(self.conn, task.id, "urgent")
        with self.assertRaises(ValueError):
            set_task_due_date(self.conn, task.id, "tomorrow")

    def test_receipt_has_stable_task_ids_and_priority(self) -> None:
        task = add_task(
            self.conn,
            "Pick up milk",
            created_by="alex",
            source="telegram",
            priority="high",
        )
        text = render_daily_text(
            list_open_tasks(self.conn),
            now=datetime(2026, 10, 4, 8, 0),
        )
        self.assertIn(f"{task.id:03d}", text)
        self.assertIn("[H]", text)
        self.assertIn("from alex", text)
        self.assertTrue(escpos_receipt(text).endswith(b"\x1dV\x00"))

    def test_legacy_priority_override_migrates_once(self) -> None:
        self.conn.close()
        path = Path(self.tmp.name) / "legacy.db"
        raw = sqlite3.connect(path)
        raw.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                due_at TEXT,
                created_by TEXT NOT NULL DEFAULT 'local',
                source TEXT NOT NULL DEFAULT 'cli',
                priority_override TEXT
            )
            """
        )
        raw.execute(
            """
            INSERT INTO tasks (
                text, status, created_at, created_by, source, priority_override
            ) VALUES ('Important', 'open', '2026-10-01T00:00:00+00:00', 'local', 'cli', 'must_do')
            """
        )
        raw.execute(
            """
            INSERT INTO tasks (
                text, status, created_at, created_by, source, priority_override
            ) VALUES ('Normal', 'open', '2026-10-02T00:00:00+00:00', 'local', 'cli', NULL)
            """
        )
        raw.commit()
        raw.close()

        migrated = connect(path)
        self.assertEqual(get_task(migrated, 1).priority, "high")
        self.assertEqual(get_task(migrated, 2).priority, "medium")
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(tasks)")}
        self.assertIn("priority", columns)
        self.assertNotIn("priority_override", columns)
        migrated.close()

        self.conn = connect(Path(self.tmp.name) / "tasks.db")


if __name__ == "__main__":
    unittest.main()

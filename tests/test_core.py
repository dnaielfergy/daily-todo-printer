from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from receipt_todo.db import add_task, complete_task, connect, list_open_tasks
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
        second = add_task(self.conn, "Send invoice", priority_override="must_do")
        self.assertEqual([t.id for t in list_open_tasks(self.conn)], [second.id, first.id])
        complete_task(self.conn, second.id)
        self.assertEqual([t.id for t in list_open_tasks(self.conn)], [first.id])

    def test_receipt_has_stable_task_ids(self) -> None:
        task = add_task(self.conn, "Pick up milk", created_by="alex", source="telegram")
        text = render_daily_text(list_open_tasks(self.conn), now=datetime(2026, 10, 4, 8, 0))
        self.assertIn(f"{task.id:03d}", text)
        self.assertIn("from alex", text)
        self.assertTrue(escpos_receipt(text).endswith(b"\x1dV\x00"))


if __name__ == "__main__":
    unittest.main()

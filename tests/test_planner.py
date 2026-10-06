from datetime import date
from pathlib import Path
import tempfile
import unittest

from receipt_todo.config import load_daily_settings
from receipt_todo.db import Task, add_task, connect, list_open_tasks
from receipt_todo.planner import (
    get_daily_plan,
    get_or_create_daily_plan,
    print_daily_plan,
    rank_tasks,
    render_plan,
)


def task(
    task_id: int,
    *,
    priority: str = "medium",
    due_at: str | None = None,
    created_at: str = "2026-10-01T00:00:00+00:00",
) -> Task:
    return Task(
        id=task_id,
        text=f"Task {task_id}",
        status="open",
        created_at=created_at,
        completed_at=None,
        due_at=due_at,
        created_by="local",
        source="cli",
        priority=priority,
    )


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "tasks.db")
        self.today = date(2026, 10, 5)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_deterministic_priority_and_deadline_order(self) -> None:
        tasks = [
            task(1, priority="high", due_at="2026-10-04"),    # 5
            task(2, priority="medium", due_at="2026-10-04"),  # 4 overdue
            task(3, priority="high", due_at="2026-10-05"),    # 4 today
            task(4, priority="low", due_at="2026-10-04"),     # 3 overdue
            task(5, priority="medium", due_at="2026-10-05"),  # 3 today
            task(6, priority="high", due_at="2026-10-06"),    # 3 future
            task(7, priority="high"),                          # 3 none
            task(8, priority="medium"),                        # 2
            task(9, priority="low", due_at="2026-10-05"),     # 2 today
            task(10, priority="low"),                          # 1
        ]
        ranked = rank_tasks(tasks, today=self.today)
        self.assertEqual(
            [item.id for item in ranked],
            [1, 2, 3, 4, 5, 6, 7, 9, 8, 10],
        )

    def test_future_due_oldest_and_id_tiebreakers(self) -> None:
        tasks = [
            task(3, priority="medium", due_at="2026-10-08"),
            task(2, priority="medium", due_at="2026-10-06"),
            task(
                4,
                priority="medium",
                created_at="2026-10-03T00:00:00+00:00",
            ),
            task(
                1,
                priority="medium",
                created_at="2026-09-30T00:00:00+00:00",
            ),
            task(
                5,
                priority="medium",
                created_at="2026-10-03T00:00:00+00:00",
            ),
        ]
        ranked = rank_tasks(tasks, today=self.today)
        self.assertEqual([item.id for item in ranked], [2, 3, 1, 4, 5])

    def test_malformed_due_date_is_treated_as_undated(self) -> None:
        malformed = task(1, priority="medium", due_at="not-a-date")
        valid_future = task(2, priority="medium", due_at="2026-10-06")
        ranked = rank_tasks([malformed, valid_future], today=self.today)
        self.assertEqual([item.id for item in ranked], [2, 1])

    def test_plan_ranks_all_tasks_before_max_items_cutoff(self) -> None:
        first = add_task(self.conn, "First", priority="high")
        second = add_task(self.conn, "Second", priority="medium")
        third = add_task(self.conn, "Third", priority="low")

        plan = get_or_create_daily_plan(
            self.conn,
            max_items=2,
            plan_date=self.today,
        )

        self.assertEqual(len(plan.items), 3)
        self.assertEqual([item.task.id for item in plan.selected], [first.id, second.id])
        self.assertEqual([item.deterministic_rank for item in plan.items], [1, 2, 3])
        self.assertTrue(all(item.ai_rank is None for item in plan.items))
        self.assertEqual([item.final_rank for item in plan.items], [1, 2, 3])
        self.assertEqual(len(list_open_tasks(self.conn)), 3)
        self.assertNotIn(third.id, [item.task.id for item in plan.selected])

    def test_same_day_retry_reuses_existing_plan(self) -> None:
        add_task(self.conn, "Original")
        first = get_or_create_daily_plan(
            self.conn,
            max_items=10,
            plan_date=self.today,
        )
        add_task(self.conn, "Added later")

        second = get_or_create_daily_plan(
            self.conn,
            max_items=1,
            plan_date=self.today,
        )

        self.assertEqual(second.id, first.id)
        self.assertEqual(second.max_items, 10)
        self.assertEqual(len(second.items), 1)

    def test_preview_does_not_mark_plan_printed(self) -> None:
        add_task(self.conn, "Preview me")
        plan = get_or_create_daily_plan(
            self.conn,
            max_items=10,
            plan_date=self.today,
        )
        text = render_plan(plan)
        self.assertIn("Preview me", text)
        self.assertIsNone(
            get_daily_plan(self.conn, plan_date=self.today).printed_at
        )

    def test_print_is_idempotent_and_force_reprints(self) -> None:
        add_task(self.conn, "Print me")
        calls = []

        def fake_print(data: bytes, printer_name: str | None) -> None:
            calls.append((data, printer_name))

        first, did_print = print_daily_plan(
            self.conn,
            max_items=10,
            printer_name="TEST",
            plan_date=self.today,
            print_func=fake_print,
        )
        self.assertTrue(did_print)
        self.assertIsNotNone(first.printed_at)
        self.assertEqual(len(calls), 1)

        _, did_print = print_daily_plan(
            self.conn,
            max_items=10,
            printer_name="TEST",
            plan_date=self.today,
            print_func=fake_print,
        )
        self.assertFalse(did_print)
        self.assertEqual(len(calls), 1)

        _, did_print = print_daily_plan(
            self.conn,
            max_items=10,
            printer_name="TEST",
            plan_date=self.today,
            force=True,
            print_func=fake_print,
        )
        self.assertTrue(did_print)
        self.assertEqual(len(calls), 2)

    def test_printer_failure_leaves_plan_retryable(self) -> None:
        add_task(self.conn, "Retry me")

        def fail_print(data: bytes, printer_name: str | None) -> None:
            raise RuntimeError("printer offline")

        with self.assertRaises(RuntimeError):
            print_daily_plan(
                self.conn,
                max_items=10,
                plan_date=self.today,
                print_func=fail_print,
            )

        plan = get_daily_plan(self.conn, plan_date=self.today)
        self.assertIsNotNone(plan)
        self.assertIsNone(plan.printed_at)

    def test_daily_config_defaults_and_validates_max_items(self) -> None:
        missing = Path(self.tmp.name) / "missing.toml"
        self.assertEqual(load_daily_settings(missing).max_items, 10)

        configured = Path(self.tmp.name) / "configured.toml"
        configured.write_text(
            "[daily]\nmax_items = 7\n",
            encoding="utf-8",
        )
        self.assertEqual(load_daily_settings(configured).max_items, 7)

        invalid = Path(self.tmp.name) / "invalid.toml"
        invalid.write_text(
            "[daily]\nmax_items = 0\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            load_daily_settings(invalid)


if __name__ == "__main__":
    unittest.main()

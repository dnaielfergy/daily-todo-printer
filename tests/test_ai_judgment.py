from pathlib import Path
import tempfile
import unittest

from receipt_todo.ai_judgment import (
    DEFAULT_JUDGMENT_SET,
    load_judgment_set,
    score_judgments,
    seed_judgment_database,
)
from receipt_todo.db import DEFAULT_DB, connect, list_open_tasks


class AIJudgmentTests(unittest.TestCase):
    def test_checked_in_judgment_set_has_expected_scale_and_mix(self) -> None:
        dataset = load_judgment_set(DEFAULT_JUDGMENT_SET)

        self.assertEqual(len(dataset.tasks), 100)
        self.assertEqual(len(dataset.judgments), 100)
        self.assertEqual(dataset.reference_date.isoformat(), "2026-10-09")

        priorities = {task.priority for task in dataset.tasks}
        self.assertEqual(priorities, {"low", "medium", "high"})

        self.assertTrue(any(task.due_at is None for task in dataset.tasks))
        self.assertTrue(any(task.due_at < "2026-10-09" for task in dataset.tasks if task.due_at))
        self.assertTrue(any(task.due_at == "2026-10-09" for task in dataset.tasks))
        self.assertTrue(any(task.due_at > "2026-10-09" for task in dataset.tasks if task.due_at))

        counts = {}
        for judgment in dataset.judgments:
            counts[judgment.category] = counts.get(judgment.category, 0) + 1

        self.assertEqual(
            counts,
            {
                "age_tiebreak": 10,
                "deadline": 20,
                "mixed_signal": 10,
                "priority": 20,
                "semantic_importance": 20,
                "semantic_urgency": 20,
            },
        )

    def test_seed_creates_disposable_database_with_stable_fixture_ids(self) -> None:
        dataset = load_judgment_set(DEFAULT_JUDGMENT_SET)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.judgment.db"
            conn = connect(path)
            seed_judgment_database(
                conn,
                db_path=path,
                dataset=dataset,
            )
            tasks = list_open_tasks(conn)
            conn.close()

        self.assertEqual(len(tasks), 100)
        self.assertEqual([task.id for task in tasks], list(range(1, 101)))
        self.assertTrue(all(task.source == "eval" for task in tasks))

    def test_seed_refuses_production_database_path(self) -> None:
        dataset = load_judgment_set(DEFAULT_JUDGMENT_SET)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scratch.db"
            conn = connect(path)
            with self.assertRaisesRegex(ValueError, "refusing to seed"):
                seed_judgment_database(
                    conn,
                    db_path=DEFAULT_DB,
                    dataset=dataset,
                )
            conn.close()

    def test_score_judgments_reports_category_failures(self) -> None:
        dataset = load_judgment_set(DEFAULT_JUDGMENT_SET)
        order = [task.id for task in dataset.tasks]
        score = score_judgments(order, dataset.judgments)

        self.assertEqual(score.total, 100)
        self.assertEqual(
            sum(total for _, total in score.by_category.values()),
            100,
        )
        self.assertEqual(score.total - score.passed, len(score.failures))


if __name__ == "__main__":
    unittest.main()

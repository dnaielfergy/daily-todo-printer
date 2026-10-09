from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

from receipt_todo.ai import AIRanking
from receipt_todo.ai_benchmark import (
    _pairwise_agreement,
    _permutations,
    load_benchmark_cases,
    run_stability,
)
from receipt_todo.db import Task
from receipt_todo.planner import rank_tasks


class AIBenchmarkTests(unittest.TestCase):
    def test_checked_in_cases_are_valid_and_intentionally_challenge_baseline(self) -> None:
        cases = load_benchmark_cases(Path("eval/ai-ranking/cases.json"))
        self.assertGreaterEqual(len(cases), 5)

        challenged = 0
        for case in cases:
            deterministic = [
                task.id for task in rank_tasks(case.tasks, today=case.today)
            ]
            positions = {task_id: index for index, task_id in enumerate(deterministic)}
            passed = all(
                positions[before] < positions[after]
                for before, after in case.expect_before
            )
            if not passed:
                challenged += 1

        self.assertGreaterEqual(
            challenged,
            1,
            "benchmark should include at least one case where semantic judgment can beat the deterministic baseline",
        )


    def test_real_task_stability_reports_cutoff_and_rank_ranges(self) -> None:
        tasks = [
            Task(1, "A", "open", "2026-10-01T00:00:00+00:00", None, None, "test", "eval", "medium"),
            Task(2, "B", "open", "2026-10-01T00:00:00+00:00", None, None, "test", "eval", "medium"),
            Task(3, "C", "open", "2026-10-01T00:00:00+00:00", None, None, "test", "eval", "medium"),
        ]
        variants = {
            "real::p0": AIRanking([1, 2, 3], {1: 0.5, 2: 0.3, 3: 0.2}, "test"),
            "real::p1": AIRanking([1, 3, 2], {1: 0.5, 2: 0.2, 3: 0.3}, "test"),
            "real::p2": AIRanking([1, 2, 3], {1: 0.5, 2: 0.3, 3: 0.2}, "test"),
            "real::p3": AIRanking([1, 3, 2], {1: 0.5, 2: 0.2, 3: 0.3}, "test"),
        }

        with patch("receipt_todo.ai_benchmark.run_kev_batch", return_value=variants):
            result = run_stability(
                tasks,
                today=date(2026, 10, 9),
                max_items=1,
                python_path=Path("python.exe"),
                bridge_path=Path("bridge.py"),
                model="test",
                device="cpu",
                timeout_seconds=30,
            )

        self.assertTrue(result.stable_top)
        self.assertTrue(result.stable_selected_set)
        self.assertEqual(result.rank_ranges[1], (1, 1))
        self.assertEqual(result.rank_ranges[2], (2, 3))
        self.assertEqual(result.rank_ranges[3], (2, 3))
        self.assertAlmostEqual(result.min_rank_agreement, 2 / 3)

    def test_permutation_helpers_measure_full_rank_stability(self) -> None:
        cases = load_benchmark_cases(Path("eval/ai-ranking/cases.json"))
        three_task_case = next(case for case in cases if len(case.tasks) == 3)
        permutations = _permutations(three_task_case.tasks)

        self.assertGreaterEqual(len(permutations), 3)
        self.assertEqual(
            len({tuple(task.id for task in permutation) for permutation in permutations}),
            len(permutations),
        )

        self.assertEqual(_pairwise_agreement([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertEqual(_pairwise_agreement([1, 2, 3], [3, 2, 1]), 0.0)
        self.assertAlmostEqual(
            _pairwise_agreement([1, 2, 3], [1, 3, 2]),
            2 / 3,
        )


if __name__ == "__main__":
    unittest.main()

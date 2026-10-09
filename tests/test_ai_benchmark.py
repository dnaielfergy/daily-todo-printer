from pathlib import Path
import unittest

from receipt_todo.ai_benchmark import _pairwise_agreement, _permutations, load_benchmark_cases
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

from pathlib import Path
import unittest

from receipt_todo.ai_benchmark import load_benchmark_cases
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


if __name__ == "__main__":
    unittest.main()

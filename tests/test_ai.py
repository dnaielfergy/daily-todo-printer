from datetime import date
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from receipt_todo.ai import (
    AIRanking,
    AIRankingError,
    build_kev_request,
    consensus_ranking,
    kev_option_permutations,
    rank_from_probabilities,
    run_kev_ranking,
)
from receipt_todo.config import load_ai_settings
from receipt_todo.db import Task


def task(task_id: int, text: str = "Task") -> Task:
    return Task(
        id=task_id,
        text=text,
        status="open",
        created_at="2026-10-01T00:00:00+00:00",
        completed_at=None,
        due_at=None,
        created_by="tester",
        source="cli",
        priority="medium",
    )


class AIRankingTests(unittest.TestCase):
    def test_request_contains_only_intended_task_fields(self) -> None:
        request = build_kev_request([task(7, "Send proposal")], today=date(2026, 10, 9))
        option = request["question"]["criteria"]["7"]

        self.assertEqual(option["text"], "Send proposal")
        self.assertEqual(option["created_by"], "tester")
        self.assertNotIn("status", option)
        self.assertNotIn("source", option)
        self.assertEqual(request["state"]["today"], "2026-10-09")

    def test_probabilities_produce_stable_complete_ranking(self) -> None:
        result = rank_from_probabilities(
            {"2": 0.25, "1": 0.50, "3": 0.25},
            expected_ids=[1, 2, 3],
            model="test",
        )
        self.assertEqual(result.task_ids, [1, 2, 3])
        self.assertEqual(result.probabilities[1], 0.50)

    def test_consensus_averages_permutations_and_is_input_order_independent(self) -> None:
        rankings = [
            AIRanking([1, 2, 3], {1: 0.50, 2: 0.30, 3: 0.20}, "test", inference_ms=5),
            AIRanking([2, 1, 3], {1: 0.35, 2: 0.45, 3: 0.20}, "test", inference_ms=7),
            AIRanking([1, 3, 2], {1: 0.45, 2: 0.20, 3: 0.35}, "test", inference_ms=6),
        ]
        result = consensus_ranking(rankings)
        self.assertEqual(result.task_ids, [1, 2, 3])
        self.assertAlmostEqual(result.probabilities[1], (0.50 + 0.35 + 0.45) / 3)
        self.assertEqual(result.inference_ms, 18)

        first = [task(3), task(1), task(2)]
        second = [task(2), task(3), task(1)]
        self.assertEqual(
            [[item.id for item in permutation] for permutation in kev_option_permutations(first)],
            [[item.id for item in permutation] for permutation in kev_option_permutations(second)],
        )

    def test_invalid_probability_sets_are_rejected(self) -> None:
        invalid = [
            {"1": 1.0},  # missing
            {"1": 0.5, "2": 0.25, "9": 0.25},  # unknown
            {"1": 0.9, "2": 0.9},  # bad total
            {"1": -0.1, "2": 1.1},  # outside range
        ]
        for probabilities in invalid:
            with self.subTest(probabilities=probabilities):
                with self.assertRaises(AIRankingError):
                    rank_from_probabilities(
                        probabilities,
                        expected_ids=[1, 2],
                        model="test",
                    )

    def test_subprocess_environment_excludes_app_secrets_and_forces_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python_path = root / "python.exe"
            bridge_path = root / "bridge.py"
            python_path.write_text("", encoding="utf-8")
            bridge_path.write_text("", encoding="utf-8")

            stdout = json.dumps(
                {
                    "model": "test",
                    "device": "cpu",
                    "load_ms": 10,
                    "results": [
                        {
                            "id": "daily",
                            "probabilities": {"1": 0.7, "2": 0.3},
                            "inference_ms": 5,
                        }
                    ],
                }
            )

            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "must-not-leak",
                    "UNRELATED_SECRET": "also-no",
                    "PATH": os.environ.get("PATH", ""),
                },
                clear=False,
            ), patch(
                "receipt_todo.ai.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout, ""),
            ) as mocked:
                result = run_kev_ranking(
                    [task(1), task(2)],
                    today=date(2026, 10, 9),
                    python_path=python_path,
                    bridge_path=bridge_path,
                    model="test",
                    device="cpu",
                    repo_root=root,
                )

            self.assertEqual(result.task_ids, [1, 2])
            child_env = mocked.call_args.kwargs["env"]
            self.assertNotIn("TELEGRAM_BOT_TOKEN", child_env)
            self.assertNotIn("UNRELATED_SECRET", child_env)
            self.assertEqual(child_env["HF_HUB_OFFLINE"], "1")
            self.assertEqual(child_env["TRANSFORMERS_OFFLINE"], "1")

    def test_ai_config_defaults_disabled_and_validates_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.toml"
            defaults = load_ai_settings(missing)
            self.assertFalse(defaults.enabled)
            self.assertEqual(defaults.model, "jaredpalmer/kev-0.8b@v1.0")

            invalid = root / "invalid.toml"
            invalid.write_text("[ai]\ndevice = \"tpu\"\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_ai_settings(invalid)


if __name__ == "__main__":
    unittest.main()

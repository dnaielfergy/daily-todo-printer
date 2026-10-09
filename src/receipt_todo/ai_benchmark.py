from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

from .ai import AIRanking, run_kev_batch
from .db import Task
from .planner import rank_tasks


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    today: date
    tasks: list[Task]
    expect_before: list[tuple[int, int]]


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    deterministic_ids: list[int]
    ai_ids: list[int]
    deterministic_pass: bool
    ai_pass: bool
    stable_top: bool
    max_probability_delta: float
    inference_ms: float | None


def _task_from_json(value: dict) -> Task:
    return Task(
        id=int(value["id"]),
        text=str(value["text"]),
        status="open",
        created_at=str(value["created_at"]),
        completed_at=None,
        due_at=value.get("due_at"),
        created_by=str(value.get("created_by", "test")),
        source="eval",
        priority=str(value["priority"]),
    )


def load_benchmark_cases(path: Path) -> list[BenchmarkCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("AI benchmark file must contain a non-empty list")

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    for row in raw:
        case_id = str(row["id"]).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"invalid or duplicate benchmark case ID: {case_id!r}")
        seen_ids.add(case_id)

        tasks = [_task_from_json(item) for item in row["tasks"]]
        task_ids = [task.id for task in tasks]
        if not tasks or len(task_ids) != len(set(task_ids)):
            raise ValueError(f"benchmark case {case_id} has empty or duplicate task IDs")

        expectations = [tuple(int(x) for x in pair) for pair in row.get("expect_before", [])]
        known = set(task_ids)
        for before, after in expectations:
            if before not in known or after not in known or before == after:
                raise ValueError(f"benchmark case {case_id} has invalid expectation")

        cases.append(
            BenchmarkCase(
                case_id=case_id,
                today=date.fromisoformat(str(row["today"])),
                tasks=tasks,
                expect_before=expectations,
            )
        )
    return cases


def _passes(order: list[int], expectations: list[tuple[int, int]]) -> bool:
    positions = {task_id: index for index, task_id in enumerate(order)}
    return all(positions[before] < positions[after] for before, after in expectations)


def run_benchmark(
    cases: list[BenchmarkCase],
    *,
    python_path: Path,
    bridge_path: Path,
    model: str,
    device: str,
    timeout_seconds: int,
) -> tuple[list[BenchmarkResult], AIRanking]:
    batch = []
    for case in cases:
        batch.append((case.case_id, case.tasks, case.today))
        batch.append((f"{case.case_id}::reversed", list(reversed(case.tasks)), case.today))

    rankings = run_kev_batch(
        batch,
        python_path=python_path,
        bridge_path=bridge_path,
        model=model,
        device=device,
        timeout_seconds=timeout_seconds,
    )

    results: list[BenchmarkResult] = []
    for case in cases:
        original = rankings[case.case_id]
        reversed_result = rankings[f"{case.case_id}::reversed"]
        deterministic_ids = [
            task.id for task in rank_tasks(case.tasks, today=case.today)
        ]
        deltas = [
            abs(original.probabilities[task.id] - reversed_result.probabilities[task.id])
            for task in case.tasks
        ]
        results.append(
            BenchmarkResult(
                case_id=case.case_id,
                deterministic_ids=deterministic_ids,
                ai_ids=original.task_ids,
                deterministic_pass=_passes(deterministic_ids, case.expect_before),
                ai_pass=_passes(original.task_ids, case.expect_before),
                stable_top=original.task_ids[0] == reversed_result.task_ids[0],
                max_probability_delta=max(deltas, default=0.0),
                inference_ms=original.inference_ms,
            )
        )

    first = rankings[cases[0].case_id]
    return results, first

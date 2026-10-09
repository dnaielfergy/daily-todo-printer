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
    min_rank_agreement: float
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


def _permutations(tasks: list[Task]) -> list[list[Task]]:
    candidates = [
        list(tasks),
        list(reversed(tasks)),
        list(tasks[1:] + tasks[:1]),
        list(tasks[::2] + tasks[1::2]),
        list(tasks[1::2] + tasks[::2]),
    ]
    unique: list[list[Task]] = []
    seen: set[tuple[int, ...]] = set()
    for permutation in candidates:
        ids = tuple(task.id for task in permutation)
        if ids not in seen:
            seen.add(ids)
            unique.append(permutation)
    return unique


def _pairwise_agreement(reference: list[int], candidate: list[int]) -> float:
    if len(reference) < 2:
        return 1.0

    ref_positions = {task_id: index for index, task_id in enumerate(reference)}
    candidate_positions = {task_id: index for index, task_id in enumerate(candidate)}
    agreements = 0
    total = 0
    for index, left in enumerate(reference):
        for right in reference[index + 1 :]:
            total += 1
            ref_before = ref_positions[left] < ref_positions[right]
            candidate_before = candidate_positions[left] < candidate_positions[right]
            agreements += int(ref_before == candidate_before)
    return agreements / total if total else 1.0


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
    permutation_counts: dict[str, int] = {}
    for case in cases:
        permutations = _permutations(case.tasks)
        permutation_counts[case.case_id] = len(permutations)
        for index, tasks in enumerate(permutations):
            batch.append((f"{case.case_id}::p{index}", tasks, case.today))

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
        original = rankings[f"{case.case_id}::p0"]
        variants = [
            rankings[f"{case.case_id}::p{index}"]
            for index in range(permutation_counts[case.case_id])
        ]
        deterministic_ids = [
            task.id for task in rank_tasks(case.tasks, today=case.today)
        ]

        probability_deltas = [
            abs(original.probabilities[task.id] - variant.probabilities[task.id])
            for variant in variants[1:]
            for task in case.tasks
        ]
        agreements = [
            _pairwise_agreement(original.task_ids, variant.task_ids)
            for variant in variants[1:]
        ]

        results.append(
            BenchmarkResult(
                case_id=case.case_id,
                deterministic_ids=deterministic_ids,
                ai_ids=original.task_ids,
                deterministic_pass=_passes(deterministic_ids, case.expect_before),
                ai_pass=_passes(original.task_ids, case.expect_before),
                stable_top=all(
                    variant.task_ids[0] == original.task_ids[0]
                    for variant in variants[1:]
                ),
                min_rank_agreement=min(agreements, default=1.0),
                max_probability_delta=max(probability_deltas, default=0.0),
                inference_ms=original.inference_ms,
            )
        )

    first = rankings[f"{cases[0].case_id}::p0"]
    return results, first

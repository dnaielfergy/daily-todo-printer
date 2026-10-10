from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import sqlite3

from .db import DEFAULT_DB, Task


DEFAULT_JUDGMENT_DB = Path("data/tasks.judgment.db")
DEFAULT_JUDGMENT_SET = Path("eval/ai-ranking/judgment-set.json")


@dataclass(frozen=True)
class Judgment:
    before: int
    after: int
    category: str
    reason: str


@dataclass(frozen=True)
class JudgmentSet:
    reference_date: date
    tasks: list[Task]
    judgments: list[Judgment]


@dataclass(frozen=True)
class JudgmentScore:
    passed: int
    total: int
    by_category: dict[str, tuple[int, int]]
    failures: list[Judgment]

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0


def load_judgment_set(path: Path) -> JudgmentSet:
    raw = json.loads(path.read_text(encoding="utf-8"))
    reference_date = date.fromisoformat(str(raw["reference_date"]))

    tasks: list[Task] = []
    seen_task_ids: set[int] = set()
    for item in raw["tasks"]:
        task_id = int(item["id"])
        if task_id in seen_task_ids:
            raise ValueError(f"duplicate judgment-set task ID: {task_id}")
        seen_task_ids.add(task_id)
        tasks.append(
            Task(
                id=task_id,
                text=str(item["text"]),
                status="open",
                created_at=str(item["created_at"]),
                completed_at=None,
                due_at=item.get("due_at"),
                created_by=str(item.get("created_by", "eval")),
                source="eval",
                priority=str(item["priority"]),
            )
        )

    judgments: list[Judgment] = []
    seen_judgments: set[tuple[int, int]] = set()
    for item in raw["judgments"]:
        judgment = Judgment(
            before=int(item["before"]),
            after=int(item["after"]),
            category=str(item["category"]),
            reason=str(item["reason"]),
        )
        if judgment.before == judgment.after:
            raise ValueError("judgment cannot compare a task with itself")
        if judgment.before not in seen_task_ids or judgment.after not in seen_task_ids:
            raise ValueError(
                f"judgment references unknown task: "
                f"{judgment.before} before {judgment.after}"
            )
        key = (judgment.before, judgment.after)
        if key in seen_judgments:
            raise ValueError(
                f"duplicate judgment: {judgment.before} before {judgment.after}"
            )
        seen_judgments.add(key)
        judgments.append(judgment)

    graph = {task_id: [] for task_id in seen_task_ids}
    for judgment in judgments:
        graph[judgment.before].append(judgment.after)
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(task_id: int) -> None:
        if task_id in visiting:
            raise ValueError("judgment set contains a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for child in graph[task_id]:
            visit(child)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)

    if not tasks:
        raise ValueError("judgment set must contain tasks")
    if not judgments:
        raise ValueError("judgment set must contain judgments")

    return JudgmentSet(
        reference_date=reference_date,
        tasks=tasks,
        judgments=judgments,
    )


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def seed_judgment_database(
    conn: sqlite3.Connection,
    *,
    db_path: Path,
    dataset: JudgmentSet,
) -> None:
    if _same_path(db_path, DEFAULT_DB):
        raise ValueError(
            "refusing to seed the production database; use data/tasks.judgment.db"
        )

    with conn:
        conn.execute("DELETE FROM daily_plan_items")
        conn.execute("DELETE FROM daily_plans")
        conn.execute("DELETE FROM telegram_updates")
        conn.execute("DELETE FROM app_state")
        conn.execute("DELETE FROM tasks")

        for task in dataset.tasks:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, text, status, created_at, completed_at, due_at,
                    created_by, source, priority
                )
                VALUES (?, ?, 'open', ?, NULL, ?, ?, 'eval', ?)
                """,
                (
                    task.id,
                    task.text,
                    task.created_at,
                    task.due_at,
                    task.created_by,
                    task.priority,
                ),
            )


def score_judgments(
    order: list[int],
    judgments: list[Judgment],
) -> JudgmentScore:
    positions = {task_id: index for index, task_id in enumerate(order)}
    passed = 0
    by_category: dict[str, list[int]] = {}
    failures: list[Judgment] = []

    for judgment in judgments:
        if judgment.before not in positions or judgment.after not in positions:
            raise ValueError("ranking is missing judgment-set task IDs")
        success = positions[judgment.before] < positions[judgment.after]
        category = by_category.setdefault(judgment.category, [0, 0])
        category[1] += 1
        if success:
            passed += 1
            category[0] += 1
        else:
            failures.append(judgment)

    return JudgmentScore(
        passed=passed,
        total=len(judgments),
        by_category={
            category: (values[0], values[1])
            for category, values in sorted(by_category.items())
        },
        failures=failures,
    )

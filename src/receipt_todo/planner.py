from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import sqlite3
from typing import Callable

from .db import Task, get_tasks, list_open_tasks
from .printer import print_raw_windows
from .receipt import escpos_receipt, render_daily_text


PRIORITY_SCORE = {"low": 1, "medium": 2, "high": 3}
AIRanker = Callable[[list[Task], date], list[int] | None]


@dataclass(frozen=True)
class RankedTask:
    task: Task
    deterministic_rank: int
    ai_rank: int | None
    final_rank: int
    included: bool


@dataclass(frozen=True)
class DailyPlan:
    id: int
    plan_date: str
    created_at: str
    printed_at: str | None
    max_items: int
    items: list[RankedTask]

    @property
    def selected(self) -> list[RankedTask]:
        return [item for item in self.items if item.included]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_due_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _deadline_state(due: date | None, today: date) -> int:
    if due is None:
        return 3
    if due < today:
        return 0
    if due == today:
        return 1
    return 2


def _deadline_boost(due: date | None, today: date) -> int:
    if due is not None and due < today:
        return 2
    if due == today:
        return 1
    return 0


def rank_tasks(tasks: list[Task], *, today: date | None = None) -> list[Task]:
    today = today or date.today()

    def sort_key(task: Task):
        due = _parse_due_date(task.due_at)
        score = PRIORITY_SCORE[task.priority] + _deadline_boost(due, today)
        return (
            -score,
            _deadline_state(due, today),
            due or date.max,
            task.created_at,
            task.id,
        )

    return sorted(tasks, key=sort_key)


def _ai_rank_map(
    deterministic: list[Task],
    *,
    today: date,
    ai_ranker: AIRanker | None,
    on_ai_error: Callable[[str], None] | None,
) -> dict[int, int] | None:
    if ai_ranker is None or not deterministic:
        return None

    try:
        order = ai_ranker(list(deterministic), today)
    except Exception as exc:
        if on_ai_error is not None:
            on_ai_error(str(exc))
        return None

    if order is None:
        return None

    expected = [task.id for task in deterministic]
    if len(order) != len(expected) or len(set(order)) != len(order) or set(order) != set(expected):
        if on_ai_error is not None:
            on_ai_error("AI ranking did not contain every candidate exactly once")
        return None

    return {task_id: index for index, task_id in enumerate(order, start=1)}


def _load_plan(conn: sqlite3.Connection, row: sqlite3.Row) -> DailyPlan:
    item_rows = conn.execute(
        """
        SELECT task_id, deterministic_rank, ai_rank, final_rank, included
        FROM daily_plan_items
        WHERE plan_id = ?
        ORDER BY final_rank, task_id
        """,
        (row["id"],),
    ).fetchall()
    task_map = get_tasks(conn, [int(item["task_id"]) for item in item_rows])
    items = [
        RankedTask(
            task=task_map[int(item["task_id"])],
            deterministic_rank=int(item["deterministic_rank"]),
            ai_rank=None if item["ai_rank"] is None else int(item["ai_rank"]),
            final_rank=int(item["final_rank"]),
            included=bool(item["included"]),
        )
        for item in item_rows
        if int(item["task_id"]) in task_map
    ]
    return DailyPlan(
        id=int(row["id"]),
        plan_date=str(row["plan_date"]),
        created_at=str(row["created_at"]),
        printed_at=row["printed_at"],
        max_items=int(row["max_items"]),
        items=items,
    )


def get_daily_plan(
    conn: sqlite3.Connection,
    *,
    plan_date: date | None = None,
) -> DailyPlan | None:
    plan_date = plan_date or date.today()
    row = conn.execute(
        "SELECT * FROM daily_plans WHERE plan_date = ?",
        (plan_date.isoformat(),),
    ).fetchone()
    return None if row is None else _load_plan(conn, row)


def get_or_create_daily_plan(
    conn: sqlite3.Connection,
    *,
    max_items: int,
    plan_date: date | None = None,
    ai_ranker: AIRanker | None = None,
    on_ai_error: Callable[[str], None] | None = None,
) -> DailyPlan:
    if max_items < 1:
        raise ValueError("daily.max_items must be at least 1")

    plan_date = plan_date or date.today()
    existing = get_daily_plan(conn, plan_date=plan_date)
    if existing is not None:
        return existing

    deterministic = rank_tasks(list_open_tasks(conn), today=plan_date)
    deterministic_rank = {
        task.id: index for index, task in enumerate(deterministic, start=1)
    }
    ai_rank = _ai_rank_map(
        deterministic,
        today=plan_date,
        ai_ranker=ai_ranker,
        on_ai_error=on_ai_error,
    )

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO daily_plans (plan_date, created_at, max_items)
            VALUES (?, ?, ?)
            """,
            (plan_date.isoformat(), _now(), max_items),
        )
        plan_id = int(cursor.lastrowid)
        for task in deterministic:
            deterministic_position = deterministic_rank[task.id]
            ai_position = None if ai_rank is None else ai_rank[task.id]
            final_position = ai_position or deterministic_position
            conn.execute(
                """
                INSERT INTO daily_plan_items (
                    plan_id, task_id, deterministic_rank, ai_rank,
                    final_rank, included
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    task.id,
                    deterministic_position,
                    ai_position,
                    final_position,
                    int(final_position <= max_items),
                ),
            )

    created = get_daily_plan(conn, plan_date=plan_date)
    if created is None:
        raise RuntimeError("Daily plan was not created")
    return created


def render_plan(plan: DailyPlan, *, now: datetime | None = None) -> str:
    tasks = [item.task for item in plan.selected]
    return render_daily_text(
        tasks,
        now=now,
        open_count=len(plan.items),
        planned_count=len(tasks),
    )


def mark_plan_printed(conn: sqlite3.Connection, plan_id: int) -> None:
    conn.execute(
        "UPDATE daily_plans SET printed_at = ? WHERE id = ?",
        (_now(), plan_id),
    )
    conn.commit()


def print_daily_plan(
    conn: sqlite3.Connection,
    *,
    max_items: int,
    printer_name: str | None = None,
    plan_date: date | None = None,
    force: bool = False,
    print_func: Callable[[bytes, str | None], None] = print_raw_windows,
    ai_ranker: AIRanker | None = None,
    on_ai_error: Callable[[str], None] | None = None,
) -> tuple[DailyPlan, bool]:
    plan = get_or_create_daily_plan(
        conn,
        max_items=max_items,
        plan_date=plan_date,
        ai_ranker=ai_ranker,
        on_ai_error=on_ai_error,
    )
    if plan.printed_at is not None and not force:
        return plan, False

    text = render_plan(plan)
    print_func(escpos_receipt(text), printer_name)
    mark_plan_printed(conn, plan.id)

    refreshed = get_daily_plan(
        conn,
        plan_date=date.fromisoformat(plan.plan_date),
    )
    if refreshed is None:
        raise RuntimeError("Daily plan disappeared after printing")
    return refreshed, True

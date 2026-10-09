from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .ai import run_kev_ranking
from .ai_benchmark import load_benchmark_cases, run_benchmark
from .config import load_ai_settings, load_daily_settings, load_telegram_settings
from .db import (
    DEFAULT_DB,
    add_task,
    complete_task,
    connect,
    list_open_tasks,
    set_task_due_date,
    set_task_priority,
)
from .planner import get_or_create_daily_plan, print_daily_plan, rank_tasks, render_plan
from .printer import print_raw_windows
from .receipt import escpos_receipt, render_daily_text
from .telegram import run_listener


AI_EVAL_DB = Path("data/tasks.test.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="receipt-todo")
    parser.add_argument("--db", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a task")
    add.add_argument("text")
    add.add_argument("--due")
    add.add_argument("--priority", choices=["low", "medium", "high"], default="medium")
    add.add_argument("--by", default="local")
    add.add_argument("--source", default="cli")

    sub.add_parser("list", help="List open tasks")

    done = sub.add_parser("done", help="Complete a task")
    done.add_argument("id", type=int)

    priority = sub.add_parser("priority", help="Set task priority")
    priority.add_argument("id", type=int)
    priority.add_argument("priority", choices=["low", "medium", "high"])

    due = sub.add_parser("due", help="Set or clear a task due date")
    due.add_argument("id", type=int)
    due.add_argument("due_at", help="YYYY-MM-DD or clear")

    sub.add_parser("preview", help="Preview all current open tasks")

    printer = sub.add_parser("print", help="Print all current open tasks on Windows")
    printer.add_argument("--printer-name")

    daily = sub.add_parser("daily", help="Preview or print today's persisted daily plan")
    daily.add_argument("--config", type=Path)
    daily.add_argument("--printer-name")
    daily.add_argument("--preview", action="store_true")
    daily.add_argument("--force", action="store_true")

    ai_eval = sub.add_parser(
        "ai-eval",
        help="Compare Kev ranking with deterministic ranking without persisting a daily plan",
    )
    ai_eval.add_argument("--config", type=Path)
    ai_eval.add_argument("--device", choices=["auto", "cpu", "cuda"])

    ai_benchmark = sub.add_parser(
        "ai-benchmark",
        help="Run the checked-in synthetic Kev ranking benchmark",
    )
    ai_benchmark.add_argument("--config", type=Path)
    ai_benchmark.add_argument(
        "--cases",
        type=Path,
        default=Path("eval/ai-ranking/cases.json"),
    )
    ai_benchmark.add_argument("--device", choices=["auto", "cpu", "cuda"])

    telegram = sub.add_parser("telegram", help="Run the Telegram listener")
    telegram.add_argument("--config", type=Path, default=Path("config.local.toml"))
    return parser


def _daily_config_path(value: Path | None) -> Path:
    if value is not None:
        return value
    dedicated = Path("config.daily.toml")
    return dedicated if dedicated.exists() else Path("config.local.toml")


def _build_ai_ranker(config_path: Path):
    settings = load_ai_settings(config_path)
    if not settings.enabled:
        return None

    def ranker(tasks, today):
        return run_kev_ranking(
            tasks,
            today=today,
            python_path=settings.python_path,
            bridge_path=settings.bridge_path,
            model=settings.model,
            device=settings.device,
            timeout_seconds=settings.timeout_seconds,
        ).task_ids

    return ranker


def _ai_error(message: str) -> None:
    print(f"AI ranking unavailable: {message}; using deterministic ranking.")


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "ai-benchmark":
        config_path = _daily_config_path(args.config)
        settings = load_ai_settings(config_path)
        cases = load_benchmark_cases(args.cases)
        results, runtime = run_benchmark(
            cases,
            python_path=settings.python_path,
            bridge_path=settings.bridge_path,
            model=settings.model,
            device=args.device or settings.device,
            timeout_seconds=settings.timeout_seconds,
        )
        print(f"Model: {runtime.model}")
        print(f"Device: {runtime.device or 'unknown'}")
        if runtime.load_ms is not None:
            print(f"Load: {runtime.load_ms:.0f} ms")
        print("")
        print("case                           det  ai  stable  max-delta  inference")
        for result in results:
            inference = (
                f"{result.inference_ms:.0f} ms"
                if result.inference_ms is not None
                else "-"
            )
            print(
                f"{result.case_id:<30} "
                f"{'pass' if result.deterministic_pass else 'fail':>4} "
                f"{'pass' if result.ai_pass else 'fail':>4} "
                f"{'yes' if result.stable_top else 'NO':>6} "
                f"{result.max_probability_delta:>9.4f}  {inference}"
            )
        print("")
        print(
            f"AI expectations: {sum(result.ai_pass for result in results)}/{len(results)}"
        )
        print(
            "Deterministic expectations: "
            f"{sum(result.deterministic_pass for result in results)}/{len(results)}"
        )
        print(
            "Stable top choice under reversed options: "
            f"{sum(result.stable_top for result in results)}/{len(results)}"
        )
        return

    db_path = args.db or (AI_EVAL_DB if args.command == "ai-eval" else DEFAULT_DB)
    conn = connect(db_path)

    if args.command == "add":
        task = add_task(
            conn,
            args.text,
            due_at=args.due,
            created_by=args.by,
            source=args.source,
            priority=args.priority,
        )
        print(f"Added #{task.id}: {task.text}")
        return

    if args.command == "done":
        task = complete_task(conn, args.id)
        print(f"Done #{task.id}: {task.text}")
        return

    if args.command == "priority":
        task = set_task_priority(conn, args.id, args.priority)
        print(f"Priority #{task.id}: {task.priority}")
        return

    if args.command == "due":
        due_at = None if args.due_at.lower() == "clear" else args.due_at
        task = set_task_due_date(conn, args.id, due_at)
        print(f"Due #{task.id}: {task.due_at or 'none'}")
        return

    if args.command == "telegram":
        run_listener(conn, load_telegram_settings(args.config))
        return

    if args.command == "ai-eval":
        config_path = _daily_config_path(args.config)
        settings = load_ai_settings(config_path)
        tasks = list_open_tasks(conn)
        if not tasks:
            print(f"No open tasks in evaluation database: {db_path}")
            print(
                "Seed it with: receipt-todo --db data/tasks.test.db add "
                '"Example task" --priority high'
            )
            return

        today = date.today()
        deterministic = rank_tasks(tasks, today=today)
        result = run_kev_ranking(
            deterministic,
            today=today,
            python_path=settings.python_path,
            bridge_path=settings.bridge_path,
            model=settings.model,
            device=args.device or settings.device,
            timeout_seconds=settings.timeout_seconds,
        )
        deterministic_rank = {
            task.id: index for index, task in enumerate(deterministic, start=1)
        }
        ai_rank = {task_id: index for index, task_id in enumerate(result.task_ids, start=1)}

        print(f"Database: {db_path}")
        print(f"Model: {result.model}")
        print(f"Device: {result.device or 'unknown'}")
        if result.load_ms is not None:
            print(f"Load: {result.load_ms:.0f} ms")
        if result.inference_ms is not None:
            print(f"Inference: {result.inference_ms:.0f} ms")
        print("")
        print("ID   deterministic   ai   probability")
        for task_id in result.task_ids:
            print(
                f"{task_id:03d}  {deterministic_rank[task_id]:>13}  "
                f"{ai_rank[task_id]:>3}   {result.probabilities[task_id]:.4f}"
            )
        return

    if args.command == "daily":
        config_path = _daily_config_path(args.config)
        settings = load_daily_settings(config_path)
        ai_ranker = _build_ai_ranker(config_path)
        if args.preview:
            plan = get_or_create_daily_plan(
                conn,
                max_items=settings.max_items,
                ai_ranker=ai_ranker,
                on_ai_error=_ai_error,
            )
            print(render_plan(plan))
            return
        printer_name = args.printer_name or settings.printer_name
        plan, printed = print_daily_plan(
            conn,
            max_items=settings.max_items,
            printer_name=printer_name,
            force=args.force,
            ai_ranker=ai_ranker,
            on_ai_error=_ai_error,
        )
        if printed:
            print(f"Printed daily plan with {len(plan.selected)} tasks")
        else:
            print(f"Daily plan for {plan.plan_date} already printed")
        return

    tasks = list_open_tasks(conn)
    if args.command == "list":
        for task in tasks:
            due = f" due {task.due_at}" if task.due_at else ""
            print(f"#{task.id} [{task.priority}] {task.text}{due}")
        return

    receipt = render_daily_text(tasks)
    if args.command == "preview":
        print(receipt)
        return

    if args.command == "print":
        print_raw_windows(escpos_receipt(receipt), args.printer_name)
        print(f"Printed {len(tasks)} open tasks")

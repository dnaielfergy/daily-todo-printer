from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_daily_settings, load_telegram_settings
from .db import (
    DEFAULT_DB,
    add_task,
    complete_task,
    connect,
    list_open_tasks,
    set_task_due_date,
    set_task_priority,
)
from .planner import get_or_create_daily_plan, print_daily_plan, render_plan
from .printer import print_raw_windows
from .receipt import escpos_receipt, render_daily_text
from .telegram import run_listener
from .recipes import (
    get_recipe_job, list_recipe_jobs, load_recipe_job, print_recipe_job,
    requeue_recipe_job, render_recipe_job, submit_recipe_job,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="receipt-todo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
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
    daily.add_argument("--config", type=Path, default=Path("config.local.toml"))
    daily.add_argument("--printer-name")
    daily.add_argument("--preview", action="store_true")
    daily.add_argument("--force", action="store_true")

    recipes = sub.add_parser("recipe", help="Manage recipe print jobs")
    recipes_sub = recipes.add_subparsers(dest="recipe_command", required=True)
    submit = recipes_sub.add_parser("submit")
    submit.add_argument("file", type=Path)
    recipes_sub.add_parser("list")
    preview = recipes_sub.add_parser("preview")
    preview.add_argument("job_id")
    recipe_print = recipes_sub.add_parser("print")
    recipe_print.add_argument("job_id")
    recipe_print.add_argument("--printer-name")
    recipe_print.add_argument("--force", action="store_true")
    requeue = recipes_sub.add_parser("requeue")
    requeue.add_argument("job_id")

    telegram = sub.add_parser("telegram", help="Run the Telegram listener")
    telegram.add_argument("--config", type=Path, default=Path("config.local.toml"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    conn = connect(args.db)

    if args.command == "recipe":
        if args.recipe_command == "submit":
            payload = load_recipe_job(args.file)
            created = submit_recipe_job(conn, payload)
            print(("Queued " if created else "Already queued ") + payload["job_id"])
        elif args.recipe_command == "list":
            for row in list_recipe_jobs(conn):
                payload = json.loads(row["payload_json"])
                print(f'{row["job_id"]} [{row["status"]}] {payload["date"]} {payload["meal"]}: {payload["recipe"]["title"]}')
        elif args.recipe_command == "preview":
            row = get_recipe_job(conn, args.job_id)
            print(render_recipe_job(json.loads(row["payload_json"])))
        elif args.recipe_command == "print":
            printed = print_recipe_job(conn, args.job_id, printer_name=args.printer_name, force=args.force)
            print(("Printed " if printed else "Already printed ") + args.job_id)
        elif args.recipe_command == "requeue":
            requeue_recipe_job(conn, args.job_id)
            print("Requeued " + args.job_id)
        return

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

    if args.command == "daily":
        settings = load_daily_settings(args.config)
        if args.preview:
            plan = get_or_create_daily_plan(conn, max_items=settings.max_items)
            print(render_plan(plan))
            return
        printer_name = args.printer_name or settings.printer_name
        plan, printed = print_daily_plan(
            conn,
            max_items=settings.max_items,
            printer_name=printer_name,
            force=args.force,
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

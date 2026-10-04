from __future__ import annotations

import argparse
from pathlib import Path

from .db import DEFAULT_DB, add_task, complete_task, connect, list_open_tasks
from .printer import print_raw_windows
from .receipt import escpos_receipt, render_daily_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="receipt-todo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a task")
    add.add_argument("text")
    add.add_argument("--due")
    add.add_argument("--by", default="daniel")
    add.add_argument("--source", default="cli")
    add.add_argument("--must-do", action="store_true")

    sub.add_parser("list", help="List open tasks")

    done = sub.add_parser("done", help="Complete a task")
    done.add_argument("id", type=int)

    sub.add_parser("preview", help="Preview today's receipt")

    printer = sub.add_parser("print", help="Print today's receipt on Windows")
    printer.add_argument("--printer-name")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    conn = connect(args.db)

    if args.command == "add":
        task = add_task(
            conn,
            args.text,
            due_at=args.due,
            created_by=args.by,
            source=args.source,
            priority_override="must_do" if args.must_do else None,
        )
        print(f"Added #{task.id}: {task.text}")
        return

    if args.command == "done":
        task = complete_task(conn, args.id)
        print(f"Done #{task.id}: {task.text}")
        return

    tasks = list_open_tasks(conn)
    if args.command == "list":
        for task in tasks:
            print(f"#{task.id} {task.text}")
        return

    receipt = render_daily_text(tasks)
    if args.command == "preview":
        print(receipt)
        return

    if args.command == "print":
        print_raw_windows(escpos_receipt(receipt), args.printer_name)
        print(f"Printed {len(tasks)} open tasks")

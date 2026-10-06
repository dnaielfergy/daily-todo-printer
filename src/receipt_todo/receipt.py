from __future__ import annotations

from datetime import datetime
from .db import Task

ESC = b"\x1b"
GS = b"\x1d"
PRIORITY_MARKER = {"high": "H", "medium": "M", "low": "L"}


def render_daily_text(
    tasks: list[Task],
    *,
    now: datetime | None = None,
    open_count: int | None = None,
    planned_count: int | None = None,
) -> str:
    now = now or datetime.now()
    lines = [
        "=" * 42,
        now.strftime("%A, %B %d").upper(),
        "=" * 42,
        "",
        "TODAY",
        "",
    ]
    if not tasks:
        lines.append("No open tasks.")
    else:
        for task in tasks:
            marker = PRIORITY_MARKER[task.priority]
            due = f"  due {task.due_at}" if task.due_at else ""
            lines.append(f"[{marker}] {task.id:03d}  {task.text}{due}")
            if task.source == "telegram":
                lines.append(f"      from {task.created_by}")

    lines.extend(["", "-" * 42])
    if open_count is not None and planned_count is not None:
        lines.append(f"{planned_count} planned / {open_count} open")
    else:
        lines.append(f"{len(tasks)} open")
    lines.extend([
        "",
        "Telegram: /done 42",
        "Send anything else to add it.",
        "=" * 42,
    ])
    return "\n".join(lines)


def render_incoming_task(task: Task) -> str:
    return "\n".join([
        "-" * 42,
        "NEW TODO",
        "",
        f"#{task.id:03d} [{PRIORITY_MARKER[task.priority]}]",
        task.text,
        "",
        f"from {task.created_by}",
        "-" * 42,
    ])


def escpos_receipt(text: str) -> bytes:
    safe = text.encode("cp437", errors="replace")
    return ESC + b"@" + safe + b"\n\n\n" + GS + b"V\x00"

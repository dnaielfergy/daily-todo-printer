from __future__ import annotations

from datetime import datetime
from .db import Task

ESC = b"\x1b"
GS = b"\x1d"


def render_daily_text(tasks: list[Task], *, now: datetime | None = None) -> str:
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
            marker = "!" if task.priority_override == "must_do" else " "
            due = f"  due {task.due_at}" if task.due_at else ""
            lines.append(f"[{marker}] {task.id:03d}  {task.text}{due}")
            if task.created_by != "daniel":
                lines.append(f"      from {task.created_by}")
    lines.extend([
        "",
        "-" * 42,
        f"{len(tasks)} open",
        "",
        "Text: done 42",
        "Text anything else to add it.",
        "=" * 42,
    ])
    return "\n".join(lines)


def escpos_receipt(text: str) -> bytes:
    # Initialize, print UTF-8-ish ASCII-safe content, feed, then full cut.
    # The initial product receipt intentionally stays plain-text; typography
    # can be designed after the physical printer spike proves dimensions.
    safe = text.encode("cp437", errors="replace")
    return ESC + b"@" + safe + b"\n\n\n" + GS + b"V\x00"

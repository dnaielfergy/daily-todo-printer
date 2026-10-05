from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("data/tasks.db")


@dataclass(frozen=True)
class Task:
    id: int
    text: str
    status: str
    created_at: str
    completed_at: str | None
    due_at: str | None
    created_by: str
    source: str
    priority_override: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL CHECK(length(trim(text)) > 0),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'cancelled')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            due_at TEXT,
            created_by TEXT NOT NULL DEFAULT 'local',
            source TEXT NOT NULL DEFAULT 'cli',
            priority_override TEXT CHECK(priority_override IN ('must_do', 'normal') OR priority_override IS NULL)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_updates (
            update_id INTEGER PRIMARY KEY,
            task_id INTEGER,
            response_text TEXT,
            processed_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def add_task(
    conn: sqlite3.Connection,
    text: str,
    *,
    due_at: str | None = None,
    created_by: str = "local",
    source: str = "cli",
    priority_override: str | None = None,
) -> Task:
    clean = text.strip()
    if not clean:
        raise ValueError("Task text cannot be empty")
    cursor = conn.execute(
        """
        INSERT INTO tasks (text, created_at, due_at, created_by, source, priority_override)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (clean, _now(), due_at, created_by, source, priority_override),
    )
    conn.commit()
    return get_task(conn, int(cursor.lastrowid))


def add_telegram_task_once(
    conn: sqlite3.Connection,
    update_id: int,
    text: str,
    *,
    created_by: str,
    response_factory,
) -> tuple[Task, str, bool]:
    existing = conn.execute(
        "SELECT task_id, response_text FROM telegram_updates WHERE update_id = ?",
        (update_id,),
    ).fetchone()
    if existing is not None and existing["task_id"] is not None:
        return get_task(conn, int(existing["task_id"])), existing["response_text"] or "", False

    clean = text.strip()
    if not clean:
        raise ValueError("Task text cannot be empty")

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (text, created_at, created_by, source)
            VALUES (?, ?, ?, 'telegram')
            """,
            (clean, _now(), created_by),
        )
        task_id = int(cursor.lastrowid)
        task = get_task(conn, task_id)
        response = response_factory(task)
        conn.execute(
            """
            INSERT INTO telegram_updates (update_id, task_id, response_text, processed_at)
            VALUES (?, ?, ?, ?)
            """,
            (update_id, task_id, response, _now()),
        )
    return task, response, True


def get_task(conn: sqlite3.Connection, task_id: int) -> Task:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"Task #{task_id} does not exist")
    return Task(**dict(row))


def list_open_tasks(conn: sqlite3.Connection) -> list[Task]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'open'
        ORDER BY
            CASE priority_override WHEN 'must_do' THEN 0 ELSE 1 END,
            CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,
            due_at,
            id
        """
    ).fetchall()
    return [Task(**dict(row)) for row in rows]


def complete_task(conn: sqlite3.Connection, task_id: int) -> Task:
    task = get_task(conn, task_id)
    if task.status == "done":
        return task
    cursor = conn.execute(
        "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
        (_now(), task_id),
    )
    if cursor.rowcount == 0:
        raise ValueError(f"Task #{task_id} is {task.status}, not open")
    conn.commit()
    return get_task(conn, task_id)


def cancel_task(conn: sqlite3.Connection, task_id: int) -> Task:
    task = get_task(conn, task_id)
    if task.status == "cancelled":
        return task
    cursor = conn.execute(
        "UPDATE tasks SET status = 'cancelled' WHERE id = ? AND status = 'open'",
        (task_id,),
    )
    if cursor.rowcount == 0:
        raise ValueError(f"Task #{task_id} is {task.status}, not open")
    conn.commit()
    return get_task(conn, task_id)


def get_telegram_response(conn: sqlite3.Connection, update_id: int) -> str | None:
    row = conn.execute(
        "SELECT response_text FROM telegram_updates WHERE update_id = ?",
        (update_id,),
    ).fetchone()
    return None if row is None else (row["response_text"] or "")


def record_telegram_update(conn: sqlite3.Connection, update_id: int, response_text: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO telegram_updates (update_id, response_text, processed_at)
        VALUES (?, ?, ?)
        """,
        (update_id, response_text, _now()),
    )
    conn.commit()


def get_state_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return default if row is None else int(row["value"])


def set_state_int(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute(
        """
        INSERT INTO app_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()

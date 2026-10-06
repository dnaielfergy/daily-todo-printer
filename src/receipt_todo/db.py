from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("data/tasks.db")
PRIORITIES = {"low", "medium", "high"}


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
    priority: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _create_tasks_table(conn: sqlite3.Connection, name: str = "tasks") -> None:
    conn.execute(
        f"""
        CREATE TABLE {name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL CHECK(length(trim(text)) > 0),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'cancelled')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            due_at TEXT,
            created_by TEXT NOT NULL DEFAULT 'local',
            source TEXT NOT NULL DEFAULT 'cli',
            priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high'))
        )
        """
    )


def _migrate_tasks(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
    ).fetchone()
    if exists is None:
        _create_tasks_table(conn)
        conn.commit()
        return

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "priority" in columns and "priority_override" not in columns:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DROP TABLE IF EXISTS tasks_new")
        conn.execute("BEGIN")
        _create_tasks_table(conn, "tasks_new")
        if "priority" in columns:
            priority_expr = (
                "CASE WHEN priority IN ('low', 'medium', 'high') "
                "THEN priority ELSE 'medium' END"
            )
        else:
            priority_expr = (
                "CASE WHEN priority_override = 'must_do' "
                "THEN 'high' ELSE 'medium' END"
            )
        conn.execute(
            f"""
            INSERT INTO tasks_new (
                id, text, status, created_at, completed_at, due_at,
                created_by, source, priority
            )
            SELECT
                id, text, status, created_at, completed_at, due_at,
                created_by, source, {priority_expr}
            FROM tasks
            """
        )
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _migrate_tasks(conn)
    conn.execute("PRAGMA foreign_keys = ON")
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            printed_at TEXT,
            max_items INTEGER NOT NULL CHECK(max_items > 0)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_plan_items (
            plan_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            deterministic_rank INTEGER NOT NULL,
            ai_rank INTEGER,
            final_rank INTEGER NOT NULL,
            included INTEGER NOT NULL CHECK(included IN (0, 1)),
            PRIMARY KEY(plan_id, task_id),
            FOREIGN KEY(plan_id) REFERENCES daily_plans(id) ON DELETE CASCADE,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """
    )
    conn.commit()
    return conn


def validate_priority(priority: str) -> str:
    clean = priority.strip().lower()
    if clean not in PRIORITIES:
        raise ValueError("Priority must be low, medium, or high")
    return clean


def validate_due_date(due_at: str | None) -> str | None:
    if due_at is None:
        return None
    clean = due_at.strip()
    if not clean:
        return None
    try:
        date.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("Due date must be YYYY-MM-DD or clear") from exc
    return clean


def add_task(
    conn: sqlite3.Connection,
    text: str,
    *,
    due_at: str | None = None,
    created_by: str = "local",
    source: str = "cli",
    priority: str = "medium",
) -> Task:
    clean = text.strip()
    if not clean:
        raise ValueError("Task text cannot be empty")
    due_at = validate_due_date(due_at)
    priority = validate_priority(priority)
    cursor = conn.execute(
        """
        INSERT INTO tasks (
            text, created_at, due_at, created_by, source, priority
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (clean, _now(), due_at, created_by, source, priority),
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
            INSERT INTO tasks (text, created_at, created_by, source, priority)
            VALUES (?, ?, ?, 'telegram', 'medium')
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


def get_tasks(conn: sqlite3.Connection, task_ids: list[int]) -> dict[int, Task]:
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE id IN ({placeholders})",
        task_ids,
    ).fetchall()
    return {int(row["id"]): Task(**dict(row)) for row in rows}


def list_open_tasks(conn: sqlite3.Connection) -> list[Task]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'open' ORDER BY id"
    ).fetchall()
    return [Task(**dict(row)) for row in rows]


def set_task_priority(conn: sqlite3.Connection, task_id: int, priority: str) -> Task:
    get_task(conn, task_id)
    priority = validate_priority(priority)
    conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id))
    conn.commit()
    return get_task(conn, task_id)


def set_task_due_date(conn: sqlite3.Connection, task_id: int, due_at: str | None) -> Task:
    get_task(conn, task_id)
    due_at = validate_due_date(due_at)
    conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (due_at, task_id))
    conn.commit()
    return get_task(conn, task_id)


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

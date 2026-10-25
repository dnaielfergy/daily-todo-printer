"""Source-independent recipe print jobs; no recipe library or external sync lives here."""

from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

from .printer import print_raw_windows
from .receipt import escpos_receipt

WIDTH = 42
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: object, label: str, max_length: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{label} must be nonempty text (max {max_length} chars)")
    return value.strip()


def validate_recipe_job(payload: object) -> dict:
    """Validate the v1 transport contract without taking ownership of source recipes."""
    if not isinstance(payload, dict):
        raise ValueError("Recipe job must be a JSON object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported recipe job schema_version (expected 1)")
    job_id = _text(payload.get("job_id"), "job_id", 120)
    if JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("job_id must use letters, digits, dots, underscores, colons or hyphens")
    meal_date = _text(payload.get("date"), "date", 10)
    try:
        if date.fromisoformat(meal_date).isoformat() != meal_date:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    if payload.get("meal") not in ("lunch", "dinner"):
        raise ValueError("meal must be lunch or dinner")

    recipe = payload.get("recipe")
    if not isinstance(recipe, dict):
        raise ValueError("recipe must be an object")
    _text(recipe.get("title"), "recipe.title", 160)
    servings = recipe.get("servings")
    if servings is not None and (type(servings) is not int or not 1 <= servings <= 100):
        raise ValueError("recipe.servings must be an integer between 1 and 100")
    for field in ("prep_minutes", "cook_minutes"):
        value = recipe.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= 1440):
            raise ValueError(f"recipe.{field} must be an integer between 0 and 1440")

    ingredients = recipe.get("ingredients")
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 80:
        raise ValueError("recipe.ingredients must contain 1-80 items")
    for index, ingredient in enumerate(ingredients):
        if not isinstance(ingredient, dict):
            raise ValueError(f"recipe.ingredients[{index}] must be an object")
        _text(ingredient.get("name"), f"ingredient {index} name", 200)
        if "amount" in ingredient and ingredient["amount"] is not None:
            _text(ingredient["amount"], f"ingredient {index} amount", 100)

    steps = recipe.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 60:
        raise ValueError("recipe.steps must contain 1-60 instructions")
    for index, step in enumerate(steps):
        _text(step, f"step {index + 1}", 2000)

    if "source" in payload and payload["source"] is not None:
        source = payload["source"]
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        _text(source.get("type"), "source.type", 40)
        _text(source.get("reference"), "source.reference", 500)
    return payload


def load_recipe_job(path: Path) -> dict:
    return validate_recipe_job(json.loads(path.read_text(encoding="utf-8-sig")))


def ensure_jobs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recipe_print_jobs (
            job_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued', 'printing', 'printed', 'needs_review')),
            created_at TEXT NOT NULL,
            printed_at TEXT,
            print_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()


def submit_recipe_job(conn: sqlite3.Connection, payload: dict) -> bool:
    """Return False for an identical replay; reject changed content under the same ID."""
    validate_recipe_job(payload)
    ensure_jobs_table(conn)
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with conn:
        existing = conn.execute(
            "SELECT payload_json FROM recipe_print_jobs WHERE job_id = ?",
            (payload["job_id"],),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != serialized:
                raise ValueError("job_id already exists with different content; use a new job_id")
            return False
        conn.execute(
            "INSERT INTO recipe_print_jobs (job_id, payload_json, created_at) VALUES (?, ?, ?)",
            (payload["job_id"], serialized, _now()),
        )
    return True


def get_recipe_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    ensure_jobs_table(conn)
    row = conn.execute(
        "SELECT * FROM recipe_print_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Recipe job not found: {job_id}")
    return row


def list_recipe_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_jobs_table(conn)
    return conn.execute(
        "SELECT * FROM recipe_print_jobs ORDER BY created_at DESC, job_id DESC"
    ).fetchall()


def _wrap(text: str, *, prefix: str = "", continuation: str = "") -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=prefix,
        subsequent_indent=continuation,
        break_long_words=True,
        break_on_hyphens=False,
    )


def render_recipe_job(payload: dict) -> str:
    validate_recipe_job(payload)
    recipe = payload["recipe"]
    lines = [
        "=" * WIDTH,
        f'{payload["meal"].upper()} / {payload["date"]}',
        "=" * WIDTH,
        "",
    ]
    lines.extend(_wrap(recipe["title"].strip()))
    details = []
    if recipe.get("servings") is not None:
        details.append(f'Serves {recipe["servings"]}')
    if recipe.get("prep_minutes") is not None:
        details.append(f'Prep {recipe["prep_minutes"]}m')
    if recipe.get("cook_minutes") is not None:
        details.append(f'Cook {recipe["cook_minutes"]}m')
    if details:
        lines.extend(["", " | ".join(details)])
    lines.extend(["", "INGREDIENTS", ""])
    for item in recipe["ingredients"]:
        amount = item.get("amount")
        name = item["name"].strip()
        lines.extend(_wrap(f'{str(amount).strip() + " " if amount else ""}{name}', prefix="- ", continuation="  "))
    lines.extend(["", "METHOD", ""])
    for index, step in enumerate(recipe["steps"], start=1):
        label = f"{index}. "
        lines.extend(_wrap(step.strip(), prefix=label, continuation=" " * len(label)))
        lines.append("")
    lines.append("-" * WIDTH)
    lines.extend(_wrap(f'Job: {payload["job_id"]}'))
    lines.append("=" * WIDTH)
    return "\n".join(lines)


def print_recipe_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    printer_name: str | None = None,
    force: bool = False,
) -> bool:
    """Claim before printing; an uncertain spooler failure requires explicit requeue."""
    row = get_recipe_job(conn, job_id)
    if row["status"] == "printed" and not force:
        return False
    allowed = ("queued", "printed") if force else ("queued",)
    if row["status"] not in allowed:
        raise ValueError(f"Job is {row['status']}; inspect printer and explicitly requeue if needed")
    with conn:
        claimed = conn.execute(
            f"UPDATE recipe_print_jobs SET status = 'printing' WHERE job_id = ? "
            f"AND status IN ({','.join('?' for _ in allowed)})",
            (job_id, *allowed),
        )
        if claimed.rowcount != 1:
            raise ValueError("Recipe job already claimed by another printer process")
    try:
        payload = json.loads(row["payload_json"])
        print_raw_windows(escpos_receipt(render_recipe_job(payload)), printer_name)
    except Exception:
        with conn:
            conn.execute(
                "UPDATE recipe_print_jobs SET status = 'needs_review' WHERE job_id = ?",
                (job_id,),
            )
        raise
    with conn:
        conn.execute(
            """
            UPDATE recipe_print_jobs
            SET status = 'printed', printed_at = ?, print_count = print_count + 1
            WHERE job_id = ?
            """,
            (_now(), job_id),
        )
    return True


def requeue_recipe_job(conn: sqlite3.Connection, job_id: str) -> None:
    row = get_recipe_job(conn, job_id)
    if row["status"] not in ("printing", "needs_review"):
        raise ValueError("Only interrupted/uncertain jobs can be requeued")
    with conn:
        conn.execute(
            "UPDATE recipe_print_jobs SET status = 'queued' WHERE job_id = ?",
            (job_id,),
        )

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

    recipes = payload.get("recipes")
    if not isinstance(recipes, list) or not 1 <= len(recipes) <= 12:
        raise ValueError("recipes must contain 1-12 recipe objects")
    for recipe in recipes:
        if not isinstance(recipe, dict):
            raise ValueError("each recipe must be an object")
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

    if "recipe" in payload:
        raise ValueError("Use recipes array, not legacy recipe field")
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
            print_count INTEGER NOT NULL DEFAULT 0,
            inbox_week TEXT,
            inbox_active INTEGER NOT NULL DEFAULT 1 CHECK(inbox_active IN (0, 1)),
            inbox_inactive_at TEXT,
            job_kind TEXT NOT NULL DEFAULT 'recipe'
                CHECK(job_kind IN ('recipe', 'grocery'))
        )
        """
    )
    # Older installs have immutable jobs but no inbox provenance. The importer
    # handles any ambiguous legacy publications conservatively.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipe_print_jobs)")}
    if "inbox_week" not in columns:
        conn.execute("ALTER TABLE recipe_print_jobs ADD COLUMN inbox_week TEXT")
    if "inbox_active" not in columns:
        conn.execute(
            "ALTER TABLE recipe_print_jobs ADD COLUMN inbox_active INTEGER NOT NULL DEFAULT 1"
        )
    if "inbox_inactive_at" not in columns:
        conn.execute("ALTER TABLE recipe_print_jobs ADD COLUMN inbox_inactive_at TEXT")
    if "job_kind" not in columns:
        conn.execute(
            "ALTER TABLE recipe_print_jobs ADD COLUMN job_kind TEXT NOT NULL DEFAULT 'recipe'"
        )
    if not conn.in_transaction:
        conn.commit()


def submit_print_job(conn: sqlite3.Connection, payload: dict, *, kind: str) -> bool:
    """Idempotent local submit; IDs are unique across all print job types."""
    if kind not in ("recipe", "grocery"):
        raise ValueError("Unsupported print job type")
    ensure_jobs_table(conn)
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with conn:
        existing = conn.execute(
            "SELECT payload_json, inbox_active, job_kind FROM recipe_print_jobs WHERE job_id = ?",
            (payload["job_id"],),
        ).fetchone()
        if existing is not None:
            if not existing["inbox_active"]:
                raise ValueError("job_id was withdrawn; publish a new approved job_id instead")
            if existing["job_kind"] != kind or existing["payload_json"] != serialized:
                raise ValueError("job_id already exists with different content or type; use a new job_id")
            return False
        conn.execute(
            "INSERT INTO recipe_print_jobs (job_id, payload_json, created_at, job_kind) "
            "VALUES (?, ?, ?, ?)",
            (payload["job_id"], serialized, _now(), kind),
        )
    return True


def submit_recipe_job(conn: sqlite3.Connection, payload: dict) -> bool:
    validate_recipe_job(payload)
    return submit_print_job(conn, payload, kind="recipe")


def get_print_job(conn: sqlite3.Connection, job_id: str, *, kind: str) -> sqlite3.Row:
    ensure_jobs_table(conn)
    row = conn.execute(
        "SELECT * FROM recipe_print_jobs WHERE job_id = ? AND job_kind = ?",
        (job_id, kind),
    ).fetchone()
    if row is None:
        raise KeyError(f"{kind.capitalize()} job not found: {job_id}")
    return row


def get_recipe_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    return get_print_job(conn, job_id, kind="recipe")


def list_print_jobs(conn: sqlite3.Connection, *, kind: str) -> list[sqlite3.Row]:
    ensure_jobs_table(conn)
    return conn.execute(
        "SELECT * FROM recipe_print_jobs WHERE job_kind = ? "
        "ORDER BY created_at DESC, job_id DESC",
        (kind,),
    ).fetchall()


def list_recipe_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list_print_jobs(conn, kind="recipe")


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
    lines = [
        "=" * WIDTH,
        f'{payload["meal"].upper()} / {payload["date"]}',
        "=" * WIDTH,
        "",
    ]
    for recipe_index, recipe in enumerate(payload["recipes"], start=1):
        if recipe_index > 1:
            lines.extend(["", "-" * WIDTH, ""])
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


def print_print_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    kind: str,
    renderer,
    printer_name: str | None = None,
    force: bool = False,
) -> bool:
    """Durably claim a job before a locked spooler handoff.

    First commit status=printing, so process termination cannot silently put
    an uncertain print back in the queue. Then recheck approved-plan state
    under a SQLite write lock while handing bytes to the Windows spooler.
    The publisher cannot retire the job between that recheck and handoff.
    """
    ensure_jobs_table(conn)
    with conn:
        row = conn.execute(
            "SELECT * FROM recipe_print_jobs WHERE job_id = ? AND job_kind = ?",
            (job_id, kind),
        ).fetchone()
        if row is None:
            raise KeyError(f"{kind.capitalize()} job not found: {job_id}")
        if not row["inbox_active"]:
            raise ValueError("Recipe job was withdrawn from the approved plan; cannot print or reprint")
        if row["status"] == "printed" and not force:
            return False
        allowed = ("queued", "printed") if force else ("queued",)
        if row["status"] not in allowed:
            raise ValueError(
                f"Job is {row['status']}; inspect printer and explicitly requeue if needed"
            )
        claimed = conn.execute(
            f"UPDATE recipe_print_jobs SET status = 'printing' WHERE job_id = ? "
            f"AND inbox_active = 1 AND job_kind = ? AND status IN ({','.join('?' for _ in allowed)})",
            (job_id, kind, *allowed),
        )
        if claimed.rowcount != 1:
            raise ValueError("Recipe job already claimed or withdrawn")
    # A revision might commit between claim and this second transaction.
    # Recheck active status before starting any physical print.
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute(
            "SELECT status, inbox_active FROM recipe_print_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if current is None or current["status"] != "printing":
            raise ValueError("Recipe job claim changed before spooler handoff")
        if not current["inbox_active"]:
            # No bytes were sent; preserve prior history instead of reporting an
            # attempted print. The withdrawal has already become authoritative.
            conn.execute(
                "UPDATE recipe_print_jobs SET status = ? WHERE job_id = ? AND status = 'printing'",
                (row["status"], job_id),
            )
            conn.commit()
            raise ValueError("Recipe job was withdrawn before spooler handoff")
        try:
            payload = json.loads(row["payload_json"])
            print_raw_windows(escpos_receipt(renderer(payload)), printer_name)
        except BaseException:
            conn.execute(
                "UPDATE recipe_print_jobs SET status = 'needs_review' WHERE job_id = ?",
                (job_id,),
            )
            conn.commit()
            raise
        conn.execute(
            """
            UPDATE recipe_print_jobs
            SET status = 'printed', printed_at = ?, print_count = print_count + 1
            WHERE job_id = ?
            """,
            (_now(), job_id),
        )
        conn.commit()
        return True
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def print_recipe_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    printer_name: str | None = None,
    force: bool = False,
) -> bool:
    return print_print_job(
        conn, job_id, kind="recipe", renderer=render_recipe_job,
        printer_name=printer_name, force=force,
    )


def requeue_print_job(conn: sqlite3.Connection, job_id: str, *, kind: str) -> None:
    row = get_print_job(conn, job_id, kind=kind)
    if not row["inbox_active"]:
        raise ValueError("Withdrawn print jobs cannot be requeued")
    if row["status"] not in ("printing", "needs_review"):
        raise ValueError("Only interrupted/uncertain jobs can be requeued")
    with conn:
        updated = conn.execute(
            "UPDATE recipe_print_jobs SET status = 'queued' "
            "WHERE job_id = ? AND job_kind = ? AND inbox_active = 1 "
            "AND status IN ('printing', 'needs_review')",
            (job_id, kind),
        )
        if updated.rowcount != 1:
            raise ValueError("Job already changed or withdrawn")


def requeue_recipe_job(conn: sqlite3.Connection, job_id: str) -> None:
    requeue_print_job(conn, job_id, kind="recipe")

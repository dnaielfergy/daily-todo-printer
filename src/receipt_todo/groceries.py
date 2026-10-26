"""Versioned grocery print jobs. Quantities are approved upstream, never calculated here."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .recipes import (
    JOB_ID_PATTERN, WIDTH, _wrap, get_print_job, list_print_jobs,
    print_print_job, requeue_print_job, submit_print_job,
)

MAX_GROCERY_FILE_BYTES = 131_072
MAX_GROCERY_ITEMS = 120


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be nonempty text (max {limit} chars)")
    cleaned = value.strip()
    # Raw ESC/POS commands and unprintable/control characters must never enter
    # the spooler. The existing ESC/POS text adapter uses CP437.
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in cleaned):
        raise ValueError(f"{label} contains control characters")
    try:
        encoded = cleaned.encode("cp437")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} contains characters unsupported by the printer") from exc
    if any(byte < 32 or byte == 127 for byte in encoded):
        raise ValueError(f"{label} contains printer control bytes")
    return cleaned


def _allowed(value: dict, allowed: set[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(extra))}")


def validate_grocery_job(payload: object) -> dict:
    """Validate the complete immutable grocery snapshot without modifying it."""
    if not isinstance(payload, dict):
        raise ValueError("Grocery job must be an object")
    _allowed(payload, {
        "schema_version", "job_id", "week_start", "plan_revision",
        "title", "groups", "items", "source",
    }, "grocery job")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported grocery schema_version (expected 1)")
    job_id = _text(payload.get("job_id"), "job_id", 120)
    if JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("Invalid grocery job_id")
    week = _text(payload.get("week_start"), "week_start", 10)
    try:
        parsed = date.fromisoformat(week)
        if parsed.isoformat() != week or parsed.weekday() != 0:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("week_start must be a valid Monday in YYYY-MM-DD") from exc

    revision = payload.get("plan_revision")
    if revision is not None and (type(revision) is not int or not 1 <= revision <= 1_000_000_000):
        raise ValueError("plan_revision must be a positive integer")
    if payload.get("title") is not None:
        _text(payload["title"], "title", 120)

    has_groups = "groups" in payload
    has_items = "items" in payload
    if has_groups == has_items:
        raise ValueError("Provide either groups or items (not both)")
    if has_groups:
        groups = payload["groups"]
        if not isinstance(groups, list) or not 1 <= len(groups) <= 12:
            raise ValueError("groups must contain 1-12 categories")
    else:
        groups = [{"name": "Items", "items": payload["items"]}]

    item_count = 0
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"group {group_index} must be an object")
        _allowed(group, {"name", "items"}, f"group {group_index}")
        _text(group.get("name"), f"group {group_index} name", 60)
        items = group.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_GROCERY_ITEMS:
            raise ValueError(f"group {group_index} items must be a nonempty list")
        item_count += len(items)
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"item {group_index}/{item_index} must be an object")
            _allowed(item, {"name", "amount", "note"}, f"item {group_index}/{item_index}")
            _text(item.get("name"), f"item {group_index}/{item_index} name", 160)
            if item.get("amount") is not None:
                _text(item["amount"], f"item {group_index}/{item_index} amount", 80)
            if item.get("note") is not None:
                _text(item["note"], f"item {group_index}/{item_index} note", 160)
    if not 1 <= item_count <= MAX_GROCERY_ITEMS:
        raise ValueError(f"Grocery job must contain 1-{MAX_GROCERY_ITEMS} total items")

    if payload.get("source") is not None:
        source = payload["source"]
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        _allowed(source, {"type", "reference"}, "source")
        _text(source.get("type"), "source.type", 40)
        _text(source.get("reference"), "source.reference", 500)
    return payload


def load_grocery_job(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) > MAX_GROCERY_FILE_BYTES:
        raise ValueError("Grocery file exceeds size limit")

    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate grocery JSON key: {key}")
            result[key] = value
        return result

    return validate_grocery_job(
        json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
    )


def render_grocery_job(payload: dict) -> str:
    validate_grocery_job(payload)
    lines = [
        "=" * WIDTH,
        f'GROCERIES / WEEK {payload["week_start"]}',
        "=" * WIDTH,
    ]
    if payload.get("title") is not None:
        lines.append("")
        lines.extend(_wrap(payload["title"].strip()))

    groups = payload.get("groups")
    if groups is None:
        groups = [{"name": "Items", "items": payload["items"]}]
    for group in groups:
        lines.extend(["", *(_wrap(group["name"].upper())), "-" * WIDTH])
        for item in group["items"]:
            amount = item.get("amount")
            # Only show the upstream-approved amount, verbatim. Never infer
            # numbers, merge duplicate entries, or convert units locally.
            label = (amount.strip() + " " if amount else "") + item["name"].strip()
            lines.extend(_wrap(label, prefix="[ ] ", continuation="    "))
            if item.get("note"):
                lines.extend(_wrap(item["note"].strip(), prefix="    note: ", continuation="          "))
    lines.extend(["", "-" * WIDTH])
    lines.extend(_wrap(f'Job: {payload["job_id"]}'))
    lines.append("=" * WIDTH)
    return "\n".join(lines)


def submit_grocery_job(conn, payload: dict) -> bool:
    validate_grocery_job(payload)
    return submit_print_job(conn, payload, kind="grocery")


def get_grocery_job(conn, job_id: str):
    return get_print_job(conn, job_id, kind="grocery")


def list_grocery_jobs(conn):
    return list_print_jobs(conn, kind="grocery")


def print_grocery_job(conn, job_id: str, *, printer_name: str | None = None, force: bool = False) -> bool:
    return print_print_job(
        conn, job_id, kind="grocery", renderer=render_grocery_job,
        printer_name=printer_name, force=force,
    )


def requeue_grocery_job(conn, job_id: str) -> None:
    requeue_print_job(conn, job_id, kind="grocery")

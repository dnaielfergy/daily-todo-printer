"""Grocery receipts, approvals and revision protection across the private print inbox."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from receipt_todo.cli import main
from receipt_todo.db import add_task, connect, list_open_tasks
from receipt_todo.groceries import (
    get_grocery_job, list_grocery_jobs, load_grocery_job, print_grocery_job,
    requeue_grocery_job, render_grocery_job, submit_grocery_job,
    validate_grocery_job,
)
from receipt_todo.inbox import InboxError, import_snapshot, load_snapshot
from receipt_todo.recipes import (
    get_recipe_job, list_recipe_jobs, print_recipe_job, submit_recipe_job,
)

WEEK = "2026-10-26"
MANIFEST = f"weeks/{WEEK}/manifest.json"
RECIPE = f"weeks/{WEEK}/jobs/2026-10-26-lunch.json"
GROCERY_V1 = f"weeks/{WEEK}/groceries/shopping-v1.json"
GROCERY_V2 = f"weeks/{WEEK}/groceries/shopping-v2.json"


def grocery(job_id="shopping-v1", revision=1, *, servings_note=None):
    return {
        "schema_version": 1,
        "job_id": job_id,
        "week_start": WEEK,
        "plan_revision": revision,
        "title": "Weekly shopping",
        "groups": [
            {"name": "Produce", "items": [
                {"name": "Tomatoes", "amount": "4", "note": "Ripe"},
                {"name": "Extra long fresh seasonal vegetables from the market", "amount": "2 bunches"},
            ]},
            {"name": "Pantry", "items": [
                {"name": "Rice", "amount": "1 1/2 cups" if servings_note is None else servings_note},
                {"name": "Salt"},
            ]},
        ],
        "source": {"type": "test", "reference": "issue-18"},
    }


def recipe():
    return {
        "schema_version": 1,
        "job_id": "monday-lunch",
        "date": "2026-10-26",
        "meal": "lunch",
        "recipe": {"title": "Rice bowls", "ingredients": [{"name": "Rice", "amount": "1 cup"}],
                   "steps": ["Cook rice."]},
    }


def files_for(revision=1, grocery_payload=None, include_recipe=True):
    files = {
        "inbox.json": {"schema_version": 1, "weeks": [MANIFEST]},
        MANIFEST: {
            "schema_version": 1, "week_start": WEEK, "revision": revision,
            "jobs": [RECIPE] if include_recipe else [],
        },
    }
    if include_recipe:
        files[RECIPE] = recipe()
    if grocery_payload is not None:
        grocery_path = GROCERY_V1 if revision == 1 else GROCERY_V2
        files[MANIFEST]["grocery"] = grocery_path
        files[grocery_path] = grocery_payload
    return files


class FakeClient:
    def __init__(self, files):
        self.files = files
        self.requests = []

    def commit_sha(self):
        return "a" * 40

    def read_file(self, sha, path):
        self.requests.append((sha, path))
        return self.files[path]


def snapshot(files):
    return load_snapshot(FakeClient(files))


class GroceryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.temp.name) / "test.db"
        self.conn = connect(self.dbpath)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_render_42_columns_and_preserve_amounts(self):
        result = render_grocery_job(grocery())
        self.assertIn("GROCERIES / WEEK 2026-10-26", result)
        self.assertIn("PRODUCE", result)
        self.assertIn("PANTRY", result)
        self.assertIn("[ ] 4 Tomatoes", result)
        self.assertIn("1 1/2 cups Rice", result)
        self.assertIn("note: Ripe", result)
        self.assertIn("Job: shopping-v1", result)
        self.assertTrue(all(len(line) <= 42 for line in result.splitlines()), result)
        self.assertEqual(result.count("[ ] "), 4)

    def test_flat_items_supported_without_groups(self):
        flat = grocery()
        flat["items"] = flat.pop("groups")[0]["items"]
        self.assertIn("ITEMS", render_grocery_job(flat))
        self.assertEqual(len(flat["items"]), 2)

    def test_validation_rejects_unsafe_or_oversized_fields(self):
        cases = [
            ("date not Monday", lambda g: g.update(week_start="2026-10-27")),
            ("invalid date", lambda g: g.update(week_start="2026-10-99")),
            ("wrong schema", lambda g: g.update(schema_version=True)),
            ("wrong revision", lambda g: g.update(plan_revision=False)),
            ("no groups", lambda g: g.update(groups=[])),
            ("both flat and grouped", lambda g: g.update(items=[{"name": "Rice"}])),
            ("control char", lambda g: g["groups"][0]["items"][0].update(name="Rice\x1b[2J")),
            ("line break", lambda g: g["groups"][0]["items"][0].update(amount="4\n5")),
            ("unsupported charset", lambda g: g["groups"][0]["items"][0].update(name="Emoji 🧄")),
            ("injected field", lambda g: g.update(print_now=True)),
            ("too many items", lambda g: g["groups"][0].update(items=[{"name": "A"}] * 121)),
            ("too many total items", lambda g: g.update(groups=[
                {"name": "A", "items": [{"name": "Rice"}] * 70},
                {"name": "B", "items": [{"name": "Beans"}] * 70},
            ])),
            ("missing amount name", lambda g: g["groups"][0]["items"][0].update(name=" ")),
        ]
        for label, update in cases:
            candidate = copy.deepcopy(grocery())
            update(candidate)
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_grocery_job(candidate)
        oversized = Path(self.temp.name) / "oversized.json"
        oversized.write_bytes(b" " * 131073)
        with self.assertRaisesRegex(ValueError, "size"):
            load_grocery_job(oversized)
        duplicates = Path(self.temp.name) / "duplicate.json"
        duplicates.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_grocery_job(duplicates)

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_local_submit_idempotency_print_once_reprint_and_typed_access(self, printer):
        a = grocery()
        self.assertTrue(submit_grocery_job(self.conn, a))
        self.assertFalse(submit_grocery_job(self.conn, a))
        changed = grocery(servings_note="3 cups")
        with self.assertRaisesRegex(ValueError, "different"):
            submit_grocery_job(self.conn, changed)
        self.assertEqual(len(list_recipe_jobs(self.conn)), 0)
        self.assertEqual(len(list_grocery_jobs(self.conn)), 1)
        with self.assertRaises(KeyError):
            get_recipe_job(self.conn, a["job_id"])
        with self.assertRaises(KeyError):
            print_recipe_job(self.conn, a["job_id"])
        self.assertTrue(print_grocery_job(self.conn, a["job_id"]))
        self.assertFalse(print_grocery_job(self.conn, a["job_id"]))
        self.assertTrue(print_grocery_job(self.conn, a["job_id"], force=True))
        self.assertEqual(printer.call_count, 2)
        row = get_grocery_job(self.conn, a["job_id"])
        self.assertEqual((row["status"], row["print_count"]), ("printed", 2))

    def test_cannot_reuse_id_across_types(self):
        a = grocery("monday-lunch")
        self.assertTrue(submit_recipe_job(self.conn, recipe()))
        with self.assertRaisesRegex(ValueError, "different"):
            submit_grocery_job(self.conn, a)
        with self.assertRaises(KeyError):
            get_grocery_job(self.conn, "monday-lunch")

    @patch("receipt_todo.recipes.print_raw_windows", side_effect=RuntimeError("spooler"))
    def test_ambiguous_failure_requires_explicit_requeue(self, printer):
        a = grocery()
        submit_grocery_job(self.conn, a)
        with self.assertRaises(RuntimeError):
            print_grocery_job(self.conn, a["job_id"])
        self.assertEqual(get_grocery_job(self.conn, a["job_id"])["status"], "needs_review")
        with self.assertRaises(ValueError):
            print_grocery_job(self.conn, a["job_id"])
        requeue_grocery_job(self.conn, a["job_id"])
        self.assertEqual(get_grocery_job(self.conn, a["job_id"])["status"], "queued")

    def test_pinned_publication_import_and_idempotency(self):
        task = add_task(self.conn, "Wash dishes")
        files = files_for(grocery_payload=grocery())
        client = FakeClient(files)
        snap = load_snapshot(client)
        self.assertEqual(len(snap.weeks), 1)
        self.assertEqual(len(snap.weeks[0].jobs), 1)
        self.assertEqual(snap.weeks[0].grocery["job_id"], "shopping-v1")
        self.assertTrue(all(sha == "a" * 40 for sha, _ in client.requests))
        self.assertEqual(import_snapshot(self.conn, snap, preview=True), 2)
        self.assertEqual(import_snapshot(self.conn, snap), 2)
        self.assertEqual(import_snapshot(self.conn, snap), 0)
        self.assertEqual(get_grocery_job(self.conn, "shopping-v1")["inbox_active"], 1)
        self.assertEqual(len(list_recipe_jobs(self.conn)), 1)
        self.assertEqual([task.id], [t.id for t in list_open_tasks(self.conn)])

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_grocery_revision_withdrawal_and_history(self, printer):
        import_snapshot(self.conn, snapshot(files_for(grocery_payload=grocery())))
        a = get_grocery_job(self.conn, "shopping-v1")
        self.assertEqual(a["status"], "queued")
        self.assertTrue(print_grocery_job(self.conn, "shopping-v1"))
        second = files_for(revision=2, grocery_payload=grocery("shopping-v2", revision=2, servings_note="3 cups"))
        self.assertEqual(import_snapshot(self.conn, snapshot(second)), 1)
        old = get_grocery_job(self.conn, "shopping-v1")
        self.assertEqual((old["status"], old["inbox_active"], old["print_count"]), ("printed", 0, 1))
        self.assertEqual(get_grocery_job(self.conn, "shopping-v2")["inbox_active"], 1)
        for force in (False, True):
            with self.subTest(force=force), self.assertRaisesRegex(ValueError, "withdrawn"):
                print_grocery_job(self.conn, "shopping-v1", force=force)
        with self.assertRaisesRegex(ValueError, "requeued"):
            requeue_grocery_job(self.conn, "shopping-v1")
        with self.assertRaises(ValueError):
            submit_grocery_job(self.conn, grocery())
        self.assertEqual(import_snapshot(self.conn, snapshot(second)), 0)
        self.assertEqual(printer.call_count, 1)

    def test_plan_revision_requires_new_grocery_or_withdrawal(self):
        original = files_for(grocery_payload=grocery())
        import_snapshot(self.conn, snapshot(original))
        bad = files_for(revision=2, grocery_payload=grocery())
        with self.assertRaisesRegex(InboxError, "plan_revision"):
            snapshot(bad)
        self.assertEqual(get_grocery_job(self.conn, "shopping-v1")["inbox_active"], 1)
        without = files_for(revision=2)
        self.assertEqual(import_snapshot(self.conn, snapshot(without)), 0)
        self.assertEqual(get_grocery_job(self.conn, "shopping-v1")["inbox_active"], 0)
        with self.assertRaisesRegex(InboxError, "Immutable job_id"):
            import_snapshot(self.conn, snapshot(files_for(revision=3, grocery_payload=grocery("shopping-v1", 3))))

    def test_grocery_path_traversal_mismatch_and_bad_payload_fail_closed(self):
        cases = [
            "../../secrets.json",
            "weeks/2026-10-26/groceries/../secret.json",
            "weeks/2026-11-02/groceries/shopping.json",
            "weeks/2026-10-26/jobs/dinner.json",
            "weeks/2026-10-26/groceries/../../secret.json",
        ]
        for value in cases:
            files = files_for(grocery_payload=grocery())
            files[MANIFEST]["grocery"] = value
            with self.subTest(path=value), self.assertRaises(InboxError):
                snapshot(files)
        files = files_for(grocery_payload=grocery())
        files[GROCERY_V1]["week_start"] = "2026-11-02"
        with self.assertRaisesRegex(InboxError, "mismatch"):
            snapshot(files)
        files = files_for(grocery_payload=grocery())
        files[GROCERY_V1]["groups"][0]["items"] = []
        with self.assertRaises(InboxError):
            snapshot(files)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='recipe_print_jobs'"
        ).fetchone())

    def test_revision_conflict_rolls_back_grocery_and_recipe_together(self):
        import_snapshot(self.conn, snapshot(files_for(grocery_payload=grocery())))
        changed = files_for(revision=1, grocery_payload=grocery(servings_note="3 cups"))
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, snapshot(changed))
        self.assertEqual(get_grocery_job(self.conn, "shopping-v1")["inbox_active"], 1)
        self.assertEqual(len(list_grocery_jobs(self.conn)), 1)
        self.assertEqual(len(list_recipe_jobs(self.conn)), 1)

    def test_empty_manifest_withdraws_grocery_but_index_pruning_cannot(self):
        import_snapshot(self.conn, snapshot(files_for(grocery_payload=grocery())))
        omitted = {"inbox.json": {"schema_version": 1, "weeks": []}}
        with self.assertRaisesRegex(InboxError, "Cannot omit"):
            import_snapshot(self.conn, snapshot(omitted))
        empty = files_for(revision=2, include_recipe=False)
        self.assertEqual(import_snapshot(self.conn, snapshot(empty)), 0)
        self.assertEqual(get_grocery_job(self.conn, "shopping-v1")["inbox_active"], 0)
        self.assertEqual(get_recipe_job(self.conn, "monday-lunch")["inbox_active"], 0)
        self.assertEqual(import_snapshot(self.conn, snapshot(omitted)), 0)

    def test_existing_recipe_only_manifest_keeps_legacy_fingerprint(self):
        original = snapshot(files_for())
        import_snapshot(self.conn, original)
        self.assertEqual(import_snapshot(self.conn, original), 0)
        self.assertEqual(get_recipe_job(self.conn, "monday-lunch")["inbox_active"], 1)

    def test_cli_grocery_roundtrip_without_real_printer(self):
        path = Path(self.temp.name) / "grocery.json"
        path.write_text(json.dumps(grocery()), encoding="utf-8")
        operations = [
            ["grocery", "submit", str(path)],
            ["grocery", "list"],
            ["grocery", "preview", "shopping-v1"],
        ]
        for args in operations:
            with patch("receipt_todo.cli.connect", return_value=self.conn), patch(
                "sys.argv", ["receipt-todo", *args]
            ), redirect_stdout(io.StringIO()) as output:
                main()
                self.assertIn("shopping-v1", output.getvalue())

    def test_migration_default_recipe_type_for_prior_queue(self):
        self.conn.execute(
            """
            CREATE TABLE recipe_print_jobs (
                job_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL, printed_at TEXT,
                print_count INTEGER NOT NULL DEFAULT 0,
                inbox_week TEXT, inbox_active INTEGER NOT NULL DEFAULT 1,
                inbox_inactive_at TEXT
            )
            """
        )
        self.conn.execute(
            "INSERT INTO recipe_print_jobs(job_id,payload_json,created_at) VALUES (?,?,?)",
            ("monday-lunch", json.dumps(recipe(), sort_keys=True, separators=(",", ":")), "2026-10-25"),
        )
        self.conn.commit()
        from receipt_todo.recipes import ensure_jobs_table
        ensure_jobs_table(self.conn)
        self.assertEqual(get_recipe_job(self.conn, "monday-lunch")["job_kind"], "recipe")
        self.assertEqual(len(list_grocery_jobs(self.conn)), 0)


if __name__ == "__main__":
    unittest.main()

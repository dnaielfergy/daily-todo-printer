"""Regression and adversarial tests for approved weekly print reconciliation."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from receipt_todo.db import connect
from receipt_todo.inbox import InboxError, import_snapshot, load_snapshot
from receipt_todo.recipes import (
    ensure_jobs_table, get_recipe_job, print_recipe_job, requeue_recipe_job,
    submit_recipe_job,
)

WEEK = "2026-10-26"
MANIFEST = f"weeks/{WEEK}/manifest.json"


def job(name, *, date="2026-10-28", meal="dinner", servings=2):
    return {
        "schema_version": 1,
        "job_id": name,
        "date": date,
        "meal": meal,
        "recipe": {
            "title": name,
            "servings": servings,
            "ingredients": [{"name": "rice", "amount": "1 cup"}],
            "steps": ["Cook rice."],
        },
    }


def snapshot(revision, jobs, *, listed=True):
    files = {
        "inbox.json": {"schema_version": 1, "weeks": [MANIFEST] if listed else []},
        MANIFEST: {
            "schema_version": 1, "week_start": WEEK, "revision": revision,
            "jobs": [f"weeks/{WEEK}/jobs/{j['job_id']}.json" for j in jobs],
        },
    }
    for j in jobs:
        files[f"weeks/{WEEK}/jobs/{j['job_id']}.json"] = j

    class FakeClient:
        def commit_sha(self):
            return "c" * 40

        def read_file(self, sha, path):
            return files[path]

    return load_snapshot(FakeClient())


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.db"
        self.conn = connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def state(self, job_id):
        row = get_recipe_job(self.conn, job_id)
        return row["status"], row["inbox_active"], row["print_count"]

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_swap_cancel_and_no_revival_even_with_force(self, printer):
        lunch = job("mon-lunch-v1", date="2026-10-26", meal="lunch")
        dinner = job("wed-dinner-v1")
        self.assertEqual(import_snapshot(self.conn, snapshot(1, [lunch, dinner])), 2)
        self.assertEqual(self.state("mon-lunch-v1"), ("queued", 1, 0))
        self.assertTrue(print_recipe_job(self.conn, "mon-lunch-v1"))
        replacement = job("wed-dinner-v2", servings=4)
        self.assertEqual(import_snapshot(self.conn, snapshot(2, [replacement])), 1)
        self.assertEqual(self.state("mon-lunch-v1"), ("printed", 0, 1))
        self.assertEqual(self.state("wed-dinner-v1"), ("queued", 0, 0))
        self.assertEqual(self.state("wed-dinner-v2"), ("queued", 1, 0))
        for id_ in ("mon-lunch-v1", "wed-dinner-v1"):
            for force in (False, True):
                with self.subTest(id=id_, force=force), self.assertRaisesRegex(ValueError, "withdrawn"):
                    print_recipe_job(self.conn, id_, force=force)
        with self.assertRaisesRegex(ValueError, "requeued"):
            requeue_recipe_job(self.conn, "wed-dinner-v1")
        self.assertEqual(printer.call_count, 1)
        self.assertEqual(import_snapshot(self.conn, snapshot(2, [replacement])), 0)

    def test_explicit_empty_manifest_withdraws_and_pruning_is_safe(self):
        first = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [first]))
        with self.assertRaisesRegex(InboxError, "Cannot omit"):
            import_snapshot(self.conn, snapshot(1, [], listed=False))
        self.assertEqual(self.state("wed-dinner-v1"), ("queued", 1, 0))
        self.assertEqual(import_snapshot(self.conn, snapshot(2, [])), 0)
        self.assertEqual(self.state("wed-dinner-v1"), ("queued", 0, 0))
        self.assertEqual(import_snapshot(self.conn, snapshot(2, [], listed=False)), 0)
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, snapshot(1, [first]))
        with self.assertRaisesRegex(InboxError, "cannot be reactivated"):
            import_snapshot(self.conn, snapshot(3, [first]))

    def test_conflicting_same_revision_or_immutable_reuse_rolls_back(self):
        old = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [old]))
        another = job("wed-dinner-v2", servings=3)
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, snapshot(1, [another]))
        bad = job("wed-dinner-v1", servings=9)
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, snapshot(2, [another, bad]))
        self.assertEqual(self.state("wed-dinner-v1"), ("queued", 1, 0))
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM recipe_print_jobs WHERE job_id='wed-dinner-v2'"
        ).fetchone())
        self.assertEqual(self.conn.execute(
            "SELECT revision FROM recipe_inbox_publications WHERE week_start=?", (WEEK,)
        ).fetchone()[0], 1)

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_uncertain_print_history_preserved_and_no_requeue(self, printer):
        old = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [old]))
        printer.side_effect = RuntimeError("spooler uncertain")
        with self.assertRaises(RuntimeError):
            print_recipe_job(self.conn, old["job_id"])
        self.assertEqual(self.state(old["job_id"]), ("needs_review", 1, 0))
        import_snapshot(self.conn, snapshot(2, []))
        self.assertEqual(self.state(old["job_id"]), ("needs_review", 0, 0))
        with self.assertRaisesRegex(ValueError, "requeued"):
            requeue_recipe_job(self.conn, old["job_id"])

    def test_missing_other_week_with_active_job_rolls_back_new_week(self):
        old = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [old]))
        # An omitted manifest must not be silently interpreted as approval to cancel.
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, snapshot(2, [], listed=False))
        self.assertEqual(self.state(old["job_id"]), ("queued", 1, 0))

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_spooler_claim_serializes_against_reconciliation(self, printer):
        old = job("wed-dinner-v1")
        replacement = job("wed-dinner-v2")
        import_snapshot(self.conn, snapshot(1, [old]))
        update = snapshot(2, [replacement])

        def during_spooler(*_):
            competing = sqlite3.connect(self.path, timeout=0.05)
            competing.row_factory = sqlite3.Row
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    import_snapshot(competing, update)
            finally:
                competing.close()

        printer.side_effect = during_spooler
        self.assertTrue(print_recipe_job(self.conn, old["job_id"]))
        self.assertEqual(self.state(old["job_id"]), ("printed", 1, 1))
        self.assertEqual(import_snapshot(self.conn, update), 1)
        self.assertEqual(self.state(old["job_id"]), ("printed", 0, 1))
        self.assertEqual(self.state(replacement["job_id"]), ("queued", 1, 0))

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_interrupted_handoff_needs_review_not_automatic_retry(self, printer):
        old = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [old]))
        printer.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            print_recipe_job(self.conn, old["job_id"])
        self.assertEqual(self.state(old["job_id"]), ("needs_review", 1, 0))
        with self.assertRaisesRegex(ValueError, "needs_review"):
            print_recipe_job(self.conn, old["job_id"])
        # A different process can see the committed failure/uncertainty state.
        second = connect(self.path)
        try:
            self.assertEqual(get_recipe_job(second, old["job_id"])["status"], "needs_review")
        finally:
            second.close()

    def test_database_failure_after_retirement_rolls_back_entire_publication(self):
        old = job("wed-dinner-v1")
        import_snapshot(self.conn, snapshot(1, [old]))
        self.conn.execute(
            """
            CREATE TRIGGER fail_new_job BEFORE INSERT ON recipe_print_jobs
            WHEN NEW.job_id = 'wed-dinner-v2'
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END
            """
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected failure"):
            import_snapshot(self.conn, snapshot(2, [job("wed-dinner-v2")]))
        self.assertEqual(self.state(old["job_id"]), ("queued", 1, 0))
        self.assertEqual(self.conn.execute(
            "SELECT revision FROM recipe_inbox_publications WHERE week_start=?", (WEEK,)
        ).fetchone()[0], 1)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM recipe_print_jobs WHERE job_id='wed-dinner-v2'"
        ).fetchone())

    def test_legacy_schema_migration_preserves_existing_manual_history(self):
        old = job("legacy-manual")
        self.conn.execute(
            """
            CREATE TABLE recipe_print_jobs (
                job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL, printed_at TEXT,
                print_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.conn.execute(
            "INSERT INTO recipe_print_jobs "
            "(job_id, payload_json, status, created_at, print_count) "
            "VALUES (?, ?, 'printed', '2026-10-01', 1)",
            (old["job_id"], json.dumps(old, sort_keys=True, separators=(",", ":"))),
        )
        self.conn.commit()
        ensure_jobs_table(self.conn)
        self.assertEqual(self.state("legacy-manual"), ("printed", 1, 1))
        self.assertIsNone(get_recipe_job(self.conn, "legacy-manual")["inbox_week"])
        self.assertEqual(import_snapshot(self.conn, snapshot(1, [job("new-week-job")])), 1)

    def test_upgrades_identical_legacy_import_and_blocks_ambiguous_removal(self):
        old = job("wed-dinner-v1")
        snap = snapshot(1, [old])
        import_snapshot(self.conn, snap)
        self.conn.execute(
            "UPDATE recipe_print_jobs SET inbox_week = NULL WHERE job_id = ?",
            (old["job_id"],),
        )
        self.conn.execute(
            "UPDATE recipe_inbox_publications SET reconciled = 0 WHERE week_start = ?",
            (WEEK,),
        )
        self.conn.commit()
        self.assertEqual(import_snapshot(self.conn, snap), 0)
        self.assertEqual(get_recipe_job(self.conn, old["job_id"])["inbox_week"], WEEK)
        self.conn.execute(
            "UPDATE recipe_print_jobs SET inbox_week = NULL WHERE job_id = ?",
            (old["job_id"],),
        )
        self.conn.execute(
            "UPDATE recipe_inbox_publications SET reconciled = 0 WHERE week_start = ?",
            (WEEK,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(InboxError, "Unattributed legacy"):
            import_snapshot(self.conn, snapshot(2, [job("wed-dinner-v2")]))
        self.assertEqual(self.state(old["job_id"]), ("queued", 1, 0))

    def test_manual_recipe_with_same_date_is_not_reconciled(self):
        manual = job("manual-lunch", date="2026-10-26", meal="lunch")
        self.assertTrue(submit_recipe_job(self.conn, manual))
        published = job("wed-dinner-v1")
        self.assertEqual(import_snapshot(self.conn, snapshot(1, [published])), 1)
        self.assertEqual(import_snapshot(self.conn, snapshot(2, [])), 0)
        self.assertEqual(self.state("manual-lunch"), ("queued", 1, 0))


if __name__ == "__main__":
    unittest.main()

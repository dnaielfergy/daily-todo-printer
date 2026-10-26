import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from receipt_todo.db import add_task, connect, list_open_tasks
from receipt_todo.recipes import (
    get_recipe_job, list_recipe_jobs, print_recipe_job, requeue_recipe_job,
    render_recipe_job, submit_recipe_job, validate_recipe_job,
)


def sample_job():
    return {
        "schema_version": 1,
        "job_id": "meal-2026-10-26-lunch-v1",
        "date": "2026-10-26",
        "meal": "lunch",
        "source": {"type": "notion", "reference": "recipe-123"},
        "recipes": [{
            "title": "Miso salmon bowls",
            "servings": 2,
            "ingredients": [
                {"name": "salmon", "amount": "300 g"},
                {"name": "white miso", "amount": "2 tbsp"},
            ],
            "steps": ["Mix miso with water.", "Cook salmon and serve over rice."],
        }],
    }


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_validation_and_receipt(self):
        job = sample_job()
        output = render_recipe_job(job)
        self.assertIn("LUNCH / 2026-10-26", output)
        self.assertIn("300 g salmon", output)
        self.assertIn("2. Cook salmon", output)
        self.assertTrue(all(len(line) <= 42 for line in output.splitlines()))
        bad = sample_job()
        bad["recipes"][0]["steps"] = []
        with self.assertRaises(ValueError):
            validate_recipe_job(bad)
        multi = sample_job()
        multi["recipes"].append({
            "title": "Roasted potatoes", "servings": 2,
            "ingredients": [{"name": "potatoes", "amount": "500 g"}],
            "steps": ["Roast until crisp."]
        })
        receipt = render_recipe_job(multi)
        self.assertIn("Miso salmon bowls", receipt)
        self.assertIn("Roasted potatoes", receipt)
        self.assertEqual(receipt.count("INGREDIENTS"), 2)
        self.assertTrue(all(len(line) <= 42 for line in receipt.splitlines()))
        bad = sample_job()
        bad["recipes"] = []
        with self.assertRaises(ValueError):
            validate_recipe_job(bad)
        bad = sample_job()
        bad["date"] = "2026-02-30"
        with self.assertRaises(ValueError):
            validate_recipe_job(bad)

    def test_idempotent_submissions_reject_mutation(self):
        job = sample_job()
        self.assertTrue(submit_recipe_job(self.conn, job))
        self.assertFalse(submit_recipe_job(self.conn, job))
        changed = sample_job()
        changed["recipes"][0]["title"] = "Updated"
        with self.assertRaises(ValueError):
            submit_recipe_job(self.conn, changed)
        self.assertEqual(len(list_recipe_jobs(self.conn)), 1)

    @patch("receipt_todo.recipes.print_raw_windows")
    def test_print_once_and_force(self, printer):
        job = sample_job()
        submit_recipe_job(self.conn, job)
        self.assertTrue(print_recipe_job(self.conn, job["job_id"]))
        self.assertFalse(print_recipe_job(self.conn, job["job_id"]))
        self.assertTrue(print_recipe_job(self.conn, job["job_id"], force=True))
        self.assertEqual(printer.call_count, 2)
        row = get_recipe_job(self.conn, job["job_id"])
        self.assertEqual(row["print_count"], 2)
        self.assertEqual(row["status"], "printed")

    @patch("receipt_todo.recipes.print_raw_windows", side_effect=RuntimeError("spooler failed"))
    def test_failure_requires_explicit_requeue(self, printer):
        job = sample_job()
        submit_recipe_job(self.conn, job)
        with self.assertRaises(RuntimeError):
            print_recipe_job(self.conn, job["job_id"])
        self.assertEqual(get_recipe_job(self.conn, job["job_id"])["status"], "needs_review")
        with self.assertRaises(ValueError):
            print_recipe_job(self.conn, job["job_id"])
        requeue_recipe_job(self.conn, job["job_id"])
        self.assertEqual(get_recipe_job(self.conn, job["job_id"])["status"], "queued")

    def test_recipe_jobs_do_not_affect_todos(self):
        task = add_task(self.conn, "Pay bill")
        submit_recipe_job(self.conn, sample_job())
        self.assertEqual([task.id], [t.id for t in list_open_tasks(self.conn)])


if __name__ == "__main__":
    unittest.main()

"""Offline and adversarial tests for the private GitHub print inbox."""
import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from receipt_todo.db import add_task, connect, list_open_tasks
from receipt_todo.inbox import (
    GitHubInboxClient, InboxError, _parse_json, import_snapshot, load_snapshot,
)

SHA = "a" * 40
WEEK = "2026-10-26"
MANIFEST = f"weeks/{WEEK}/manifest.json"
JOB_PATH = f"weeks/{WEEK}/jobs/2026-10-26-lunch-v1.json"


def recipe_job(job_id="2026-10-26-lunch-v1", *, title="Rice bowls", day="2026-10-26", meal="lunch"):
    return {
        "schema_version": 1,
        "job_id": job_id,
        "date": day,
        "meal": meal,
        "recipe": {
            "title": title,
            "ingredients": [{"name": "rice", "amount": "1 cup"}],
            "steps": ["Cook rice and serve."],
        },
    }


def published(*, revision=1, job=None, paths=None):
    return {
        "inbox.json": {"schema_version": 1, "weeks": [MANIFEST]},
        MANIFEST: {
            "schema_version": 1,
            "week_start": WEEK,
            "revision": revision,
            "jobs": [JOB_PATH] if paths is None else paths,
        },
        JOB_PATH: job or recipe_job(),
    }


class FakeClient:
    def __init__(self, files):
        self.files = files
        self.requests = []

    def commit_sha(self):
        return SHA

    def read_file(self, sha, path):
        self.requests.append((sha, path))
        return self.files[path]


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "tasks.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_full_snapshot_is_commit_pinned_and_import_idempotent(self):
        task = add_task(self.conn, "Pick up groceries")
        client = FakeClient(published())
        snapshot = load_snapshot(client)
        self.assertTrue(all(sha == SHA for sha, _ in client.requests))
        self.assertEqual(import_snapshot(self.conn, snapshot, preview=True), 1)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='recipe_print_jobs'"
        ).fetchone())
        self.assertEqual(import_snapshot(self.conn, snapshot), 1)
        self.assertEqual(import_snapshot(self.conn, snapshot), 0)
        row = self.conn.execute("SELECT status, print_count FROM recipe_print_jobs").fetchone()
        self.assertEqual((row["status"], row["print_count"]), ("queued", 0))
        self.assertEqual([t.id for t in list_open_tasks(self.conn)], [task.id])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM recipe_inbox_publications").fetchone()[0], 1)

    def test_changed_snapshot_requires_new_revision_and_new_job_id(self):
        first = load_snapshot(FakeClient(published()))
        import_snapshot(self.conn, first)
        files = published(revision=1, job=recipe_job(title="Changed"))
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, load_snapshot(FakeClient(files)))
        # A revision increase still cannot rewrite immutable ID contents.
        files[MANIFEST]["revision"] = 2
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, load_snapshot(FakeClient(files)))
        files[MANIFEST]["jobs"] = ["weeks/2026-10-26/jobs/2026-10-26-lunch-v2.json"]
        files["weeks/2026-10-26/jobs/2026-10-26-lunch-v2.json"] = recipe_job(
            "2026-10-26-lunch-v2", title="Changed"
        )
        del files[JOB_PATH]
        self.assertEqual(import_snapshot(self.conn, load_snapshot(FakeClient(files))), 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM recipe_print_jobs").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT revision FROM recipe_inbox_publications").fetchone()[0], 2)

    def test_downgrade_rejected(self):
        first = published(revision=3)
        import_snapshot(self.conn, load_snapshot(FakeClient(first)))
        old = published(revision=2)
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, load_snapshot(FakeClient(old)))
        self.assertEqual(self.conn.execute("SELECT revision FROM recipe_inbox_publications").fetchone()[0], 3)

    def test_snapshot_rejected_before_partial_import(self):
        files = published()
        path2 = "weeks/2026-10-26/jobs/tuesday-dinner.json"
        files[MANIFEST]["jobs"].append(path2)
        files[path2] = recipe_job("tuesday-dinner", day="2026-10-27", meal="dinner")
        files[path2]["recipe"]["steps"] = []
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='recipe_print_jobs'"
        ).fetchone())

    def test_rollback_if_any_week_conflicts(self):
        import_snapshot(self.conn, load_snapshot(FakeClient(published())))
        second_week = "2026-11-02"
        second_manifest = f"weeks/{second_week}/manifest.json"
        second_path = f"weeks/{second_week}/jobs/2026-11-02-lunch-v1.json"
        files = published(revision=2, job=recipe_job(title="Reused ID"))
        files["inbox.json"]["weeks"] = [second_manifest, MANIFEST]
        files[second_manifest] = {
            "schema_version": 1, "week_start": second_week,
            "revision": 1, "jobs": [second_path],
        }
        files[second_path] = recipe_job("2026-11-02-lunch-v1", day="2026-11-02")
        with self.assertRaises(InboxError):
            import_snapshot(self.conn, load_snapshot(FakeClient(files)))
        # Even though the new week is processed first, it must not be committed.
        self.assertEqual(self.conn.execute("SELECT count(*) FROM recipe_print_jobs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM recipe_inbox_publications").fetchone()[0], 1)

    def test_reject_manifest_traversal_and_wrong_week(self):
        for path in (
            "../../secrets.json", "/etc/passwd", "weeks/2026-10-26/../secret.json",
            "weeks/2026-10-27/manifest.json",
        ):
            files = published()
            files["inbox.json"]["weeks"] = [path]
            with self.subTest(path=path), self.assertRaises(InboxError):
                load_snapshot(FakeClient(files))
        files = published(paths=["weeks/2026-10-26/jobs/../../secret.json"])
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))

    def test_reject_duplicate_slots_and_ids(self):
        files = published()
        path2 = "weeks/2026-10-26/jobs/different-file.json"
        files[MANIFEST]["jobs"].append(path2)
        files[path2] = recipe_job("other-id")
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))
        files[path2] = recipe_job("2026-10-26-lunch-v1", meal="dinner")
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))

    def test_reject_out_of_week_jobs_and_unknown_schema(self):
        files = published()
        files[JOB_PATH]["date"] = "2026-11-02"
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))
        files = published()
        files["inbox.json"]["schema_version"] = 2
        with self.assertRaises(InboxError):
            load_snapshot(FakeClient(files))

    def test_empty_inbox_is_a_safe_noop(self):
        files = {"inbox.json": {"schema_version": 1, "weeks": []}}
        snap = load_snapshot(FakeClient(files))
        self.assertEqual(import_snapshot(self.conn, snap), 0)
        self.assertEqual(len(snap.weeks), 0)

    def test_duplicate_json_keys_and_size_limit(self):
        with self.assertRaises(InboxError):
            _parse_json(b'{"schema_version":1,"schema_version":2}', "test")
        with self.assertRaises(InboxError):
            _parse_json(b" " * 131073, "oversized")

    def test_auth_and_repo_validation(self):
        for repo, ref in (
            ("evil/../../repo", "main"),
            ("example/print", "--upload-pack=evil"),
            ("example/print", "main..other"),
            ("example/print", "feature//branch"),
        ):
            with self.subTest(repo=repo, ref=ref), self.assertRaises(InboxError):
                GitHubInboxClient(repo=repo, ref=ref, token="secret")
        with self.assertRaises(InboxError):
            GitHubInboxClient(repo="example/print", ref="main", token="")

    @patch("receipt_todo.inbox._OPENER.open")
    def test_remote_api_pins_all_files_to_commit_and_uses_bearer_auth(self, urlopen):
        class Response:
            def __init__(self, value):
                self.data = json.dumps(value).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, amount):
                return self.data[:amount]

        files = published()
        calls = []

        def serve(request, timeout):
            self.assertEqual(timeout, 20)
            self.assertEqual(request.get_header("Authorization"), "Bearer fake-token")
            calls.append(request.full_url)
            if "/git/ref/heads/main" in request.full_url:
                return Response({"object": {"type": "commit", "sha": SHA}})
            path = request.full_url.split("/contents/")[1].split("?")[0]
            self.assertIn("ref=" + SHA, request.full_url)
            raw = json.dumps(files[path]).encode()
            return Response({"type": "file", "encoding": "base64",
                             "size": len(raw), "content": base64.b64encode(raw).decode()})

        urlopen.side_effect = serve
        client = GitHubInboxClient(repo="dnaielfergy/household-print-inbox", ref="main", token="fake-token")
        snapshot = load_snapshot(client)
        self.assertEqual(len(snapshot.weeks), 1)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call.startswith("https://api.github.com/repos/") for call in calls))


if __name__ == "__main__":
    unittest.main()

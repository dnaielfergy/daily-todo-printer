"""Read-only, source-independent GitHub print inbox ingestion.

Only imports print snapshots; scheduled printing and cancellation are separate features.
All files are fetched from the SAME immutable Git commit, not moving branch HEAD.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .recipes import ensure_jobs_table, validate_recipe_job

REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.ASCII)
BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", re.ASCII)
SHA_PATTERN = re.compile(r"[a-f0-9]{40}", re.ASCII)
MANIFEST_PATTERN = re.compile(r"weeks/(\d{4}-\d{2}-\d{2})/manifest\.json", re.ASCII)
JOB_PATH_PATTERN = re.compile(
    r"weeks/(\d{4}-\d{2}-\d{2})/jobs/[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.json",
    re.ASCII,
)
MAX_FILE_SIZE = 131_072
MAX_WEEKS = 8
MAX_JOBS_PER_WEEK = 14


class InboxError(ValueError):
    """Invalid publication, unsafe contents, or an unavailable inbox."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a private-repository token to a redirected host."""

    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InboxError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(content: bytes, label: str) -> dict:
    if len(content) > MAX_FILE_SIZE:
        raise InboxError(f"{label} exceeds size limit")
    try:
        obj = json.loads(content.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InboxError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(obj, dict):
        raise InboxError(f"{label} must be a JSON object")
    return obj


def _versioned(obj: dict, label: str) -> None:
    if type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        raise InboxError(f"{label}: unsupported schema_version")


def _week_start(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InboxError("week_start must be YYYY-MM-DD")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise InboxError("Invalid week_start") from exc
    if day.weekday() != 0:
        raise InboxError("week_start must be a Monday")
    return value


def _manifest_path(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise InboxError("Manifest path must be text")
    match = MANIFEST_PATTERN.fullmatch(value)
    if match is None:
        raise InboxError("Unsafe manifest path (expected weeks/YYYY-MM-DD/manifest.json)")
    return value, _week_start(match.group(1))


def _job_path(value: object, week: str) -> str:
    if not isinstance(value, str):
        raise InboxError("Job path must be text")
    match = JOB_PATH_PATTERN.fullmatch(value)
    if match is None or match.group(1) != week:
        raise InboxError("Unsafe job path or job outside its week")
    if ".." in value:
        raise InboxError("Unsafe job path")
    return value


def _serialized(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class WeekPublication:
    week_start: str
    revision: int
    fingerprint: str
    jobs: tuple[dict, ...]


@dataclass(frozen=True)
class InboxSnapshot:
    commit_sha: str
    weeks: tuple[WeekPublication, ...]


class GitHubInboxClient:
    """Pinned-commit, read-only GitHub REST client; never executes published content."""

    def __init__(self, *, repo: str, ref: str, token: str):
        if REPO_PATTERN.fullmatch(repo) is None or ".." in repo:
            raise InboxError("Invalid GitHub repository slug")
        if (
            BRANCH_PATTERN.fullmatch(ref) is None
            or ".." in ref
            or "//" in ref
            or ref.endswith(("/", ".", ".lock"))
        ):
            raise InboxError("Invalid GitHub branch")
        if not isinstance(token, str) or not token.strip():
            raise InboxError("Set RECIPE_INBOX_TOKEN or recipe_inbox.token")
        self.repo = repo
        self.ref = ref
        self._token = token.strip()

    def _request(self, endpoint: str) -> dict:
        url = f"https://api.github.com/repos/{self.repo}/{endpoint}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "receipt-todo-print-inbox",
            },
        )
        try:
            # Limit JSON responses too, including unexpected API error bodies.
            with _OPENER.open(request, timeout=20) as response:
                raw = response.read(MAX_FILE_SIZE * 3 + 1)
        except urllib.error.HTTPError as exc:
            # Never display credentials, request headers, or server response bodies.
            raise InboxError(f"GitHub inbox HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise InboxError("GitHub inbox unavailable; retry on next scheduled run") from None
        if len(raw) > MAX_FILE_SIZE * 3:
            raise InboxError("GitHub response exceeds size limit")
        return _parse_json(raw, "GitHub response")

    def commit_sha(self) -> str:
        branch = urllib.parse.quote(self.ref, safe="")
        data = self._request(f"git/ref/heads/{branch}")
        git_object = data.get("object")
        if not isinstance(git_object, dict) or git_object.get("type") != "commit":
            raise InboxError("GitHub branch did not resolve to a commit")
        sha = git_object.get("sha")
        if not isinstance(sha, str) or SHA_PATTERN.fullmatch(sha) is None:
            raise InboxError("GitHub branch returned an invalid SHA")
        return sha

    def read_file(self, sha: str, path: str) -> dict:
        if SHA_PATTERN.fullmatch(sha) is None:
            raise InboxError("Invalid commit SHA")
        if path != "inbox.json" and not (
            MANIFEST_PATTERN.fullmatch(path) or JOB_PATH_PATTERN.fullmatch(path)
        ):
            raise InboxError("Unsafe inbox file path")
        encoded = urllib.parse.quote(path, safe="/")
        data = self._request(f"contents/{encoded}?ref={sha}")
        if data.get("type") != "file" or data.get("encoding") != "base64":
            raise InboxError(f"Unsupported GitHub file: {path}")
        size = data.get("size")
        if type(size) is not int or size < 0 or size > MAX_FILE_SIZE:
            raise InboxError(f"Invalid file size: {path}")
        encoded_content = data.get("content")
        if not isinstance(encoded_content, str):
            raise InboxError(f"Missing file content: {path}")
        try:
            content = base64.b64decode(encoded_content.replace("\n", "").replace("\r", ""), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise InboxError(f"Invalid base64 file: {path}") from exc
        if len(content) != size:
            raise InboxError(f"File size mismatch: {path}")
        return _parse_json(content, path)


def load_snapshot(client: GitHubInboxClient, *, sha: str | None = None) -> InboxSnapshot:
    sha = sha or client.commit_sha()
    if SHA_PATTERN.fullmatch(sha) is None:
        raise InboxError("Invalid pinned commit SHA")
    index = client.read_file(sha, "inbox.json")
    _versioned(index, "inbox.json")
    paths = index.get("weeks")
    if not isinstance(paths, list) or len(paths) > MAX_WEEKS:
        raise InboxError("inbox.json must list at most 8 weekly manifests")
    seen_weeks: set[str] = set()
    seen_ids: set[str] = set()
    weeks: list[WeekPublication] = []
    for raw_path in paths:
        path, week = _manifest_path(raw_path)
        if week in seen_weeks:
            raise InboxError("Duplicate weekly manifest")
        seen_weeks.add(week)
        manifest = client.read_file(sha, path)
        _versioned(manifest, path)
        if manifest.get("week_start") != week:
            raise InboxError(f"{path}: week_start mismatch")
        revision = manifest.get("revision")
        if type(revision) is not int or not 1 <= revision <= 1_000_000_000:
            raise InboxError(f"{path}: revision must be a positive integer")
        paths_in_week = manifest.get("jobs")
        if not isinstance(paths_in_week, list) or len(paths_in_week) > MAX_JOBS_PER_WEEK:
            raise InboxError(f"{path}: at most 14 recipe jobs per week")
        jobs = []
        seen_slots = set()
        seen_paths = set()
        for raw_job_path in paths_in_week:
            job_path = _job_path(raw_job_path, week)
            if job_path in seen_paths:
                raise InboxError("Duplicate job path")
            seen_paths.add(job_path)
            job = client.read_file(sha, job_path)
            try:
                validate_recipe_job(job)
            except ValueError as exc:
                raise InboxError(f"{job_path}: {exc}") from exc
            job_date = date.fromisoformat(job["date"])
            if not date.fromisoformat(week) <= job_date < date.fromisoformat(week) + timedelta(days=7):
                raise InboxError(f"{job_path}: job date outside its week")
            slot = (job["date"], job["meal"])
            if slot in seen_slots:
                raise InboxError(f"{path}: duplicate date/meal assignment")
            seen_slots.add(slot)
            if job["job_id"] in seen_ids:
                raise InboxError("Duplicate job_id across publications")
            seen_ids.add(job["job_id"])
            jobs.append(job)
        fingerprint = hashlib.sha256(
            _serialized({"manifest": manifest, "jobs": jobs}).encode("utf-8")
        ).hexdigest()
        weeks.append(WeekPublication(week, revision, fingerprint, tuple(jobs)))
    return InboxSnapshot(sha, tuple(weeks))


def import_snapshot(conn: sqlite3.Connection, snapshot: InboxSnapshot, *, preview: bool = False) -> int:
    """Atomically apply only previously unseen jobs after validating whole publication."""
    if preview:
        return sum(len(week.jobs) for week in snapshot.weeks)
    ensure_jobs_table(conn)
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_inbox_publications (
                week_start TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        # Preflight every week and job before any insert: conflict means full rollback.
        for week in snapshot.weeks:
            record = conn.execute(
                "SELECT revision, fingerprint FROM recipe_inbox_publications WHERE week_start = ?",
                (week.week_start,),
            ).fetchone()
            if record is not None and (
                week.revision < record["revision"]
                or (week.revision == record["revision"] and week.fingerprint != record["fingerprint"])
            ):
                raise InboxError(f"Rejected stale or changed-without-revision publication for {week.week_start}")
            for job in week.jobs:
                old = conn.execute(
                    "SELECT payload_json FROM recipe_print_jobs WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                if old is not None and old["payload_json"] != _serialized(job):
                    raise InboxError(f"Immutable job_id reused with different recipe: {job['job_id']}")

        added = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for week in snapshot.weeks:
            for job in week.jobs:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO recipe_print_jobs (job_id, payload_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (job["job_id"], _serialized(job), now),
                )
                added += cursor.rowcount
            conn.execute(
                """
                INSERT INTO recipe_inbox_publications (
                    week_start, revision, fingerprint, commit_sha, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(week_start) DO UPDATE SET
                    revision = excluded.revision,
                    fingerprint = excluded.fingerprint,
                    commit_sha = excluded.commit_sha,
                    applied_at = excluded.applied_at
                """,
                (week.week_start, week.revision, week.fingerprint, snapshot.commit_sha, now),
            )
    return added

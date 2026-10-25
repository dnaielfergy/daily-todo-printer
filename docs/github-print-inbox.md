# Private GitHub recipe print inbox — Phase A

Implementation: [issue #14](https://github.com/dnaielfergy/daily-todo-printer/issues/14).
The **private** repository [dnaielfergy/household-print-inbox](https://github.com/dnaielfergy/household-print-inbox) is the published source of truth for **approved print jobs**. Notion/Obsidian (later) is the editable source of truth for recipes and meal plans. Local SQLite is the operational truth for accepted jobs and what the spooler attempted.

**Working → Published → Executed** is strictly one-way. Neither GitHub nor SQLite is a second recipe editor; never push SQLite files to GitHub.

## What Phase A does

- The publisher (initially a ChatGPT GitHub connector) writes a reviewed publication to the private repository's `main` branch.
- On Windows, `receipt-todo recipe sync` reads a single immutable commit over outbound HTTPS with a read-only GitHub API token and validates **all** referenced files before SQLite writes.
- New, fully validated jobs are stored as immutable snapshots in the existing `data/tasks.db` database. Repeated polls are safe. Publication revisions are recorded by week.
- `recipe list`, `recipe preview JOB_ID`, and `recipe print JOB_ID` work as before.

**Phase A NEVER automatically prints** and **does not yet cancel/supersede old queued jobs**. Do not automate meal receipt printing until Phase B safely reconciles plan edits/removals, missed print times, and grocery slips. Existing morning todo and Telegram jobs remain untouched.

## Publication format

Private repository:

```text
inbox.json
weeks/
  2026-10-26/
    manifest.json
    jobs/
      2026-10-26-lunch-v1.json
```

The active root index lists up to eight weekly manifest paths:

```json
{"schema_version":1,"weeks":["weeks/2026-10-26/manifest.json"]}
```

A weekly manifest lists exactly the desired future lunch/dinner slots for that week:

```json
{
  "schema_version": 1,
  "week_start": "2026-10-26",
  "revision": 1,
  "jobs": ["weeks/2026-10-26/jobs/2026-10-26-lunch-v1.json"]
}
```

Each job references a complete [recipe v1 job](recipe-jobs.md) (same JSON accepted by `recipe submit`). It is an immutable *print snapshot*, not a source recipe. One job per calendar date and meal slot; use a distinct `job_id` for every changed job or date. Once a manifest has been published, increment `revision` for any change; never reuse a job ID with different content.

Publisher process: prepare jobs and manifest on a branch; review changes, then merge the branch atomically into private `main`. Avoid publishing partial multi-commit updates directly to `main`. Older Git commits remain historical, not executable commands. The importer pins all API requests to the same resolved commit SHA.

## One-time Windows configuration

1. Create a **fine-grained GitHub PAT** scoped **only** to `dnaielfergy/household-print-inbox` with **Contents: Read-only**, metadata read. Give it an expiration you can maintain. Never put it in Git, scripts, public issues or chat.
2. In ignored `C:\Projects\daily-todo-printer\config.local.toml`, add:
   ```toml
   [recipe_inbox]
   repo = "dnaielfergy/household-print-inbox"
   ref = "main"
   token = "YOUR_READ_ONLY_TOKEN"
   ```
   Alternatively set `RECIPE_INBOX_TOKEN` for the **scheduled task's process identity**; it overrides the TOML token. Do not set this environment variable only in your interactive PowerShell session if the scheduled task runs as SYSTEM.
3. Protect `config.local.toml` using Windows filesystem ACLs; the SYSTEM account needs read access. Keep your existing Telegram token and todo DB untouched. If the repo is accessible through a GitHub App token instead, ensure the same minimum read permission and renewal handling.
4. Test manually from the repo root:

   ```powershell
   .\.venv\Scripts\receipt-todo.exe recipe sync --config config.local.toml --preview
   .\.venv\Scripts\receipt-todo.exe recipe sync --config config.local.toml
   .\.venv\Scripts\receipt-todo.exe recipe sync --config config.local.toml --refresh
   .\.venv\Scripts\receipt-todo.exe recipe list
   ```

   The sample private inbox's active `inbox.json` initially has `weeks: []`: it intentionally imports **zero** jobs until you approve and publish a week. Example files live in `examples/` and are not active. A preview fetches and validates remote data without adding print jobs. Subsequent scheduled polls fetch only the branch SHA if it has not changed; use `--refresh` to force re-validation of the same publication after repairing local state.
5. After verifying the manual sync, run PowerShell **as Administrator**:
   ```powershell
   .\scripts\install-recipe-sync-task.ps1 -EveryMinutes 5
   ```
   This adds `Daily Todo Printer - Recipe Inbox` running as SYSTEM. Each run imports only, never prints. It skips overlapping runs and stops an overlong poll. Verify via Task Scheduler History / Last Run Result.

## Failure and safety

- If GitHub is offline or the token expires, the sync task fails and the next scheduled run retries. Existing SQLite jobs can still print manually. Neither the morning todo printer nor Telegram needs GitHub.
- Full snapshot validation occurs before any SQL mutation; a conflict rolls back the batch. Invalid versions, weeks outside Monday boundaries, duplicate slots, path traversal, unexpectedly large files, changed-without-revision publications and stale revisions fail closed.
- Jobs and manifests are read as **data only**. No GitHub content is executed, and only GitHub's fixed API hostname is contacted. HTTP redirects are disabled to prevent credentials being forwarded to another host.
- Publishing a new revision does **not** remove earlier SQLite jobs during Phase A. This is why scheduled recipe printing is intentionally not enabled yet. Phase B will reconcile superseded future jobs and provide safe missed-run rules.
- A successful Windows spooler return isn't proof paper emerged. Continue using the existing manual `requeue` and `--force` rules for uncertain/repeated prints.
- Back up `data/tasks.db` using a SQLite-safe backup method rather than copying it while active. GitHub history is NOT a SQLite backup.
- If your credential leaks, revoke it in GitHub immediately, rotate the token, and inspect repository access.

## Acceptance tests / review

Run `python -m unittest discover -s tests -v` on Windows (CI also runs on Windows).
Verify a private branch with an example published week before merging both PRs, repeat sync, confirm zero duplicate imports, confirm malicious paths and incorrect revisions are rejected in tests, and inspect the resulting `recipe_print_jobs` table. No real meal receipts should be printed automatically.

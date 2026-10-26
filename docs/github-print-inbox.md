# Private GitHub recipe and grocery print inbox — Phase A transport + B1/B2 reconciliation

Implementation: [issue #14](https://github.com/dnaielfergy/daily-todo-printer/issues/14).
The **private** repository [dnaielfergy/household-print-inbox](https://github.com/dnaielfergy/household-print-inbox) is the published source of truth for **approved print jobs**. The existing household Notion recipe workspace (future ChatGPT workflow in #19) is the editable source of truth for recipes and meal plans. Local SQLite is the operational truth for accepted jobs and what the spooler attempted.

**Working → Published → Executed** is strictly one-way. Neither GitHub nor SQLite is a second recipe editor; never push SQLite files to GitHub.

## Transport and reconciliation

- The publisher (initially a ChatGPT GitHub connector) writes a reviewed publication to the private repository's `main` branch.
- On Windows, `receipt-todo recipe sync` reads a single immutable commit over outbound HTTPS with a read-only GitHub API token and validates **all** referenced files before SQLite writes.
- New, fully validated jobs are stored as immutable snapshots in the existing `data/tasks.db` database. Repeated polls are safe. Publication revisions are recorded by week. A newer approved weekly manifest marks omitted earlier jobs **withdrawn** while preserving their payload, prior status and print history. SQLite reconciles a whole snapshot transactionally.
- `recipe list` shows withdrawn jobs (e.g. `[queued, withdrawn]`). `recipe print JOB_ID` and `recipe print JOB_ID --force` refuse withdrawn jobs; `recipe requeue JOB_ID` refuses them too. Manually submitted jobs remain independent of the inbox.

**The inbox never automatically prints.** B1 (#17) handles supersession, and B2 (#18) adds versioned 80mm grocery receipts with the same safety protections. Notion/ChatGPT publishing (#19) and conservative scheduling (#20) are separate later steps. Existing morning todo and Telegram jobs remain untouched.

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

### Approved weekly revision rules (Phase B1)

- A **week manifest's `jobs` list is the complete current approved set for that week**, not an append-only list. If Wednesday dinner is replaced, create a new immutable job JSON with a **new `job_id`** and increase the manifest's `revision`; remove the old job from the list. If Thursday lunch is cancelled, remove that job and increase `revision`. If servings or ingredients change, treat this as a new job.
- Importing the higher revision atomically sets `inbox_active = 0` and `inbox_inactive_at` on previously published jobs absent from the new manifest. The old row remains for history, with its previous `status` and `print_count`. A retired job ID **cannot be reactivated**, even under a later revision.
- **Withdrawing an entire week:** publish `{"schema_version":1,"week_start":"YYYY-MM-DD","revision":N,"jobs":[]}` with `N` greater than the previous revision, and keep the manifest listed in `inbox.json` for a successful sync. Only then may you prune that week from the index. **Removing a week from `inbox.json` alone is NOT cancellation:** an omitted week containing any active queued/uncertain job is rejected for the entire snapshot. This avoids a dropped index reference silently leaving a meal active or falsely signalling withdrawal.
- Weeks omitted from the root index after their jobs have been fully retired (or after they've all been printed) are **archival/pruning only**; the last accepted weekly revision remains recorded locally. A stale manifest or same-revision changed content is rejected when it reappears. Already withdrawn job IDs cannot be reinstated. Already printed history is preserved, with no automatic reprint.
- A submitted recipe (via `recipe submit`) has no `inbox_week`, and is *not* governed by the GitHub weekly manifest. Use publication-specific IDs to avoid collisions. An existing manual job ID cannot be adopted into a new publication.
- Prints acquire a SQLite `BEGIN IMMEDIATE` lock for the spooler handoff, so an approved-plan import cannot withdraw the job between the eligibility check and the print call. The tradeoff is that other SQLite writes may wait for the Windows spooler; any timeout causes the sync to retry on the next poll. Neither this protection nor spooler return proves a physical receipt was delivered.
- No scheduled meal or grocery printing is enabled. Current jobs require explicit manual `recipe print` or `grocery print` commands.

### Grocery snapshot in the weekly manifest (Phase B2)

A weekly manifest can optionally reference **one** grocery payload alongside the meal `jobs` array:

```json
{
  "schema_version": 1,
  "week_start": "2026-10-26",
  "revision": 2,
  "jobs": ["weeks/2026-10-26/jobs/wednesday-dinner-v2.json"],
  "grocery": "weeks/2026-10-26/groceries/shopping-v2.json"
}
```

The grocery path must remain within `weeks/YYYY-MM-DD/groceries/`, have a safe filename, and resolve to a [v1 grocery job](grocery-jobs.md) with a matching `week_start` and `plan_revision = 2`. The importer validates it with the recipe jobs from **one pinned commit**, then reconciles the entire approval transactionally in existing SQLite. A new grocery ID is required whenever that approved week is revised; reusing the previous grocery job under a new manifest revision is rejected.

**Crucial:** A changed meal plan cannot keep its old grocery snapshot by accident. On every new manifest revision, include a new approved grocery snapshot (with its matching plan revision and new job ID), or **omit `grocery`** to intentionally retire the older one. Explicitly cancelling a whole week still requires a higher-revision empty `jobs: []` manifest **without** a `grocery` reference before removing the week from `inbox.json`. Previously printed groceries remain historical, never auto-reprinted.

The five-minute importer continues to be read-only and import-only. No grocery or recipe receipt prints without an explicit `grocery print`/`recipe print` call.

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
- A newer approved revision marks removed SQLite jobs **withdrawn**, instead of deleting them or changing their historical print status. Both normal print and `--force` deny withdrawn jobs. Retry/requeue after an ambiguous failure also denies a withdrawn job. Deliberate historical-print support is a separate, explicitly audited future feature; no override is provided for withdrawn jobs in B1.
- A successful Windows spooler return isn't proof paper emerged. Continue using the existing manual `requeue` and `--force` rules for uncertain/repeated prints.
- Back up `data/tasks.db` using a SQLite-safe backup method rather than copying it while active. GitHub history is NOT a SQLite backup.
- If your credential leaks, revoke it in GitHub immediately, rotate the token, and inspect repository access.

## One-time upgrade for existing Phase A installations

- Take a SQLite-safe backup of `data/tasks.db` before changing code. After deploying B1, run `receipt-todo recipe sync --config config.local.toml --refresh` to reconcile the current approved inbox.
- B1 uses a versioned local `app_state` SHA cache key so the first scheduled B1 poll reads current GitHub content even if the commit SHA didn't change since Phase A. It adds `inbox_week`, `inbox_active`, and `inbox_inactive_at` to existing recipe rows without modifying their print status or immutable payload.
- A nonempty Phase A inbox had **no stored row-level publication ownership**. B1 can adopt identical IDs listed in the current approved manifest. If a previous approved job was removed from that manifest before migration, the importer cannot safely distinguish it from a manually submitted recipe and rejects the publication as `Unattributed legacy recipe job`. In that case, **do not delete rows or force changes**: inspect `recipe_print_jobs` and the last approved GitHub revision to perform an explicit one-time migration/reconciliation. This safeguard does not affect the verified empty live inbox used at initial rollout.
- If a future publication omits a still-active week from `inbox.json`, sync fails closed. Publish an explicit higher-revision empty manifest for that week first.
- B2 adds a backwards-compatible `job_kind` migration (`recipe` is the default for existing jobs) and versioned SHA-cache reconciliation. No Windows scheduled task, Telegram listener, or 7 AM todo receipt is modified.

## Acceptance tests / review

Run `python -m unittest discover -s tests -v` on Windows (CI also runs on Windows).
Verify a private branch with an example published week before merging both PRs, repeat sync, confirm zero duplicate imports, confirm malicious paths and incorrect revisions are rejected in tests, and inspect the resulting `recipe_print_jobs` table. No real meal receipts should be printed automatically.

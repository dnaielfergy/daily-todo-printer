# Grocery print jobs (v1)

Grocery receipts are **precomputed and explicitly approved upstream**. The Windows printer never decides what to buy, performs serving math, merges entries, or converts units. It stores immutable print snapshots in the existing SQLite database alongside recipes, using a distinct `job_kind = 'grocery'` value.

**No automatic grocery or recipe printing exists in this milestone.** The five-minute GitHub poller imports approved data; only explicit `grocery print` sends bytes to the printer.

## Local manual workflow

Sample: [example-grocery-job.json](example-grocery-job.json) contains fictional ingredients and no household data.

```powershell
receipt-todo grocery submit docs/example-grocery-job.json
receipt-todo grocery list
receipt-todo grocery preview 2026-10-26-groceries-example-v1
receipt-todo grocery print 2026-10-26-groceries-example-v1
```

The first normal print claims a job and records its spooler handoff; subsequent normal calls report `Already printed`. To intentionally print another copy of an **active** grocery snapshot use `grocery print JOB_ID --force`. If the spooler errors or the process is interrupted, inspect the physical printer first, then `grocery requeue JOB_ID` to recover from `printing`/`needs_review`. A spooler acknowledgment is **not** proof that paper emerged; never auto-retry an ambiguous job.

An obsolete/withdrawn grocery job may still be listed and previewed as historical data, but cannot be normally printed, force-printed, resubmitted, or requeued.

## JSON contract

```json
{
  "schema_version": 1,
  "job_id": "2026-10-26-groceries-v3",
  "week_start": "2026-10-26",
  "plan_revision": 3,
  "title": "Weekly groceries",
  "groups": [
    {
      "name": "Produce",
      "items": [
        {"name": "tomatoes", "amount": "4", "note": "ripe"},
        {"name": "fresh basil", "amount": "1 bunch"}
      ]
    },
    {"name": "Pantry", "items": [{"name": "rice", "amount": "1 1/2 cups"}]}
  ],
  "source": {"type": "notion", "reference": "optional-origin-id"}
}
```

- `schema_version`: required integer `1`.
- `job_id`: required immutable globally unique ID (shared across meal jobs and grocery jobs), 1–120 safe ASCII characters: letters, digits, `.`, `_`, `:`, `-`. An unchanged submission is idempotent; changed payload or changed job type **must** use a new ID.
- `week_start`: required ISO Monday in `YYYY-MM-DD`; must equal the weekly manifest's `week_start` when published.
- `plan_revision`: optional for **manual** standalone grocery jobs; **required for GitHub-published groceries**, and must equal that week's manifest `revision` (positive integer up to 1,000,000,000). A new approval/revision requires a new grocery snapshot **with a new job ID** or an explicitly absent grocery reference.
- `title`: optional print heading, at most 120 characters.
- `groups`: 1–12 groups, each with `name` (max 60 characters) and nonempty `items` array; **or** provide a single flat top-level `items` array instead of `groups`.
- Each item has `name` (required, max 160 characters), `amount` (optional, approved text, max 80 characters), and `note` (optional, max 160 characters). At most **120 total items** per grocery receipt.
- `source`: optional `{ "type": "...", "reference": "..." }` for provenance (type max 40, reference max 500 characters). No source-specific behavior on Windows.
- Extra fields, unsupported schema versions, unsafe job IDs, malformed dates, oversized files, JSON with duplicate keys, control characters (including ESC/POS codes), and characters the existing CP437 printer adapter cannot represent are rejected. A JSON file must fit the existing **128 KiB** remote size bound.

Quantity units are **human-readable text** (e.g. `"2 cans"`, `"1 1/2 cups"`). Omitted `amount` simply prints the item name; the host never assumes `1`. Repeated entries are intentionally not combined at print time. The upstream planning agent must resolve any ambiguous conversions before human approval.

Each grocery receipt uses the same **42-column/80mm ESC/POS renderer** as recipe receipts. Sections, checkboxes `[ ]`, item labels/quantities, notes, and immutable job ID are laid out with deterministic wrapping.

## Publishing through the private GitHub inbox

The existing `receipt-todo recipe sync --config config.local.toml` command handles both recipe and grocery jobs; no new scheduler or credential is required. The manifest remains revisioned per week:

```json
{
  "schema_version": 1,
  "week_start": "2026-10-26",
  "revision": 3,
  "jobs": ["weeks/2026-10-26/jobs/monday-lunch-v3.json"],
  "grocery": "weeks/2026-10-26/groceries/2026-10-26-groceries-v3.json"
}
```

The optional `grocery` path must match `weeks/<same Monday>/groceries/<safe filename>.json`. At most one grocery job per week can be current. It must point to valid JSON with matching `week_start` and `plan_revision`. Import validates **all** referenced files from one pinned Git commit and applies the entire week atomically.

**Revision rule:** If the approved menu changes, its current grocery snapshot is no longer automatically valid. Publish a new grocery file/job ID for the new manifest revision, or remove the `grocery` field to withdraw the old grocery job. Leaving an older grocery job attached to a new revision fails validation. Removing the `grocery` field from a higher approved manifest withdraws prior unprinted groceries; printed receipts remain historical and cannot automatically reprint.

**Whole-week withdrawal** still requires a higher-revision manifest with `jobs: []` and no `grocery` reference before pruning the manifest from `inbox.json`. Removing the week from the index alone is not authorization to cancel pending groceries.

Prepare snapshots on a review branch in **private** `dnaielfergy/household-print-inbox`, get explicit approval of the menu **and grocery quantities**, and merge the complete publication as one commit. Do not place real recipes, credentials, or SQLite files in the public printer repository.

Related: [private GitHub inbox policy](github-print-inbox.md), [recipe print jobs](recipe-jobs.md), and [#18](https://github.com/dnaielfergy/daily-todo-printer/issues/18).

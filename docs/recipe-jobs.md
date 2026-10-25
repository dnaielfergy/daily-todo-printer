# Recipe print jobs (v1)

The recipe system does **not** own an editable cookbook. Any upstream system (Notion, Obsidian, an agent, a manual file) may assemble a validated job and submit it as JSON. The printer host keeps an immutable snapshot, queues it, and prints it through the existing Windows ESC/POS adapter. No Notion login, cloud AI, local LLM, or inbound server is needed.

## Example

See [example-recipe-job.json](example-recipe-job.json).

```powershell
receipt-todo recipe submit docs/example-recipe-job.json
receipt-todo recipe list
receipt-todo recipe preview 2026-10-26-lunch-miso-salmon-v1
receipt-todo recipe print 2026-10-26-lunch-miso-salmon-v1
```

To print a second copy of an **active** job deliberately: `receipt-todo recipe print JOB_ID --force`. The private GitHub publisher can withdraw an earlier approved job with a higher weekly revision; withdrawn jobs cannot be printed/reprinted even with `--force` (nor requeued). Manual submitted jobs remain independent of the weekly manifest.

If the printer errors or the process is interrupted after claiming a job, the status is `needs_review` or `printing`. Inspect the printer **before** running `receipt-todo recipe requeue JOB_ID`, followed by `receipt-todo recipe print JOB_ID`. Receipt delivery cannot be exactly-once after ambiguous spooler failures.

## Contract

- `schema_version`: integer `1` (required)
- `job_id`: unique external idempotency key (required, letters/digits/dot/underscore/colon/hyphen, max 120 chars). Replays with the same ID and identical content are ignored; changed content must use a new ID.
- `date`: ISO calendar date `YYYY-MM-DD` (required)
- `meal`: `lunch` or `dinner` (required)
- `recipe`: required object containing `title` (string), `ingredients` (nonempty array of `{name, amount?}`), and `steps` (nonempty array of strings). Optional integer fields: `servings`, `prep_minutes`, `cook_minutes`.
- `source`: optional `{type, reference}` for provenance. Source-specific fields must **not** drive printing.

The `amount` field is intentionally human-readable (e.g. `"1 1/2 cups"`): upstream agents handle conversions; the printer doesn't do culinary reasoning. Names, steps, and metadata have size bounds enforced during ingestion.

Jobs are written into the existing SQLite file `data/tasks.db` in `recipe_print_jobs`. Recipe state never changes todo ranking or edits source data. Printing is Windows-only for now, as in the existing project; Linux can be a separate print-adapter project later.

## Later integration boundary

An approved weekly publication in the private GitHub inbox may group recipe jobs by Monday-start week; those rows have `inbox_week`, `inbox_active`, and `inbox_inactive_at` metadata to preserve print history across substitutions and cancellations. The [weekly inbox contract](github-print-inbox.md) defines revisions and safe removals. A future ChatGPT agent will fetch/modify recipes in the existing Notion household library; that integration is not required on the printer host. Grocery receipts and scheduled printing are separate follow-ups.

# ChatGPT household meal planning and approval-to-print workflow (#19)

**Purpose:** Use the household-specific MCP to maintain Notion Recipes and Weekly Meals, then prepare **reviewed immutable print snapshots** in the private GitHub inbox. This is an agent-facing operational checklist and household user guide. It does not authorize publishing by itself.

**Source-of-truth boundaries**

- **Working:** Household Notion's existing Recipes and Weekly Meals data sources, accessed only using the installed **Household MCP** tools. The regular connected Notion integration may point at another workspace; never use it as a substitute.
- **Published:** Private `dnaielfergy/household-print-inbox` `main` holds the latest explicitly approved weekly manifest and immutable job JSON.
- **Executed:** Windows `receipt-todo recipe sync` imports a pinned Git commit to SQLite. Importing **never prints**. Automatic recipe/grocery printing is out of scope until #20.
- Never put real household recipe content, grocery lists, account identifiers, credentials or approvals in the **public** `daily-todo-printer` repo. Keep actual snapshots and their review PRs in the **private** inbox.

## One-time manual setup: Notion recipe database template

**The public Notion API and current Household MCP tools can apply or create recipe page blocks, but do not create native database templates.** Perform the following once using the Notion UI in the existing **Recipes** database:

1. Open the database and select the small arrow next to **New** → **+ New template**. Name it `Recipe - structured`. Optionally make it the default template for manually created pages. Do not change existing recipes or their data source.
2. Under the page properties keep `Name` (title), `Servings` (positive base yield), `Recipe` (optional source URL), plus optional `Meal Type`, `Diet`, `Best For`, `Notes`, `Last Made`. Leave Servings unset in the template: each recipe must specify its own yield.
3. Add a **real Heading 2** block with exactly `Ingredients`. Directly below it add **one native simple table** with exactly **four columns** and a header row: `Ingredient | Quantity | Unit | Notes`. Leave ingredient cells blank as placeholders (or remove the blank row before filling).
4. Add a **real Heading 2** block named exactly `Instructions` with a native **numbered list**. Leave editable placeholder steps. An optional `Notes` heading and ordinary bullets can follow, outside the managed sections.
5. Create a temporary page using the new template, fill **Servings**, at least one valid ingredient row (name and quantity), and one numbered instruction. Verify `get_recipe` can read it before using it for a planned meal. Archive only the temporary page if desired.

Example header and ingredient style (fictional; do not paste a Markdown table in place of a native Notion table):

| Ingredient | Quantity | Unit | Notes |
| --- | --- | --- | --- |
| Example ingredient | 0.5 | cup | optional |

The recipe reader requires an exact `Ingredients` heading before the native 4-column table, an `Instructions` heading before numbered-list blocks, and positive `Servings`. A pasted table, plain paragraphs in place of headings, or an empty quantity will **not** pass. ChatGPT's `save_recipe` tool writes these native blocks directly on creation and can fill template skeletons or link-only entries; the Notion default template mainly improves **manual** authoring.

Weekly Meals use exactly `Meal` (title), **`Date`** (date), `Meal Type` (single-select: Breakfast/Lunch/Dinner), **`Meal Link`** (relation to one Recipes page), and optional `Servings` (number). The display-only `Recipe` rollup is never written. If a legacy `Day` date property exists, leave it untouched; `Date` is the canonical scheduling field. Breakfast may be included in Notion planning but the current recipe printer supports **lunch and dinner only**.

## A. Build the *working* week in Notion

1. Confirm the household week-start Monday (`YYYY-MM-DD`). Call `list_recipes`, following all `next_cursor` pages as needed. **Exclude `TEST ONLY` records**. `needs_preparation: false` only indicates a plausible base serving count; always call `get_recipe` to verify complete blocks before offering any recipe for publication.
2. Call `list_weekly_meals` with that Monday, following pagination. Show scheduled slots and missing/duplicate slots honestly. Do not fill a week with made-up meals; only propose meals selected by the user.
3. Use `save_recipe` for **all three** recipe operations: create a new recipe with complete native blocks (no `recipe_id`), populate a blank template or existing link-only page (existing `recipe_id`), or revise an existing structured recipe (existing `recipe_id`). The Notion API does not automatically apply the UI default template: `save_recipe` writes the complete Ingredients/Instructions blocks itself on create. For either existing-page operation, supply the **complete replacement** managed ingredients/instructions and `expected_last_edited_time` from `list_recipes` (for incomplete pages) or `get_recipe` (if complete); inspect source links/notes, and verify the full recipe with `get_recipe` after saving. Ambiguous/duplicate headings and malformed tables fail closed for manual review. Never invent quantities from inaccessible sources.
4. Use `save_weekly_meal` for requested changes and `remove_weekly_meal` to archive deliberate cancellations. These change **working Notion only** and do **not** approve, publish, or withdraw an older private inbox job.
5. Before proposing a publication, reread the actual week and every referenced recipe. A failed or incomplete read is a hard stop, not permission to fill in blanks.

## B. Prepare a human-readable approval preview

Present **all intended lunch/dinner slots in the complete approved set for the week**, including dates, recipe titles, serving counts, explicit cancellations, and any meals outside the current printer's scope. Show an itemized **categorized grocery list** with quantities and relevant notes, not merely totals or a link. State any uncertainties and ask the user to resolve them.

Calculation rules:

- Effective meal servings = Weekly Meals `Servings` when positive, else recipe `Servings`. Recipe scaling factor = effective / base. Multiply only clearly numeric ingredient quantities. Preserve unit strings and optionality.
- **Do not** guess a number for `to taste`, `as needed`, `1 can` without meaningful package assumptions, or other ambiguous text. Flag for approval. Retain specified units; combine like ingredients only with compatible dimensions/units and equivalent preparations. Never silently convert `each` and `lb`.
- Do not assume pantry inventory or exclude ingredients unless the user explicitly requests it. Identify optional ingredients and substitutions. Use clear shopping amounts and review conflicts.
- A one-meal week is valid only when it is explicitly presented as the **whole approved printable set** for that week.
- Breakfast may affect a grocery proposal if explicitly included, but it must never produce a recipe print job under the lunch/dinner v1 contract. State the grocery inclusion scope.

**Approval gate:** User must explicitly approve the **exact** week-start, complete printable menu (including intentional omitted slots), individual serving counts, categorized grocery quantities, and whether groceries are included. A Notion edit, a draft PR, and a generic "schedule this" are **not** publication approval. If the preview changes, ask for fresh approval. Never treat a previous week's approval as recurring authorization.

## C. Prepare the private GitHub publication **without publishing**

Using the existing connected GitHub app, inspect the **private** `dnaielfergy/household-print-inbox` repo's current `main` first; read `inbox.json` and this week's manifest if present. Do **not** place personal snapshot data in the public code repo.

1. Prepare a new `review/<week-start>-r<N>` branch from current private `main`. Do not write or merge into private `main` directly. When acting before approval, create only the **review PR**, clearly marked **UNAPPROVED / NOT FOR MERGE**.
2. For each selected **lunch/dinner** slot, create one **immutable** `weeks/<Monday>/jobs/<safe-unique-job-id>.json` matching [recipe-jobs.md](recipe-jobs.md):
   - `schema_version: 1`, unique `job_id` (ASCII letters/digits/dot/underscore/colon/hyphen, <=120 chars), `date`, `meal: lunch|dinner`;
   - `recipe: {title, servings, ingredients: [{name, amount?}], steps: [string]}`;
   - optional provenance `source: {type: "notion", reference: "<recipe-page-ID>"}`. Use the approved serving-adjusted quantities **in each immutable job**. Do not copy credentials.
3. If groceries are approved, create one `weeks/<Monday>/groceries/<safe-id>.json` matching [grocery-jobs.md](grocery-jobs.md): `schema_version:1`, globally unique `job_id`, `week_start`, `plan_revision:N`, and 1-12 named `groups` with approved `items: [{name, amount?, note?}]` (max 120 items), or a flat list. All quantities are approved strings.
4. Create/update `weeks/<Monday>/manifest.json` containing `schema_version:1`, `week_start`, `revision:N`, **the complete list of current approved job paths**, and `grocery` only when groceries are approved. Revision is `1` for an unpublished week; otherwise higher than the last published revision. Every changed payload needs a **new globally unique job_id and file**; never modify or repurpose historical job files.
5. Add/retain the manifest in root `inbox.json` (`schema_version:1`, at most 8 active weeks). Never silently drop another week; retain unrelated active manifests. Avoid duplicate entries and preserve proper JSON formatting.
6. Validate **all** JSON and external references, source recipe/servings, dates, distinct date+meal slots, safe paths/IDs, maximum 14 meal jobs/week, grocery `plan_revision` equals manifest revision, and printer-compatible text (CP437; no control sequences). Ensure no ref targets fictional examples under `examples/`, no duplicate IDs, and no credentials. Use pinned same-commit source reads for private repository review.
7. Open a PR against private `main`. Show user a final readable preview of the exact PR menu, grocery groups/quantities and diff. **Do not merge until the user explicitly approves that exact publication.** A draft PR is never an active inbox instruction.

**After approval:** revalidate the actual PR files against the approved preview and current `main`, ensure no conflict or concurrent new revision, then merge the complete publication. Verify the resulting private `main` `inbox.json` and manifest. This makes the snapshot available for Windows import but does **not** print.

## D. Import, revision and recovery

- On the Windows host (not through Household MCP), test: `receipt-todo recipe sync --config config.local.toml --preview`, then `receipt-todo recipe sync --config config.local.toml`. Only run the second command when explicitly intending to import; either is **import-only** and never prints. With automatic import already installed, verify the imported IDs through `receipt-todo recipe list`. Actual printing requires separate explicit local `recipe print` or `grocery print`.
- **Changed approval:** Re-read fresh Notion and private `main`, show a *complete* replacement weekly preview, increment `revision`, use new IDs for changed meal jobs **and every revised grocery job**, update the full manifest, open a new review PR and secure fresh approval. Omitted previous slots become withdrawn on import, not deleted from history; previously printed history stays immutable.
- **Withdraw whole week:** publish a freshly approved higher-revision manifest with `jobs: []`, **no `grocery`**, and keep it indexed for the host to reconcile. Removing the week from root `inbox.json` is *not* a cancellation.
- **Failure:** Never merge a partially staged publication or "fix" errors by mutating old immutable job JSON; stop, examine schema/revision errors, and re-review. Do not infer actual paper output from a successful spooler return.

## Live acceptance gates

- [x] Household MCP OAuth + scoped read tools worked in ChatGPT.
- [x] Egg Roll in a Bowl's native 4-column table and numbered instructions read end-to-end after adding the `Ingredients` heading.
- [x] Fictional recipe create/edit/readback + stale and duplicate rejection.
- [x] Fictional meal create/serving update/duplicate rejection/archive after fixing `Meal Link` capitalization.
- [ ] Native Recipes database template configured by a human in Notion UI.
- [ ] User explicitly reviews and approves a **real** complete menu and groceries for a week.
- [ ] Private GitHub review PR is validated and merged **after** approval.
- [ ] Windows host imports the approved pinned publication; zero automatic printing.

See [github-print-inbox.md](github-print-inbox.md), [recipe-jobs.md](recipe-jobs.md), [grocery-jobs.md](grocery-jobs.md), and [issue #19](https://github.com/dnaielfergy/daily-todo-printer/issues/19).

# Reproducing the Notion recipe MCP safely

This is a **reusable implementation guide**, not a record of a particular household's deployment. Keep real workspace names, Notion IDs, credentials, Access application configuration, custom hostnames, household data and account emails **outside this public repository**.

The reference implementation is in [`integrations/household-notion-mcp/`](../integrations/household-notion-mcp/). It uses Notion's `2025-09-03` API version, a Cloudflare Worker protected by Zero Trust Access, and six narrowly scoped MCP tools. The local Windows printer **never connects to Notion**; it imports separately approved snapshots from a private GitHub inbox.

## 1. Create the two Notion data sources

Use **two data sources in one household Notion workspace**. Match property names and types exactly: property capitalization is significant. Obtain each **data source ID** from the Notion API/database metadata; a database/view URL or page ID is not a substitute. Never commit IDs or paste them into a public ticket.

### Recipes

| Property | Notion type | Behavior |
| --- | --- | --- |
| `Name` | Title | Required recipe title |
| `Servings` | Number | Base recipe yield, required for a completed recipe |
| `Recipe` | URL | Optional original source link; **not** the Weekly Meals rollup |
| `Meal Type` | Multi-select | Optional recipe classification |
| `Diet` | Multi-select | Optional |
| `Best For` | Multi-select | Optional |
| `Notes` | Rich text | Optional; omitted writes preserve existing value |
| `Last Made` | Existing property, if present | Not modified by the MCP |

Each recipe page also has **native page blocks** (not database properties):

1. Heading **Ingredients**.
2. Native **simple table** with exactly four columns: `Ingredient`, `Quantity`, `Unit`, `Notes`. The first row contains those labels. Following rows contain one ingredient each; `Quantity` is required text, not a numeric Notion property. Preserve units and preparation notes separately.
3. Heading **Instructions**.
4. Native **numbered list** with one cooking step per list item.
5. Optional **Notes** heading and human annotations, outside the two managed sections.

For manual authoring, create a **default database template** called `Recipe - Structured` with those headings, an empty four-column native table, and a placeholder numbered instruction. Replace all placeholders and set Servings before using it. A table pasted as Markdown or an embedded database is not equivalent to the native four-column simple table.

**API-created recipes are different:** Notion does not automatically apply the UI's default database template to API-created pages. The implementation sends complete native blocks as the `children` of the new page, so `save_recipe` creates a complete recipe immediately rather than relying on asynchronous template application.

### Weekly Meals

| Property | Notion type | Behavior |
| --- | --- | --- |
| `Meal` | Title | Human-readable assignment title |
| `Date` | Date | **Canonical scheduling date** |
| `Meal Type` | Select | `Breakfast`, `Lunch`, or `Dinner` |
| `Meal Link` | Relation to Recipes | Exactly **one** linked recipe per entry (capital **L**) |
| `Servings` | Number | Optional override; otherwise use the recipe's base Servings |
| `Recipe` | Rollup, if present | **Read-only display** through `Meal Link`; do not write this |
| `Day` | Date, if present | Legacy/additional date; **ignored** by the MCP |

The writer enforces one weekly assignment per `Date` + `Meal Type` slot. Breakfast may be planned but the current print-job contract emits only lunch/dinner receipts.

## 2. Connect a narrowly scoped Notion integration

1. Create a **dedicated Notion integration**, separate from any studio/workspace automation.
2. Grant only the needed capabilities: read content initially; add insert/update content for controlled working-state writes.
3. Connect the integration to **only the Recipes and Weekly Meals data sources**. Do not grant workspace-wide access or unrelated databases.
4. Obtain the integration token and both data source IDs privately. Store the token as a **Cloudflare secret** and the IDs as private deployment bindings/secrets, not in source control.
5. Confirm that the integration can query both data sources and retrieve a recipe's page blocks. Test against fictional or explicitly approved records, never bulk-edit real recipes as the first test.

## 3. Deploy the Worker behind a private identity gate

Run locally from `integrations/household-notion-mcp/`:

```sh
npm install
npm test
npm run check
```

The Wrangler configuration intentionally sets `workers_dev: false`; it includes **no real account ID, custom domain or hostname**. Configure the private hostname, route and Cloudflare Zero Trust Access application **outside this repository**, before enabling production access.

Supply these binding names privately in the Cloudflare deployment:

| Binding | Purpose |
| --- | --- |
| `NOTION_TOKEN` | Dedicated Notion integration token (**secret**) |
| `NOTION_RECIPES_DATA_SOURCE_ID` | Allowed Recipes data source ID |
| `NOTION_MEALS_DATA_SOURCE_ID` | Allowed Weekly Meals data source ID |
| `ACCESS_TEAM_DOMAIN` | Private Cloudflare Access team domain |
| `ACCESS_AUD` | Audience for the specific protected Access application |
| `ACCESS_ALLOWED_EMAIL` | Only identity permitted to use the MCP |
| `ENABLE_NOTION_WRITES` | Leave unset or `false` until read-only QA passes; use literal `true` to enable writes |

The Worker requires a signed Cloudflare Access assertion; it verifies its issuer, audience, RS256 signature, expiry and allowed email. The Access application must also authenticate the MCP client. A public Worker URL or unprotected route is **not** an acceptable shortcut; authorization must fail closed. Configure MCP/OAuth client access privately, then test both the permitted identity and an unauthorized identity.

## 4. Connect and verify in ChatGPT

Enable the protected MCP connection in the intended ChatGPT workspace. Use **only** the dedicated household connector for household recipes; another connected Notion integration may point to an unrelated workspace.

The expected tools are:

| MCP tool | Purpose |
| --- | --- |
| `list_recipes` | Read titles, IDs, base servings, source links and edit timestamps; follow pagination |
| `get_recipe` | Read and strictly validate the native ingredient table and numbered steps |
| `list_weekly_meals` | Read a Monday-start week and its relation/serving overrides |
| `save_recipe` | Create, populate or revise a recipe's managed blocks/metadata |
| `save_weekly_meal` | Create or update a dated assignment; reject duplicate date/type slots |
| `remove_weekly_meal` | Archive an authorized assignment after timestamp check |

With `ENABLE_NOTION_WRITES` disabled, **only the three read tools** should be registered.

### Recipe acceptance tests

Use obviously fictional disposable pages and verify every write by rereading it:

1. **Create:** call `save_recipe` without `recipe_id`. Confirm one native ingredient table, numbered instructions, positive Servings and the correct source data source.
2. **Populate a link-only page:** start with an existing recipe title/source URL but no managed blocks. Use its `recipe_id` and latest `expected_last_edited_time` from `list_recipes`; fill the complete structured content. Preserve existing human notes.
3. **Populate a blank template:** start with headings, table headers/empty rows and placeholder instructions. Fill those blocks **in place** without adding duplicate headings or tables.
4. **Revise:** for a complete recipe, reread `get_recipe`, pass the latest edit timestamp and **full replacement** ingredients/instructions. Test both adding and removing rows/steps. Verify read-back and untouched notes.
5. Check rejection of stale edit timestamps, malformed tables, duplicate headings and out-of-scope page IDs. A placeholder instruction is **not** print-ready.
6. Create, update and archive a fictional Weekly Meals slot. Confirm `Date` and `Meal Link` (not `Day` or the `Recipe` rollup), and rejection of duplicate slots.

The MCP cannot archive recipes via its ordinary `save_recipe` tool. Remove disposable recipe fixtures manually in Notion or through a tightly scoped QA-only operation; never expose a general delete tool merely for test cleanup. Notion block writes are non-transactional: inspect and repair a partially applied edit rather than blindly retrying.

## 5. Keep the publication boundary separate

**Notion = editable working source.** ChatGPT may research, write and revise recipes and draft weekly assignments.

**Private GitHub inbox = explicitly approved publication.** Before opening a publication PR, show the entire intended week (dates, lunch/dinner slots and deliberate omissions), servings, and every categorized grocery amount. Do **not** subtract a guessed pantry inventory; the initial grocery list is the full requirement list for humans to cross off on-hand items. A draft PR is not approval.

**Windows SQLite = accepted print queue.** The importer reads one immutable private GitHub commit, validates the whole snapshot, and can use `--preview` and an isolated `--db` for premerge testing. Importing does not print. Physical receipts require a separate explicit command; scheduled automatic printing is out of scope.

See [the approval playbook](chatgpt-household-meals.md), [inbox contract](github-print-inbox.md), [recipe schema](recipe-jobs.md), and [grocery schema](grocery-jobs.md).

## 6. Before any public push

- Keep actual Notion URLs/IDs, private hostname and Access configuration, tokens, household names, identifying emails, recipe content and printer databases **out of tracked files, issues, PR descriptions and screenshots**.
- Put credentials only in private deployment secrets or local ignored configuration. The root `.gitignore` excludes `config.*.toml`, except the fictional `config.example.toml`. The integration excludes `.dev.vars*`, `.env*` and Wrangler local output.
- Use synthetic IDs, example domains and fictional ingredient data in tests. Never paste real secrets into a test to make it pass.
- Review the **staged diff** before pushing. A CI check after pushing a secret is too late to guarantee confidentiality.
- Configure future Git commits to use a GitHub-provided no-reply email where possible. Changing files does **not** remove historical commit metadata or prior public PR content.
- If any credential is exposed, **revoke/rotate it** before undertaking source/history cleanup. Git history rewrites are disruptive and cannot promise erasure from forks or caches.

## Troubleshooting

- **Notion HTTP 400 on Weekly Meals:** verify exact property names/types; `Meal Link` is a relation with uppercase `L`, `Date` is canonical, and `Recipe` is a read-only rollup.
- **Link-only recipe cannot be read with `get_recipe`:** expected; obtain ID and edit timestamp via `list_recipes`, then populate with `save_recipe`.
- **Recipe not print-ready:** inspect native four-column table, populated quantity cells, numbered instructions, and positive base Servings; remove the default instruction placeholder.
- **403/401 from the MCP:** check the Access policy, app audience, signed assertion, permitted identity and correct OAuth connection, not merely the Notion token.
- **Stale edit error:** reread the page and reconcile human edits before resubmitting. Do not override concurrent changes.
- **Notion quota, HTTP errors or partial write:** stop, review the page, and retry only after confirming the actual saved state.

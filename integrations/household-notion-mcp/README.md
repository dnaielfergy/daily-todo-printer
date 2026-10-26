# Household Notion MCP — #19

**Deployed on an Access-protected hostname for live acceptance; PR #23 remains a draft.** This Cloudflare Worker connects ChatGPT to the existing household Notion Recipes and Weekly Meals databases. It exposes **three read tools** and three working-state write tools gated by the `ENABLE_NOTION_WRITES=true` runtime binding. The live read/write fictional-data acceptance checks passed; the publishing approval gate remains separate.

The editable Notion plan and the approved GitHub print snapshots are independent. Writing a meal assignment or recipe **does not publish, print, or cancel an approved grocery/meal receipt**. Publishing requires an explicit review and approval through the private GitHub inbox workflow; see [the ChatGPT household meals playbook](../../docs/chatgpt-household-meals.md).

## Approved Notion schema

Recipes properties: Name (title), Servings (number, base recipe yield), Recipe (source URL), optional Meal Type / Diet / Best For (multi-select), Notes (rich text), and existing Last Made (not modified by the writer).

Recipe page content:
1. Native **Ingredients** heading followed by **one native Notion simple table**, exactly four columns: Ingredient, Quantity, Unit, Notes.
2. Native **Instructions** heading followed by numbered list items.
3. Other page notes may remain outside the managed sections.

Quantities are explicit text strings, such as "0.25", "2", or "to taste". Grocery calculations, incompatible-unit checks, and any ingredient normalization belong in ChatGPT, *not* in the Worker or printer.

Weekly Meals properties: Meal (title), Date (date), Meal Type (select: Breakfast/Lunch/Dinner), Meal Link (relation to exactly one Recipes page), optional Servings (number). The existing Recipe rollup is display-only. If the database also has a `Day` date property, the MCP ignores it and uses `Date` for scheduling. Breakfast is valid for planning but not printable with the current lunch/dinner snapshot contract.

## MCP tools

| Tool | Purpose |
| --- | --- |
| list_recipes | Read recipe metadata, preparation status and edit timestamps |
| get_recipe | Read and validate a recipe's simple ingredient table and numbered steps |
| list_weekly_meals | Read dated weekly assignments, serving overrides, recipe IDs and edit timestamps |
| save_recipe | **Writes enabled only:** create or revise complete recipe contents and selected metadata |
| save_weekly_meal | **Writes enabled only:** create or revise a dated recipe assignment; duplicate slots rejected |
| remove_weekly_meal | **Writes enabled only:** archive one authorized dated assignment |

For a revision, supply the page ID and the **expected last_edited_time** returned by the read tool, plus the complete intended content. This refuses most stale overwrites and guards page identity. Recipe edits update native ingredient rows and numbered instructions in place and preserve manually authored notes elsewhere on the page.

Notion block writes are **not transactional**. If an update fails halfway through, inspect the recipe and repair it before retrying. The writer verifies recipe edits with a complete read-back; an edit timestamp alone is not a database-level concurrency lock.

Omitted optional source URL, tags, or Notes fields on recipe edits are preserved. Explicit empty tags clear a multi-select; explicit null URL clears the original link. Existing Last Made values are never changed by the writer.

## Security and deployment

- The dedicated household Notion integration token must be kept as a Cloudflare **secret**, not in GitHub or Windows. Grant connection access **only** to the two household data sources. When writes are enabled it needs read, insert, and update content capabilities; never connect the studio workspace.
- Use a dedicated protected custom hostname with Cloudflare Zero Trust Access and MCP OAuth, restricted to your chosen account. The Worker also verifies the signed Cloudflare Access JWT (issuer, audience, RS256, expiry, one allowed email).
- workers_dev is disabled. No route or public Worker deployment is created by this branch. The Worker denies unauthenticated requests and never fetches arbitrary Notion URLs or modifies other databases.
- Write tools are registered **only** if the environment binding ENABLE_NOTION_WRITES is exactly "true". The default is read-only, even if the Notion integration token has write permission.
- No MCP tool can publish to GitHub, command the local printer, or bypass the human approval gate.

Required protected configuration (never commit actual values):
- NOTION_TOKEN
- NOTION_RECIPES_DATA_SOURCE_ID
- NOTION_MEALS_DATA_SOURCE_ID
- ACCESS_TEAM_DOMAIN (e.g. team.cloudflareaccess.com)
- ACCESS_AUD (Cloudflare Access app audience)
- ACCESS_ALLOWED_EMAIL (one authorized email)
- ENABLE_NOTION_WRITES (omit/false until read + OAuth tests pass)

## Build and test

From integrations/household-notion-mcp, run:
- npm install
- npm test
- npm run check

The third command is a **Wrangler dry-run bundle check**, not a deployment. Tests contain only fictional recipes and mock Notion responses.

## Acceptance gates

1. Create a dedicated household Notion integration and connect **only Recipes and Weekly Meals**; capture each **data source ID**, not its database/view URL. Validate exact live property types/names before deployment.
2. Configure a Cloudflare Zero Trust Access-protected hostname and test OAuth from **the actual ChatGPT workspace**.
3. With writes disabled, verify that the only three available MCP tools are read-only. Read back the approved egg-roll-bowl recipe containing coconut aminos and its four-column ingredient table, numbered steps, and servings.
4. Enable ENABLE_NOTION_WRITES=true only when the connection and exact schema are verified. Use a **fictional recipe and meal** for first write tests, including recipe revision, serving override, duplicate-slot denial, and meal archive. Verify every result via read-back and confirm out-of-scope IDs are rejected.
5. Test wrong-user, expired, or revoked Access tokens and turning the write switch back off. Use Notion page history to recover from any interrupted multi-block update.
6. In a separate #19 phase, add the ChatGPT **explicit human approval → private GitHub snapshot publication** workflow. Do not enable automatic printing until #20.

## Rollback

Set ENABLE_NOTION_WRITES=false, revoke or rotate Notion credentials, and/or disable the Cloudflare Access app. No production Windows, SQLite, or receipt schedule modifications are needed.

**Live acceptance:** Cloudflare Worker and OAuth connection were deployed; a real reference recipe was read without editing it. A clearly labeled fictional recipe was created/edited and a fictional weekly meal created/updated/archived. No live Windows printing or GitHub publication was performed; do not treat a Notion write as publication approval. The fictional recipe remains clearly labeled for user cleanup.

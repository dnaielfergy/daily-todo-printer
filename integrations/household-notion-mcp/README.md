# Household Notion MCP — #19 read-only first pass

**Status: review-only implementation. Do not deploy before Cloudflare Access, Notion integration scope, and real ChatGPT connection are tested.**

A small **stateless, read-only** Cloudflare Worker MCP server for the existing household Notion recipe and meal-plan data sources. It does not touch Windows, the Telegram bot, production SQLite, or the private GitHub print inbox.

## Approved Notion schema

Recipes data source properties:

- Name — title
- Recipe — source URL (optional for a complete stored recipe)
- Servings — number (original/base yield)
- Existing Meal Type, Diet, Best For, Notes, Last Made remain unchanged.

A complete recipe's **page body** contains:

1. \`Ingredients\` heading and one **native Notion simple table** with four columns \`Ingredient | Quantity | Unit | Notes\`, then one row per ingredient.
2. \`Instructions\` heading and **numbered list** blocks.

Ingredient rows represent precise source quantities, e.g. \`Coconut aminos | 0.25 | cup | Soy sauce replacement\`. The Worker extracts strings; it does **not** calculate units, scrape recipe sites, or aggregate groceries.

Weekly Meals data source properties:

- Meal — title
- Date — date, one actual calendar date per row
- Meal Type — select: Breakfast, Lunch, Dinner
- Meal link — relation to Recipes, exactly one recipe per assignment
- Servings — number, optional meal-specific override (blank = base recipe yield).
- The existing Recipe rollup is display-only and not used for writes.

Breakfast can be planned, but #17/#18 print snapshots currently support only lunch/dinner.

## Tools

| MCP tool | Input | Output |
| --- | --- | --- |
| \`list_recipes\` | optional \`limit\`, \`cursor\` | Recipe titles, IDs, serving yields, source URLs, whether preparation is needed, pagination cursor |
| \`get_recipe\` | \`recipe_id\` | One recipe from the allowlisted data source, validated ingredients and numbered steps |
| \`list_weekly_meals\` | \`week_start\` (ISO Monday), optional \`cursor\` | Dated meal assignments, recipe IDs, serving overrides, printability indicator |

All tools are read-only. No generic Notion URL fetch, database enumeration, arbitrary search or edit operations. Every returned recipe page is verified to belong to the configured **recipe data source**, not merely be readable by the Notion token.

A link-only recipe remains listable with \`needs_preparation: true\` until its base serving yield and native block content are prepared. Reading an incomplete recipe fails closed rather than guessing ingredients.

## Deployment prerequisites (manual; no secrets in GitHub)

1. **Notion:** In the household Notion workspace, create a dedicated internal integration with read access. Grant its connection **only** to the Recipes and Weekly Meals databases and the test recipe page. Do not grant access to the studio workspace or broader household parent pages. Capture the actual **data source IDs** (not the database page URLs/view IDs). Keep the integration token out of GitHub and chat.
2. **Cloudflare Zero Trust:** Configure a dedicated **self-hosted Access application** on a hostname you control, with MCP OAuth support, limited to your chosen identity/email. Deny other accounts. Set a custom Worker domain/route behind Access, and keep the \`workers.dev\` endpoint disabled.
3. **Access + Worker config:** Define these Wrangler secrets or protected environment variables:
   - \`NOTION_TOKEN\` — dedicated Notion integration token (never in git)
   - \`NOTION_RECIPES_DATA_SOURCE_ID\` — actual Recipes data source ID
   - \`NOTION_MEALS_DATA_SOURCE_ID\` — actual Weekly Meals data source ID
   - \`ACCESS_TEAM_DOMAIN\` — Cloudflare Access team hostname, e.g. \`team.cloudflareaccess.com\`
   - \`ACCESS_AUD\` — Access application audience/tag
   - \`ACCESS_ALLOWED_EMAIL\` — one exact authorized email; configure the same restriction in Access.
4. **Cloudflare Access JWT:** Access must authenticate the MCP client's OAuth token and insert a \`Cf-Access-Jwt-Assertion\` header. The Worker independently verifies its RS256 signature against the Access JWKS, issuer, audience, expiration, and exact email. The Worker fails closed (503/401/403) if misconfigured, missing, invalid, or from another identity. **Never route around Access**.
5. **ChatGPT:** An authorized workspace admin/owner opens Plugins → Add custom MCP server, enters the protected HTTPS \`/mcp\` URL, completes OAuth, and verifies the tool list (3 read-only tools). If a different UI is shown, use the workspace's app/plugin creation flow. Do not publish to other members before reviewing permissions.

Current connected ChatGPT Notion tools return \`object_not_found\` for the private household workspace: live Notion integration access **has not** been verified yet. Connect and smoke-test **one real example recipe** before treating this as production-ready.

## Development

\`\`\`sh
cd integrations/household-notion-mcp
npm install
npm test
npm run check
\`\`\`

\`npm run check\` builds via Wrangler dry-run only; it does not deploy. The current CI uses synthetic example ingredients and stubbed Notion responses, not real household content.

When credentials and Access are configured:

\`\`\`sh
npx wrangler secret put NOTION_TOKEN
npx wrangler secret put NOTION_RECIPES_DATA_SOURCE_ID
npx wrangler secret put NOTION_MEALS_DATA_SOURCE_ID
npx wrangler secret put ACCESS_TEAM_DOMAIN
npx wrangler secret put ACCESS_AUD
npx wrangler secret put ACCESS_ALLOWED_EMAIL
npx wrangler deploy
\`\`\`

The Wrangler configuration deliberately disables \`workers.dev\` and declares **no custom route or domain** yet. Attach the protected hostname/Access application explicitly and review it before attempting live deployment. Do **not** publicly expose the \`/mcp\` endpoint for debugging.

## Release gates

- [ ] ChatGPT custom MCP availability and OAuth handshake confirmed in the actual workspace.
- [ ] Dedicated Notion integration read scope verified (only two household data sources).
- [ ] Access OAuth login and signed assertion tested in front of the correct Worker hostname, including wrong-user denials.
- [ ] Live \`list_recipes\` and \`get_recipe\` return the approved coconut-aminos example, not other workspace pages.
- [ ] Live \`list_weekly_meals\` reads dated relation and optional serving override correctly.
- [ ] Review PR, test revocation/expired token, verify malformed rows are rejected.
- [ ] Add **write** operations and explicit approval/publish workflow as a separate #19 PR; do not enable grocery/meal auto-printing (#20).

There is **no production deployment** associated with this first-pass implementation. The existing private GitHub publication contract and Windows print history remain unchanged.

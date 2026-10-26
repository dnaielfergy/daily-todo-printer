import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
const root = join(import.meta.dirname, "..");
const plugin = JSON.parse(readFileSync(join(root,"plugin.json"),"utf8"));
test("portable plugin manifest references registered existing app, with bundled skill", () => {
  assert.equal(plugin.$schema, "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json");
  assert.equal(plugin.extensions["com.openai"].apps,"./.app.json");
  const base=join(root,"skills/household-meal-planning");
  for (const p of ["SKILL.md","agents/openai.yaml","references/recipes.md","references/meal-planning.md","references/groceries.md","references/publication.md"]) assert.ok(existsSync(join(base,p)),p);
  assert.ok(!existsSync(join(root,"mcp.json")),"Do not provision a second remote OAuth MCP connection");
});
test("private registered connection mapping is never committed", () => {
  const ignore = readFileSync(join(root,".gitignore"),"utf8");
  assert.match(ignore,/^\.app\.json$/m);
  assert.ok(!existsSync(join(root,".app.json")),"Registered MCP connection mapping is supplied only to private plugin package");
});

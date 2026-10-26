import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dirname, "..");
const skill = join(root, "skills/household-meal-planning");
const read = (name) => readFileSync(join(skill, name), "utf8");

test("skill frontmatter, metadata and all references are packaged", () => {
  const entry = read("SKILL.md");
  assert.match(entry, /^---\nname: household-meal-planning\ndescription: .+\n---\n/);
  assert.match(read("agents/openai.yaml"), /display_name: "Household Meal Planning"/);
  for (const name of ["recipes", "meal-planning", "groceries", "publication"]) {
    assert.ok(existsSync(join(skill, "references", name + ".md")));
    assert.match(entry, new RegExp("references/" + name + "\\.md"));
  }
});
test("private policy never appears in package, policy is mandatory, publication is gated", () => {
  const files = ["SKILL.md", ...["recipes","meal-planning","groceries","publication"].map(x => "references/" + x + ".md")];
  const text = files.map(read).join("\n");
  assert.match(text, /policies\/household\.md/);
  assert.match(text, /fail closed|stop policy-dependent work/i);
  assert.match(text, /do not merge/i);
  assert.match(text, /Never.*print|Do not.*print/i);
  assert.doesNotMatch(text, /@example-private-domain\\.test|PRIVATE_POLICY_CONTENT_MARKER/i);
});
test("fictional recipe, servings and meal behavior are explicitly covered", () => {
  assert.match(read("references/meal-planning.md"), /each linked recipe/);
  assert.match(read("references/meal-planning.md"), /leftovers/i);
  assert.match(read("references/groceries.md"), /not.*recipe rows/i);
  assert.match(read("references/groceries.md"), /similarly named/i);
});
test("MCP retains guarded writes and server instructions", () => {
  const code = readFileSync(join(root,"src/index.js"), "utf8");
  assert.match(code, /instructions:/);
  assert.match(code, /ENABLE_NOTION_WRITES === "true"/);
  assert.doesNotMatch(code, /get_household_policy/);
});
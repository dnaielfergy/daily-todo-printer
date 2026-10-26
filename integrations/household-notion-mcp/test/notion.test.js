import test from "node:test";
import assert from "node:assert/strict";
import {
  assertId, recipeSummary, parseRecipeBlocks, weeklyMeal, weekDates, createNotion
} from "../src/notion.js";

const uuid = (n) => (String(n).padStart(8, "0") + "-1234-4234-8234-123456789abc");
const rich = (text) => [{ plain_text: text }];
const heading = (id, text) => ({ id, type: "heading_2", heading_2: { rich_text: rich(text) } });
const row = (id, cells) => ({
  id, type: "table_row", table_row: { cells: cells.map(rich) }
});
const pages = {
  recipe: {
    id: uuid(3),
    parent: { type: "data_source_id", data_source_id: uuid(1) },
    properties: {
      Name: { type: "title", title: rich("Keto Egg Roll in a Bowl") },
      Servings: { type: "number", number: 4 },
      Recipe: { type: "url", url: "https://example.com/recipe" }
    }
  },
  meal: {
    id: uuid(4),
    properties: {
      Date: { type: "date", date: { start: "2026-11-02" } },
      Day: { type: "date", date: { start: "2026-11-05" } }, // Independent legacy date must not drive scheduling
      "Meal Type": { type: "select", select: { name: "Dinner" } },
      "Meal Link": { type: "relation", relation: [{ id: uuid(3) }] },
      Servings: { type: "number", number: 2 }
    }
  }
};

function fixtureBlocks() {
  return [
    heading(uuid(5), "Ingredients"),
    { id: uuid(6), type: "table", table: { table_width: 4 } },
    heading(uuid(7), "Instructions"),
    {
      id: uuid(8),
      type: "numbered_list_item",
      numbered_list_item: { rich_text: rich("Brown ground pork.") }
    }
  ];
}
function fixtureRows() {
  return {
    [uuid(6)]: [
      row(uuid(9), ["Ingredient", "Quantity", "Unit", "Notes"]),
      row(uuid(10), ["Sesame oil", "1", "tbsp", ""]),
      row(uuid(11), ["Onion", "0.5", "each", "Large, diced"]),
      row(uuid(12), ["Ground pork", "1", "lb", "Lean"]),
      row(uuid(13), ["Coconut aminos", "0.25", "cup", "Soy replacement"])
    ]
  };
}

test("recipe template extracts four separate grocery fields and steps", () => {
  const content = parseRecipeBlocks(fixtureBlocks(), fixtureRows());
  assert.equal(content.ingredients.length, 4);
  assert.deepEqual(content.ingredients[3], {
    ingredient: "Coconut aminos", quantity: "0.25",
    unit: "cup", notes: "Soy replacement"
  });
  assert.deepEqual(content.instructions, ["Brown ground pork."]);
});

test("recipe metadata and incomplete legacy recipe list", () => {
  const full = recipeSummary(pages.recipe);
  assert.equal(full.servings, 4);
  assert.equal(full.needs_preparation, false);
  const missing = structuredClone(pages.recipe);
  missing.properties.Servings.number = null;
  assert.equal(recipeSummary(missing).needs_preparation, true);
  assert.throws(() => recipeSummary(missing, true), /valid base Servings/);
});

test("malformed tables are rejected rather than guessed", () => {
  const empty = fixtureBlocks().filter((block) => block.type !== "table");
  assert.throws(() => parseRecipeBlocks(empty, fixtureRows()), /print-ready/);
  const bad = fixtureRows();
  bad[uuid(6)][1].table_row.cells[1] = rich("");
  assert.throws(() => parseRecipeBlocks(fixtureBlocks(), bad), /Incomplete/);
});

test("weekly meal relation and printability are deterministic", () => {
  assert.deepEqual(weeklyMeal(pages.meal), {
    id: uuid(4), date: "2026-11-02", meal_type: "Dinner",
    recipe_id: uuid(3), servings: 2, printable: true
  });
  const breakfast = structuredClone(pages.meal);
  breakfast.properties["Meal Type"].select.name = "Breakfast";
  assert.equal(weeklyMeal(breakfast).printable, false);
  breakfast.properties["Meal Link"].relation = [];
  assert.throws(() => weeklyMeal(breakfast), /one Meal Link/);
});

test("week start must be a real Monday", () => {
  assert.deepEqual(weekDates("2026-11-02"), { start: "2026-11-02", end: "2026-11-08" });
  assert.throws(() => weekDates("2026-11-03"), /Monday/);
  assert.throws(() => weekDates("2026-02-30"), /Monday/);
  assert.throws(() => assertId("../private"), /Invalid/);
});

test("Notion client uses only allowlisted sources and pages", async () => {
  const requests = [];
  const fakeFetch = async (url, opts) => {
    requests.push({ url, opts });
    const path = new URL(url).pathname;
    if (path.includes("/data_sources/")) {
      return Response.json({ results: [pages.recipe], has_more: false });
    }
    if (path.endsWith("/pages/" + assertId(uuid(3)))) {
      return Response.json(pages.recipe);
    }
    if (path.endsWith("/blocks/" + assertId(uuid(3)) + "/children")) {
      return Response.json({ results: fixtureBlocks(), has_more: false });
    }
    if (path.endsWith("/blocks/" + assertId(uuid(6)) + "/children")) {
      return Response.json({ results: fixtureRows()[uuid(6)], has_more: false });
    }
    throw new Error("Unexpected URL " + url);
  };
  const client = createNotion({
    NOTION_TOKEN: "fixture-token", NOTION_RECIPES_DATA_SOURCE_ID: uuid(1),
    NOTION_MEALS_DATA_SOURCE_ID: uuid(2)
  }, fakeFetch);
  const data = await client.getRecipe(uuid(3));
  assert.equal(data.ingredients[2].ingredient, "Ground pork");
  assert.equal(data.servings, 4);
  assert(requests.every(({ url }) => new URL(url).host === "api.notion.com"));
  assert(requests.every(({ opts }) => opts.headers["Notion-Version"] === "2025-09-03"));
  assert(requests.every(({ opts }) => opts.method === "GET"));

  const outside = createNotion({
    NOTION_TOKEN: "fixture-token", NOTION_RECIPES_DATA_SOURCE_ID: uuid(7),
    NOTION_MEALS_DATA_SOURCE_ID: uuid(2)
  }, fakeFetch);
  await assert.rejects(() => outside.getRecipe(uuid(3)), /does not belong/);
});

test("Notion errors do not echo upstream private content", async () => {
  const client = createNotion({
    NOTION_TOKEN: "secret", NOTION_RECIPES_DATA_SOURCE_ID: uuid(1),
    NOTION_MEALS_DATA_SOURCE_ID: uuid(2)
  }, async () => new Response("Private recipe ingredients", { status: 403 }));
  await assert.rejects(() => client.listRecipes(), (err) =>
    /HTTP 403/.test(err.message) && !err.message.includes("Private"));
});

test("Notion response pagination rejects an incomplete next cursor", async () => {
  const client = createNotion({
    NOTION_TOKEN: "fixture", NOTION_RECIPES_DATA_SOURCE_ID: uuid(1),
    NOTION_MEALS_DATA_SOURCE_ID: uuid(2)
  }, async (_url, _opts) => Response.json({ results: [pages.recipe], has_more: true, next_cursor: null }));
  const result = await client.listRecipes();
  assert.equal(result.next_cursor, null);
});

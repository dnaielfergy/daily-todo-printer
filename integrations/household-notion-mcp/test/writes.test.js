import test from "node:test";
import assert from "node:assert/strict";
import {
  validateRecipe, validateMeal, recipeBlocks, createNotionWriter
} from "../src/writes.js";

const id = (n) => String(n).padStart(8, "0") + "-1234-4234-8234-123456789abc";
const normal = (uuid) => uuid.replaceAll("-", "");
const rich = (text) => [{ plain_text: text }];
const env = {
  ENABLE_NOTION_WRITES: "true",
  NOTION_TOKEN: "synthetic-test-token",
  NOTION_RECIPES_DATA_SOURCE_ID: id(1),
  NOTION_MEALS_DATA_SOURCE_ID: id(2)
};
const payload = () => ({
  name: "Fictional Bowl",
  servings: 4,
  source_url: "https://example.com/fictional-bowl",
  ingredients: [
    { ingredient: "Coconut aminos", quantity: "0.25", unit: "cup", notes: "" },
    { ingredient: "Onion", quantity: "0.5", unit: "each", notes: "Diced" }
  ],
  instructions: ["Cook onion.", "Serve."]
});

function createFixture() {
  const calls = [];
  const pageId = id(3);
  const mealId = id(4);
  const recipeSource = normal(id(1));
  const mealsSource = normal(id(2));
  const responsePage = (parent, page) => ({
    id: page, url: "https://notion.so/test-" + normal(page),
    last_edited_time: "2026-10-25T20:00:00.000Z",
    parent: { data_source_id: parent }
  });
  const recipePage = {
    ...responsePage(recipeSource, pageId),
    properties: {
      Name: { type: "title", title: rich("Fictional Bowl") },
      Servings: { type: "number", number: 4 },
      Recipe: { type: "url", url: "https://example.com/fictional-bowl" }
    }
  };
  const blocks = [
    { id: id(7), type: "heading_2", heading_2: { rich_text: rich("Ingredients") } },
    { id: id(8), type: "table", table: { table_width: 4 } },
    { id: id(9), type: "heading_2", heading_2: { rich_text: rich("Instructions") } },
    { id: id(10), type: "numbered_list_item", numbered_list_item: { rich_text: rich("Cook onion.") } },
    { id: id(11), type: "numbered_list_item", numbered_list_item: { rich_text: rich("Serve.") } }
  ];
  const rows = [
    { id: id(12), type: "table_row", table_row: { cells: [
      rich("Ingredient"), rich("Quantity"), rich("Unit"), rich("Notes")
    ] } },
    { id: id(13), type: "table_row", table_row: { cells: [
      rich("Coconut aminos"), rich("0.25"), rich("cup"), rich("")
    ] } },
    { id: id(14), type: "table_row", table_row: { cells: [
      rich("Onion"), rich("0.5"), rich("each"), rich("Diced")
    ] } }
  ];
  const state = { duplicates: [], mealMatches: [], recipePage, blocks, rows, extraTables: {} };
  let nextBlockId = 30;
  function insertBlocks(storage, additions, after) {
    const fresh = additions.map((item) => {
      const block = structuredClone(item);
      block.id = id(nextBlockId++);
      if (block.type === "table") {
        state.extraTables[normal(block.id)] = insertBlocks([], block.table.children ?? []);
        delete block.table.children;
      }
      return block;
    });
    const pos = after ? storage.findIndex((item) => item.id === after ||
      normal(item.id) === normal(after)) : storage.length - 1;
    if (after && pos < 0) throw new Error("Unknown insert anchor");
    storage.splice(pos + 1, 0, ...fresh);
    return fresh;
  }
  const fakeFetch = async (url, init) => {
    const path = new URL(url).pathname;
    const method = init.method;
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ url, path, method, body, init });
    if (method === "POST" && path.endsWith("/data_sources/" + recipeSource + "/query")) {
      return Response.json({ results: state.duplicates, has_more: false });
    }
    if (method === "POST" && path.endsWith("/data_sources/" + mealsSource + "/query")) {
      return Response.json({ results: state.mealMatches, has_more: false });
    }
    if (method === "GET" && path.endsWith("/pages/" + normal(pageId))) {
      return Response.json(state.recipePage);
    }
    if (method === "GET" && path.endsWith("/pages/" + normal(mealId))) {
      return Response.json(responsePage(mealsSource, mealId));
    }
    if (method === "GET" && path.endsWith("/blocks/" + normal(pageId) + "/children")) {
      return Response.json({ results: state.blocks, has_more: false });
    }
    if (method === "GET" && path.includes("/blocks/") && path.endsWith("/children")) {
      const blockId = path.split("/")[3];
      const tableRows = blockId === normal(id(8)) ? state.rows : state.extraTables[blockId];
      if (tableRows) return Response.json({ results: tableRows, has_more: false });
    }
    if (method === "POST" && path.endsWith("/pages")) {
      return Response.json(responsePage(body.parent.data_source_id,
        body.parent.data_source_id === recipeSource ? pageId : mealId));
    }
    if (method === "PATCH" && path.endsWith("/pages/" + normal(mealId))) {
      return Response.json(responsePage(mealsSource, mealId));
    }
    if (method === "PATCH" && path.endsWith("/pages/" + normal(pageId))) {
      const props = body.properties;
      state.recipePage.properties = {
        Name: { type: "title", title: props.Name.title.map((t) => ({ plain_text: t.text.content })) },
        Servings: { type: "number", number: props.Servings.number },
        Recipe: { type: "url", url: props.Recipe?.url ?? state.recipePage.properties.Recipe?.url ?? null }
      };
      return Response.json(state.recipePage);
    }
    if (method === "PATCH" && path.startsWith("/v1/blocks/") && path.endsWith("/children")) {
      const blockId = path.split("/")[3];
      const target = blockId === normal(pageId) ? state.blocks :
        blockId === normal(id(8)) ? state.rows : state.extraTables[blockId];
      if (!target) throw new Error("Unexpected append destination: " + path);
      return Response.json({ results: insertBlocks(target, body.children, body.after) });
    }
    if (method === "PATCH" && path.startsWith("/v1/blocks/")) {
      const blockId = path.split("/")[3];
      const allRows = [state.rows, ...Object.values(state.extraTables)].flat();
      const block = allRows.find((r) => normal(r.id) === blockId) ||
        state.blocks.find((b) => normal(b.id) === blockId);
      if (block && body.table_row) {
        block.table_row.cells = body.table_row.cells.map((a) => rich(a[0].text.content));
        return Response.json(block);
      }
      if (block && body.numbered_list_item) {
        block.numbered_list_item.rich_text = rich(body.numbered_list_item.rich_text[0].text.content);
        return Response.json(block);
      }
    }
    if (method === "DELETE" && path.startsWith("/v1/blocks/")) {
      const blockId = path.split("/")[3];
      for (const storage of [state.blocks, state.rows, ...Object.values(state.extraTables)]) {
        const index = storage.findIndex((v) => normal(v.id) === blockId);
        if (index >= 0) return Response.json(storage.splice(index, 1)[0]);
      }
    }
    throw new Error("Unexpected request " + method + " " + path);
  };
  return { calls, state, fakeFetch, pageId, mealId };
}

test("recipe content maps to native Notion 4-column table without remote scraping", () => {
  const desired = validateRecipe(payload());
  const blocks = recipeBlocks(desired);
  assert.equal(blocks.length, 5);
  assert.equal(blocks[1].table.table_width, 4);
  assert.equal(blocks[1].table.children.length, 3);
  const second = blocks[1].table.children[2].table_row.cells.map((cell) => cell[0].text.content);
  assert.deepEqual(second, ["Onion", "0.5", "each", "Diced"]);
  assert.equal(blocks[4].type, "numbered_list_item");
});

test("invalid ingredient, unsafe URL and meal date fail before network calls", () => {
  assert.throws(() => validateRecipe({ ...payload(), ingredients: [] }), /1-50 ingredients/);
  assert.throws(() => validateRecipe({ ...payload(), ingredients: [
    { ingredient: "Salt", quantity: "" }
  ] }), /quantity/);
  assert.throws(() => validateRecipe({ ...payload(), source_url: "file:///private/key" }), /HTTP/);
  assert.throws(() => validateRecipe({ ...payload(), name: "Evil\u0000" }), /recipe name/);
  assert.throws(() => validateMeal({ date: "2026-02-30", meal_type: "Dinner", recipe_ids: [id(3)] }), /date/);
  assert.throws(() => validateMeal({ date: "2026-11-02", meal_type: "Snack", recipe_ids: [id(3)] }), /type/);
});

test("write operations are disabled by default", () => {
  const { ENABLE_NOTION_WRITES, ...readOnly } = env;
  assert.throws(() => createNotionWriter(readOnly), /disabled/);
});

test("create recipe uses allowlisted data source, no other Notion URL", async () => {
  const fixture = createFixture();
  const saved = await createNotionWriter(env, fixture.fakeFetch).saveRecipe(payload());
  assert.deepEqual({ action: saved.action, recipe_id: saved.recipe_id },
    { action: "created", recipe_ids: [fixture.pageId] });
  const post = fixture.calls.find((call) => call.method === "POST" && call.path === "/v1/pages");
  assert.equal(post.body.parent.data_source_id, normal(id(1)));
  assert.equal(post.body.children[1].table.children.length, 3);
  assert.equal(post.body.template, undefined, "API-created recipes use explicit native blocks, not asynchronous default templates");
  assert(fixture.calls.every((call) => new URL(call.url).host === "api.notion.com"));
  assert(fixture.calls.every((call) => call.init.headers["Notion-Version"] === "2025-09-03"));
});

test("duplicate recipe is not silently created", async () => {
  const fixture = createFixture();
  fixture.state.duplicates = [fixture.state.recipePage];
  await assert.rejects(() => createNotionWriter(env, fixture.fakeFetch).saveRecipe(payload()), /already exists/);
  assert.equal(fixture.calls.some((call) => call.path === "/v1/pages" && call.method === "POST"), false);
});

test("out-of-scope recipe update is denied before mutation", async () => {
  const fixture = createFixture();
  fixture.state.recipePage.parent.data_source_id = id(7);
  await assert.rejects(() => createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  }), /outside/);
  assert(fixture.calls.every((call) => call.method !== "PATCH"));
});

test("stale recipe update is refused before touching page blocks", async () => {
  const fixture = createFixture();
  await assert.rejects(() => createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-24T20:00:00.000Z"
  }), /changed since/);
  assert(fixture.calls.every((call) => call.method === "GET"));
});

test("same-count recipe edit updates only managed rows/steps and reads back", async () => {
  const fixture = createFixture();
  const result = await createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), name: "Fictional Bowl, revised", servings: 2,
    ingredients: [
      { ingredient: "Coconut aminos", quantity: "0.5", unit: "cup", notes: "" },
      { ingredient: "Onion", quantity: "1", unit: "each", notes: "Diced" }
    ],
    instructions: ["Brown onion.", "Serve."],
    recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  });
  assert.equal(result.action, "updated");
  assert.equal(fixture.state.rows[1].table_row.cells[1][0].plain_text, "0.5");
  assert.equal(fixture.state.blocks[3].numbered_list_item.rich_text[0].plain_text, "Brown onion.");
  const rowPatches = fixture.calls.filter((call) =>
    call.method === "PATCH" && call.path.includes("/blocks/"));
  assert.equal(rowPatches.length, 3);
});

test("populate a link-only recipe without destroying existing source notes", async () => {
  const fixture = createFixture();
  fixture.state.recipePage.properties.Servings.number = null;
  fixture.state.blocks = [
    { id: id(20), type: "paragraph", paragraph: { rich_text: rich("Human source notes: preserve me.") } }
  ];
  const result = await createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  });
  assert.equal(result.action, "updated");
  assert.equal(fixture.state.blocks[0].paragraph.rich_text[0].plain_text,
    "Human source notes: preserve me.");
  assert.equal(fixture.state.blocks.filter((b) => b.type === "table").length, 1);
  assert.equal(fixture.state.blocks.filter((b) => b.type === "heading_2").length, 2);
  const firstTable = fixture.state.blocks.find((b) => b.type === "table");
  assert.equal(fixture.state.extraTables[normal(firstTable.id)].length, 3);
  const edits = fixture.calls.filter((c) => c.method === "PATCH" && c.path.endsWith("/children"));
  assert.equal(edits.length, 1, "link-only recipe should receive one complete set of blocks");
});

test("populate a default blank template in place, preserving Notes and headings", async () => {
  const fixture = createFixture();
  fixture.state.blocks = [
    { id: id(7), type: "heading_2", heading_2: { rich_text: rich("Ingredients") } },
    { id: id(8), type: "table", table: { table_width: 4 } },
    { id: id(9), type: "heading_2", heading_2: { rich_text: rich("Instructions") } },
    { id: id(10), type: "numbered_list_item", numbered_list_item: { rich_text: rich("Add first instruction here.") } },
    { id: id(20), type: "heading_2", heading_2: { rich_text: rich("Notes") } },
    { id: id(21), type: "paragraph", paragraph: { rich_text: rich("Optional") } }
  ];
  fixture.state.rows = [
    { id: id(12), type: "table_row", table_row: { cells: [
      rich("Ingredient"), rich("Quantity"), rich("Unit"), rich("Notes")
    ] } },
    { id: id(13), type: "table_row", table_row: { cells: [
      rich(""), rich(""), rich(""), rich("")
    ] } }
  ];
  const result = await createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  });
  assert.equal(result.action, "updated");
  assert.equal(fixture.state.blocks.filter((b) => b.type === "table").length, 1);
  assert.equal(fixture.state.blocks.filter((b) => b.type === "heading_2").length, 3);
  assert.equal(fixture.state.blocks.find((b) => b.id === id(21)).paragraph.rich_text[0].plain_text,
    "Optional");
  assert.equal(fixture.state.rows[1].table_row.cells[0][0].plain_text, "Coconut aminos");
  assert.equal(fixture.state.rows.length, 3);
  assert.equal(fixture.state.blocks.find((b) => b.id === id(10)).numbered_list_item.rich_text[0].plain_text,
    "Cook onion.");
});

test("populate a header-only template with missing table and no instructions", async () => {
  const fixture = createFixture();
  fixture.state.blocks = [
    { id: id(7), type: "heading_2", heading_2: { rich_text: rich("Ingredients") } },
    { id: id(9), type: "heading_2", heading_2: { rich_text: rich("Instructions") } },
    { id: id(21), type: "paragraph", paragraph: { rich_text: rich("Keep manual notes.") } }
  ];
  const result = await createNotionWriter(env, fixture.fakeFetch).saveRecipe({
    ...payload(), recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  });
  assert.equal(result.action, "updated");
  const tablePos = fixture.state.blocks.findIndex((b) => b.type === "table");
  const instructionPos = fixture.state.blocks.findIndex((b) => b.type === "numbered_list_item");
  assert.equal(tablePos, 1);
  assert.equal(instructionPos, 3);
  assert.equal(fixture.state.blocks.at(-1).paragraph.rich_text[0].plain_text, "Keep manual notes.");
});

test("reject ambiguous recipe headings or tables without mutating content", async () => {
  for (const mutate of [
    (fixture) => fixture.state.blocks.push({
      id: id(25), type: "heading_2", heading_2: { rich_text: rich("Ingredients") }
    }),
    (fixture) => { fixture.state.blocks[1].table.table_width = 5; },
    (fixture) => { fixture.state.blocks.splice(2, 1); }
  ]) {
    const fixture = createFixture();
    mutate(fixture);
    await assert.rejects(() => createNotionWriter(env, fixture.fakeFetch).saveRecipe({
      ...payload(), recipe_ids: [fixture.pageId],
      expected_last_edited_time: "2026-10-25T20:00:00.000Z"
    }), /manual review required/);
    assert(fixture.calls.every((c) => c.method === "GET"),
      "Ambiguous content must not be partially rewritten");
  }
});

test("revise a structured recipe with growing and shrinking managed blocks", async () => {
  const fixture = createFixture();
  const writer = createNotionWriter(env, fixture.fakeFetch);
  const ref = {
    recipe_ids: [fixture.pageId],
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  };
  const expanded = await writer.saveRecipe({
    ...payload(), ...ref,
    ingredients: [...payload().ingredients, {
      ingredient: "Synthetic spinach", quantity: "1", unit: "cup", notes: ""
    }],
    instructions: [...payload().instructions, "Serve with synthetic spinach."]
  });
  assert.equal(expanded.action, "updated");
  assert.equal(fixture.state.rows.length, 4); // header plus 3 ingredient rows
  assert.equal(fixture.state.blocks.filter((b) => b.type === "numbered_list_item").length, 3);
  const shrunk = await writer.saveRecipe({
    ...payload(), ...ref,
    ingredients: [payload().ingredients[0]],
    instructions: [payload().instructions[0]]
  });
  assert.equal(shrunk.action, "updated");
  assert.equal(fixture.state.rows.length, 2); // header plus only one ingredient
  assert.equal(fixture.state.blocks.filter((b) => b.type === "numbered_list_item").length, 1);
  assert(fixture.calls.filter((c) => c.method === "DELETE").length >= 4);
});

test("weekly meal saves only after recipe is complete and checks slot conflicts", async () => {
  const fixture = createFixture();
  const writer = createNotionWriter(env, fixture.fakeFetch);
  const saved = await writer.saveWeeklyMeal({
    date: "2026-11-02", meal_type: "Dinner", recipe_ids: [fixture.pageId],
    servings: 2
  });
  assert.equal(saved.action, "created");
  const post = fixture.calls.find((c) => c.path === "/v1/pages" && c.method === "POST");
  assert.equal(post.body.parent.data_source_id, normal(id(2)));
  assert.equal(post.body.properties.Meal.title[0].text.content, "Fictional Bowl");
  assert.deepEqual(post.body.properties["Meal Link"].relation, [{ id: normal(id(3)) }]);
  const multi = validateMeal({ date: "2026-11-02", meal_type: "Dinner", recipe_ids: [id(3), id(5)] });
  assert.equal(multi.recipe_ids.length, 2);
  assert.throws(() => validateMeal({ date: "2026-11-02", meal_type: "Dinner", recipe_ids: [id(3), id(3)] }), /Duplicate/);
  fixture.state.mealMatches = [{ id: fixture.mealId }];
  await assert.rejects(() => writer.saveWeeklyMeal({
    date: "2026-11-02", meal_type: "Dinner", recipe_ids: [fixture.pageId]
  }), /already exists/);
});

test("weekly meal updates require fresh edit token and matching meal source", async () => {
  const fixture = createFixture();
  const writer = createNotionWriter(env, fixture.fakeFetch);
  await assert.rejects(() => writer.saveWeeklyMeal({
    meal_id: fixture.mealId,
    expected_last_edited_time: "2026-10-24T20:00:00.000Z",
    date: "2026-11-02", meal_type: "Dinner", recipe_ids: [fixture.pageId]
  }), /changed since/);
  assert.equal(fixture.calls.some((call) => call.method === "PATCH"), false);
  const updated = await writer.saveWeeklyMeal({
    meal_id: fixture.mealId,
    expected_last_edited_time: "2026-10-25T20:00:00.000Z",
    date: "2026-11-02", meal_type: "Dinner", recipe_ids: [fixture.pageId]
  });
  assert.equal(updated.action, "updated");
  const edit = fixture.calls.find((c) => c.method === "PATCH");
  assert.equal(edit.body.properties.Meal, undefined,
    "A serving-only edit must not overwrite manually set meal titles");
});

test("remove weekly meal archives only authorized scoped page", async () => {
  const fixture = createFixture();
  const result = await createNotionWriter(env, fixture.fakeFetch).removeWeeklyMeal({
    meal_id: fixture.mealId,
    expected_last_edited_time: "2026-10-25T20:00:00.000Z"
  });
  assert.equal(result.action, "archived");
  const patch = fixture.calls.find((c) => c.method === "PATCH");
  assert.deepEqual(patch.body, { archived: true });
  assert.equal(patch.path, "/v1/pages/" + normal(fixture.mealId));
});

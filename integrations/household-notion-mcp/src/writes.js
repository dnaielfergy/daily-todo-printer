// Controlled working-state writes. Never writes GitHub, publishes a receipt, or calls the printer.
// All page IDs are checked against the two explicitly configured household data sources.
import { assertId, createNotion, parseRecipeBlocks } from "./notion.js";

const VERSION = "2025-09-03";
const ingredientHeader = ["Ingredient", "Quantity", "Unit", "Notes"];
const text = (value, max, label, optional = false) => {
  if (optional && (value === undefined || value === null || value === "")) return "";
  if (typeof value !== "string" || !value.trim() || value.length > max ||
      /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error("Invalid " + label);
  }
  return value.trim();
};
const rich = (value) => [{ type: "text", text: { content: value } }];
const plain = (cells) => cells.map((v) => v.map((part) => part.plain_text ?? part.text?.content ?? "").join("").trim());
const rowBlock = (cells) => ({
  object: "block",
  type: "table_row",
  table_row: { cells: cells.map(rich) }
});
const numberBlock = (step) => ({
  object: "block",
  type: "numbered_list_item",
  numbered_list_item: { rich_text: rich(step) }
});
const heading = (value) => ({
  object: "block",
  type: "heading_2",
  heading_2: { rich_text: rich(value) }
});

export function validateRecipe(payload) {
  if (!payload || typeof payload !== "object") throw new Error("Invalid recipe");
  const name = text(payload.name, 160, "recipe name");
  if (typeof payload.servings !== "number" || !Number.isFinite(payload.servings) ||
      payload.servings <= 0 || payload.servings > 100) throw new Error("Invalid recipe servings");
  let source_url = payload.source_url === undefined ? undefined : null;
  if (payload.source_url !== undefined && payload.source_url !== null && payload.source_url !== "") {
    source_url = text(payload.source_url, 1000, "recipe source URL");
    let url;
    try { url = new URL(source_url); } catch { throw new Error("Invalid recipe source URL"); }
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname) {
      throw new Error("Recipe source must be an HTTP(S) URL");
    }
  }
  if (!Array.isArray(payload.ingredients) || !payload.ingredients.length ||
      payload.ingredients.length > 50) throw new Error("Recipe must have 1-50 ingredients");
  const ingredients = payload.ingredients.map((item) => ({
    ingredient: text(item?.ingredient, 160, "ingredient name"),
    quantity: text(item?.quantity, 50, "ingredient quantity"),
    unit: text(item?.unit, 40, "ingredient unit", true),
    notes: text(item?.notes, 200, "ingredient notes", true)
  }));
  if (!Array.isArray(payload.instructions) || !payload.instructions.length ||
      payload.instructions.length > 40) throw new Error("Recipe must have 1-40 instructions");
  const instructions = payload.instructions.map((step) => text(step, 1000, "instruction"));
  function labels(name, values) {
    if (values === undefined) return undefined;
    if (!Array.isArray(values) || values.length > 12) throw new Error("Invalid " + name);
    const cleaned = values.map((v) => text(v, 80, name));
    if (new Set(cleaned.map((v) => v.toLowerCase())).size !== cleaned.length) {
      throw new Error("Duplicate " + name);
    }
    return cleaned;
  }
  const meal_types = labels("recipe Meal Type", payload.meal_types);
  const diet = labels("recipe Diet", payload.diet);
  const best_for = labels("recipe Best For", payload.best_for);
  const notes = payload.notes === undefined ? undefined :
    text(payload.notes, 1800, "recipe Notes", true);
  return {
    name, servings: payload.servings, source_url, ingredients, instructions,
    meal_types, diet, best_for, notes
  };
}

export function recipeBlocks(recipe) {
  return [
    heading("Ingredients"),
    {
      object: "block",
      type: "table",
      table: {
        table_width: 4,
        has_column_header: true,
        has_row_header: false,
        children: [
          rowBlock(ingredientHeader),
          ...recipe.ingredients.map((item) =>
            rowBlock([item.ingredient, item.quantity, item.unit, item.notes]))
        ]
      }
    },
    heading("Instructions"),
    ...recipe.instructions.map(numberBlock)
  ];
}

export function validateMeal(payload) {
  const date = text(payload?.date, 10, "meal date");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("Meal date must be YYYY-MM-DD");
  const day = new Date(date + "T00:00:00Z");
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== date) {
    throw new Error("Invalid meal date");
  }
  const meal_type = payload?.meal_type;
  if (!["Breakfast", "Lunch", "Dinner"].includes(meal_type)) {
    throw new Error("Invalid meal type");
  }
  const recipe_id = assertId(payload.recipe_id);
  const servings = payload.servings ?? null;
  if (servings !== null && (!Number.isFinite(servings) || servings <= 0 || servings > 100)) {
    throw new Error("Invalid meal servings");
  }
  return { date, meal_type, recipe_id, servings };
}

function recipeProperties(recipe) {
  return {
    Name: { title: rich(recipe.name) },
    Servings: { number: recipe.servings },
    ...(recipe.source_url !== undefined ? { Recipe: { url: recipe.source_url } } : {}),
    ...(recipe.meal_types !== undefined ? {
      "Meal Type": { multi_select: recipe.meal_types.map((name) => ({ name })) }
    } : {}),
    ...(recipe.diet !== undefined ? {
      Diet: { multi_select: recipe.diet.map((name) => ({ name })) }
    } : {}),
    ...(recipe.best_for !== undefined ? {
      "Best For": { multi_select: recipe.best_for.map((name) => ({ name })) }
    } : {}),
    ...(recipe.notes !== undefined ? { Notes: { rich_text: recipe.notes ? rich(recipe.notes) : [] } } : {})
  };
}
function mealProperties(meal) {
  return {
    Meal: { title: rich(meal.meal_type + " — " + meal.date) },
    Date: { date: { start: meal.date } },
    "Meal Type": { select: { name: meal.meal_type } },
    "Meal link": { relation: [{ id: meal.recipe_id }] },
    Servings: { number: meal.servings }
  };
}

function structure(blocks, tableRows) {
  let section = "";
  let ingredientTable = null;
  const steps = [];
  for (const block of blocks) {
    if (["heading_1", "heading_2", "heading_3"].includes(block.type)) {
      section = (block[block.type]?.rich_text ?? []).map((p) =>
        p.plain_text ?? p.text?.content ?? "").join("").trim().toLowerCase();
    } else if (section === "ingredients" && block.type === "table") {
      if (ingredientTable) throw new Error("Multiple ingredient tables; manual review required");
      ingredientTable = block;
    } else if (section === "instructions" && block.type === "numbered_list_item") {
      steps.push(block);
    }
  }
  if (!ingredientTable || ingredientTable.table?.table_width !== 4 || !steps.length) {
    throw new Error("Recipe is not in the approved template; manual review required");
  }
  const rows = tableRows[ingredientTable.id];
  if (!Array.isArray(rows) || rows.some((r) => r.type !== "table_row" ||
      r.table_row?.cells?.length !== 4)) {
    throw new Error("Unsupported ingredient table; manual review required");
  }
  const header = rows[0] &&
    plain(rows[0].table_row.cells).slice(0, 3).join("|").toLowerCase() ===
    "ingredient|quantity|unit";
  const ingredientRows = header ? rows.slice(1) : rows;
  // Refuse to rewrite an incomplete/ambiguous existing template.
  parseRecipeBlocks(blocks, tableRows);
  return { table: ingredientTable, rows: ingredientRows, steps };
}

export function createNotionWriter(env, transport = fetch) {
  if (env.ENABLE_NOTION_WRITES !== "true") {
    throw new Error("Notion writes are disabled");
  }
  const recipes = assertId(env.NOTION_RECIPES_DATA_SOURCE_ID);
  const meals = assertId(env.NOTION_MEALS_DATA_SOURCE_ID);
  if (!env.NOTION_TOKEN) throw new Error("Notion token not configured");
  const reader = createNotion(env, transport);

  async function call(method, path, body) {
    const response = await transport("https://api.notion.com/v1/" + path, {
      method,
      headers: {
        Authorization: "Bearer " + env.NOTION_TOKEN,
        "Notion-Version": VERSION,
        "Content-Type": "application/json"
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(10000)
    });
    if (!response.ok) {
      // Notion error responses may contain household data; never echo those bodies.
      throw new Error("Notion " + method + " failed (HTTP " + response.status +
        "); inspect the page before retrying");
    }
    return response.json();
  }

  async function requirePage(pageId, sourceId, expectedTime) {
    const page = await call("GET", "pages/" + assertId(pageId));
    if (page.archived || page.in_trash) throw new Error("Cannot edit an archived Notion page");
    if (!page.parent?.data_source_id ||
        assertId(page.parent.data_source_id) !== sourceId) {
      throw new Error("Page is outside the allowed household data source");
    }
    if (expectedTime !== undefined && page.last_edited_time !== expectedTime) {
      throw new Error("Page changed since it was read; read again before saving");
    }
    return page;
  }

  async function blockChildren(id) {
    const found = [];
    let cursor;
    do {
      const qs = new URLSearchParams({ page_size: "100" });
      if (cursor) qs.set("start_cursor", cursor);
      const data = await call("GET", "blocks/" + assertId(id) + "/children?" + qs);
      found.push(...data.results);
      if (found.length > 300) throw new Error("Recipe block limit exceeded");
      if (data.has_more && !data.next_cursor) throw new Error("Notion pagination incomplete");
      cursor = data.has_more ? data.next_cursor : undefined;
    } while (cursor);
    return found;
  }

  async function matchingMeals(meal) {
    const data = await call("POST", "data_sources/" + meals + "/query", {
      page_size: 10,
      filter: {
        and: [
          { property: "Date", date: { equals: meal.date } },
          { property: "Meal Type", select: { equals: meal.meal_type } }
        ]
      }
    });
    if (data.has_more) throw new Error("Meal slot has ambiguous duplicate records");
    return data.results;
  }

  async function updateRecipeBody(recipeId, desired) {
    const blocks = await blockChildren(recipeId);
    const tables = {};
    for (const block of blocks) {
      if (block.type === "table") tables[block.id] = await blockChildren(block.id);
    }
    const existing = structure(blocks, tables);
    const nextRows = desired.ingredients.map((item) =>
      [item.ingredient, item.quantity, item.unit, item.notes]);

    // Update in place to preserve page and relation identities, headings,
    // other manual notes and the original table block.
    for (let i = 0; i < Math.min(existing.rows.length, nextRows.length); i++) {
      if (JSON.stringify(plain(existing.rows[i].table_row.cells)) !==
          JSON.stringify(nextRows[i])) {
        await call("PATCH", "blocks/" + assertId(existing.rows[i].id), {
          table_row: { cells: nextRows[i].map(rich) }
        });
      }
    }
    if (nextRows.length > existing.rows.length) {
      await call("PATCH", "blocks/" + assertId(existing.table.id) + "/children", {
        children: nextRows.slice(existing.rows.length).map(rowBlock)
      });
    }
    for (const row of existing.rows.slice(nextRows.length)) {
      await call("DELETE", "blocks/" + assertId(row.id));
    }

    for (let i = 0; i < Math.min(existing.steps.length, desired.instructions.length); i++) {
      const current = (existing.steps[i].numbered_list_item.rich_text ?? [])
        .map((v) => v.plain_text ?? v.text?.content ?? "").join("").trim();
      if (current !== desired.instructions[i]) {
        await call("PATCH", "blocks/" + assertId(existing.steps[i].id), {
          numbered_list_item: { rich_text: rich(desired.instructions[i]) }
        });
      }
    }
    if (desired.instructions.length > existing.steps.length) {
      await call("PATCH", "blocks/" + assertId(recipeId) + "/children", {
        after: existing.steps.at(-1).id,
        children: desired.instructions.slice(existing.steps.length).map(numberBlock)
      });
    }
    for (const step of existing.steps.slice(desired.instructions.length)) {
      await call("DELETE", "blocks/" + assertId(step.id));
    }
  }

  return {
    async saveRecipe(payload) {
      const recipe = validateRecipe(payload);
      const existingId = payload.recipe_id;
      if (!existingId) {
        const duplicates = await call("POST", "data_sources/" + recipes + "/query", {
          page_size: 2,
          filter: { property: "Name", title: { equals: recipe.name } }
        });
        if (duplicates.results.length) {
          throw new Error("Recipe name already exists; update the existing recipe ID");
        }
        const page = await call("POST", "pages", {
          parent: { type: "data_source_id", data_source_id: recipes },
          properties: recipeProperties(recipe),
          children: recipeBlocks(recipe)
        });
        return { action: "created", recipe_id: page.id, url: page.url };
      }

      const expected = text(payload.expected_last_edited_time, 50,
        "expected_last_edited_time");
      const page = await requirePage(existingId, recipes, expected);
      await updateRecipeBody(page.id, recipe);
      await call("PATCH", "pages/" + assertId(page.id), {
        properties: recipeProperties(recipe)
      });
      const readback = await reader.getRecipe(page.id);
      if (readback.name !== recipe.name || readback.servings !== recipe.servings ||
          JSON.stringify(readback.ingredients) !== JSON.stringify(recipe.ingredients) ||
          JSON.stringify(readback.instructions) !== JSON.stringify(recipe.instructions)) {
        throw new Error("Recipe read-back differs; inspect the page before retrying");
      }
      return { action: "updated", recipe_id: page.id, url: page.url };
    },

    async saveWeeklyMeal(payload) {
      const meal = validateMeal(payload);
      // The selected recipe must be in scope AND fully prepared for grocery planning.
      await reader.getRecipe(meal.recipe_id);
      const matching = await matchingMeals(meal);
      let page;
      if (payload.meal_id) {
        const expected = text(payload.expected_last_edited_time, 50,
          "expected_last_edited_time");
        page = await requirePage(payload.meal_id, meals, expected);
        if (matching.some((record) => assertId(record.id) !== assertId(page.id))) {
          throw new Error("Another assignment already occupies this meal slot");
        }
        const updated = await call("PATCH", "pages/" + assertId(page.id), {
          properties: mealProperties(meal)
        });
        return { action: "updated", meal_id: updated.id, url: updated.url };
      }
      if (matching.length) {
        throw new Error("Meal slot already exists; update its existing ID instead");
      }
      page = await call("POST", "pages", {
        parent: { type: "data_source_id", data_source_id: meals },
        properties: mealProperties(meal)
      });
      return { action: "created", meal_id: page.id, url: page.url };
    },

    async removeWeeklyMeal({ meal_id, expected_last_edited_time }) {
      const expected = text(expected_last_edited_time, 50, "expected_last_edited_time");
      const page = await requirePage(meal_id, meals, expected);
      await call("PATCH", "pages/" + assertId(page.id), { archived: true });
      return { action: "archived", meal_id: page.id };
    }
  };
}

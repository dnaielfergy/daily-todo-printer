// Fixed-scope Notion read adapter. Never fetch arbitrary URLs or expose unrelated pages.
const NOTION_VERSION = "2025-09-03";
const UUID = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;
const MAX_BLOCKS = 300;

export function assertId(id) {
  if (typeof id !== "string" || !UUID.test(id)) throw new Error("Invalid Notion identifier");
  return id.replaceAll("-", "").toLowerCase();
}

function plainText(parts) {
  if (!Array.isArray(parts)) return "";
  return parts.map((part) => part.plain_text ?? part.text?.content ?? "").join("").trim();
}

function richProperty(property) {
  if (!property) return "";
  if (property.type === "title") return plainText(property.title);
  if (property.type === "rich_text") return plainText(property.rich_text);
  if (property.type === "url") return property.url ?? "";
  return "";
}

export function recipeSummary(page, requireComplete = false) {
  const properties = page?.properties ?? {};
  const title = richProperty(properties.Name);
  const rawServings = properties.Servings?.number;
  const servings = Number.isFinite(rawServings) && rawServings > 0 ? rawServings : null;
  if (!title || (requireComplete && servings === null)) {
    throw new Error("Recipe has no Name or valid base Servings");
  }
  return {
    id: page.id,
    name: title,
    servings,
    needs_preparation: servings === null,
    source_url: richProperty(properties.Recipe) || null,
    ...(page.last_edited_time ? { last_edited_time: page.last_edited_time } : {})
  };
}

export function parseRecipeBlocks(blocks, tableRows) {
  let section = "";
  let ingredients = null;
  const instructions = [];
  for (const block of blocks) {
    if (["heading_1", "heading_2", "heading_3"].includes(block.type)) {
      section = plainText(block[block.type]?.rich_text).toLowerCase();
      continue;
    }
    if (section === "ingredients" && block.type === "table") {
      if (ingredients !== null || block.table?.table_width !== 4) {
        throw new Error("Recipe requires one four-column ingredients table");
      }
      ingredients = [];
      const rows = tableRows[block.id] ?? [];
      for (const row of rows) {
        if (row.type !== "table_row" || row.table_row?.cells?.length !== 4) {
          throw new Error("Invalid ingredient table row");
        }
        const [name, quantity, unit, notes] = row.table_row.cells.map(plainText);
        if (name.toLowerCase() === "ingredient" &&
            quantity.toLowerCase() === "quantity" &&
            unit.toLowerCase() === "unit") continue;
        if (!name || !quantity || name.length > 160 || quantity.length > 50 ||
            unit.length > 40 || notes.length > 200) {
          throw new Error("Incomplete or oversized ingredient row");
        }
        ingredients.push({ ingredient: name, quantity, unit, notes });
      }
      continue;
    }
    if (section === "instructions" && block.type === "numbered_list_item") {
      const text = plainText(block.numbered_list_item?.rich_text);
      if (!text || text.length > 1000 ||
          ["add first instruction here.", "add first instruction here"].includes(text.toLowerCase())) {
        throw new Error("Invalid or placeholder instruction step");
      }
      instructions.push(text);
    }
  }
  if (!ingredients?.length || ingredients.length > 120 || !instructions.length ||
      instructions.length > 60) {
    throw new Error("Recipe is not print-ready: ingredients or instructions missing/oversized");
  }
  return { ingredients, instructions };
}

export function weeklyMeal(page) {
  const p = page?.properties ?? {};
  const date = p.Date?.date?.start;
  const mealType = p["Meal Type"]?.select?.name;
  const relation = p["Meal Link"]?.relation;
  const servings = p.Servings?.number ?? null;
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !["Breakfast", "Lunch", "Dinner"].includes(mealType) ||
      !Array.isArray(relation) || relation.length < 1 || relation.length > 12 || relation.some((entry) => !entry?.id) ||
      new Set(relation.map((entry) => assertId(entry.id))).size !== relation.length ||
      (servings !== null && (!Number.isFinite(servings) || servings <= 0))) {
    throw new Error("Weekly meal needs Date, Meal Type, one or more unique Meal Links and optional positive Servings");
  }
  return {
    id: page.id,
    date,
    meal_type: mealType,
    recipe_ids: relation.map((entry) => entry.id),
    servings,
    printable: mealType !== "Breakfast",
    ...(page.last_edited_time ? { last_edited_time: page.last_edited_time } : {})
  };
}

export function weekDates(weekStart) {
  if (typeof weekStart !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(weekStart)) {
    throw new Error("week_start must be YYYY-MM-DD");
  }
  const date = new Date(weekStart + "T00:00:00Z");
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== weekStart ||
      date.getUTCDay() !== 1) {
    throw new Error("week_start must be an actual Monday");
  }
  date.setUTCDate(date.getUTCDate() + 6);
  return { start: weekStart, end: date.toISOString().slice(0, 10) };
}

export function createNotion(env, transport = fetch) {
  const token = env.NOTION_TOKEN;
  if (!token || !env.NOTION_RECIPES_DATA_SOURCE_ID || !env.NOTION_MEALS_DATA_SOURCE_ID) {
    throw new Error("Household Notion connection not configured");
  }
  const recipeSource = assertId(env.NOTION_RECIPES_DATA_SOURCE_ID);
  const mealSource = assertId(env.NOTION_MEALS_DATA_SOURCE_ID);

  async function call(method, path, body) {
    const response = await transport("https://api.notion.com/v1/" + path, {
      method,
      headers: {
        Authorization: "Bearer " + token,
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(10000)
    });
    if (!response.ok) {
      // Never return upstream Notion error bodies: they may contain private content.
      throw new Error("Notion request failed (HTTP " + response.status + ")");
    }
    return response.json();
  }

  async function children(blockId) {
    const all = [];
    let cursor;
    do {
      const q = new URLSearchParams({ page_size: "100" });
      if (cursor) q.set("start_cursor", cursor);
      const data = await call("GET", "blocks/" + assertId(blockId) + "/children?" + q);
      all.push(...data.results);
      if (all.length > MAX_BLOCKS) throw new Error("Notion page exceeds supported block limit");
      cursor = data.has_more ? data.next_cursor : undefined;
      if (data.has_more && !cursor) throw new Error("Notion pagination is incomplete");
    } while (cursor);
    return all;
  }

  async function list(sourceId, body) {
    const data = await call("POST", "data_sources/" + sourceId + "/query", body);
    return {
      results: data.results,
      next_cursor: data.has_more ? data.next_cursor : null
    };
  }

  return {
    async listRecipes({ cursor = undefined, limit = 30 } = {}) {
      const data = await list(recipeSource, {
        page_size: limit,
        ...(cursor ? { start_cursor: cursor } : {})
      });
      return {
        recipes: data.results.map((page) => recipeSummary(page)),
        next_cursor: data.next_cursor
      };
    },
    async getRecipe(id) {
      const page = await call("GET", "pages/" + assertId(id));
      const parentId = page.parent?.data_source_id;
      // Do not expose unrelated Notion pages even if the token can read them.
      if (!parentId || assertId(parentId) !== recipeSource) {
        throw new Error("Recipe does not belong to the permitted recipe data source");
      }
      const blocks = await children(id);
      const rows = {};
      for (const block of blocks) {
        if (block.type === "table") rows[block.id] = await children(block.id);
      }
      return { ...recipeSummary(page, true), ...parseRecipeBlocks(blocks, rows) };
    },
    async listWeeklyMeals({ week_start, cursor = undefined } = {}) {
      const week = weekDates(week_start);
      const data = await list(mealSource, {
        page_size: 100,
        filter: {
          and: [
            { property: "Date", date: { on_or_after: week.start } },
            { property: "Date", date: { on_or_before: week.end } }
          ]
        },
        ...(cursor ? { start_cursor: cursor } : {})
      });
      return {
        week_start: week.start,
        meals: data.results.map(weeklyMeal),
        next_cursor: data.next_cursor
      };
    }
  };
}

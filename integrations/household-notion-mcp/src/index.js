import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import { requireHouseholdAccess } from "./auth.js";
import { createNotion } from "./notion.js";
import { createNotionWriter } from "./writes.js";

function createServer(env) {
  const server = new McpServer({ name: "household-notion", version: "0.2.0" });
  const notion = createNotion(env);

  function register(name, description, inputSchema, fn) {
    server.registerTool(name, { description, inputSchema }, async (args) => {
      try {
        const result = await fn(args);
        return {
          structuredContent: result,
          content: [{ type: "text", text: JSON.stringify(result) }]
        };
      } catch (error) {
        // Don't expose Notion bodies, tokens, or internal stack traces.
        return {
          isError: true,
          content: [{ type: "text", text: error instanceof Error ? error.message : "Request failed" }]
        };
      }
    });
  }

  register(
    "list_recipes",
    "List household recipe titles, base serving yields and Notion page IDs. Follow next_cursor for later pages. Read-only; link-only recipes can require preparation.",
    {
      limit: z.number().int().min(1).max(50).optional(),
      cursor: z.string().min(1).max(300).optional()
    },
    (args) => notion.listRecipes(args)
  );

  register(
    "get_recipe",
    "Read one permitted household recipe, including its four-column Notion ingredient table and numbered cooking instructions. Only complete recipes are returned.",
    { recipe_id: z.string().regex(/^[0-9a-f-]{32,36}$/i) },
    ({ recipe_id }) => notion.getRecipe(recipe_id)
  );

  register(
    "list_weekly_meals",
    "Read dated breakfast, lunch and dinner assignments for an ISO Monday-start week. Breakfast is planning-only: v1 printable receipts support lunch/dinner.",
    {
      week_start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
      cursor: z.string().min(1).max(300).optional()
    },
    (args) => notion.listWeeklyMeals(args)
  );


  // Working-state Notion writes are disabled by default, independently of
  // Cloudflare Access authentication and Notion integration permissions.
  if (env.ENABLE_NOTION_WRITES === "true") {
    const writer = createNotionWriter(env);
    const notionId = z.string().regex(/^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i);
    const ingredient = z.object({
      ingredient: z.string().min(1).max(160),
      quantity: z.string().min(1).max(50),
      unit: z.string().max(40).optional(),
      notes: z.string().max(200).optional()
    });
    register(
      "save_recipe",
      "Create or revise one household Notion recipe with the approved four-column ingredient table and numbered steps. To revise, supply recipe_id and the last_edited_time from get_recipe; the full ingredient/instruction lists replace the prior managed content. This modifies working Notion only—NEVER publishes to GitHub or prints.",
      {
        recipe_id: notionId.optional(),
        expected_last_edited_time: z.string().max(50).optional(),
        name: z.string().min(1).max(160),
        servings: z.number().positive().max(100),
        source_url: z.string().max(1000).nullable().optional(),
        meal_types: z.array(z.string().min(1).max(80)).max(12).optional(),
        diet: z.array(z.string().min(1).max(80)).max(12).optional(),
        best_for: z.array(z.string().min(1).max(80)).max(12).optional(),
        notes: z.string().max(1800).optional(),
        ingredients: z.array(ingredient).min(1).max(50),
        instructions: z.array(z.string().min(1).max(1000)).min(1).max(40)
      },
      (args) => writer.saveRecipe(args)
    );
    register(
      "save_weekly_meal",
      "Create or revise one dated household Weekly Meals entry, linking exactly one complete recipe. To revise, supply meal_id and last_edited_time from list_weekly_meals. Duplicate date/type slots are rejected. Writes working Notion only—not the approved private GitHub print inbox.",
      {
        meal_id: notionId.optional(),
        expected_last_edited_time: z.string().max(50).optional(),
        date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
        meal_type: z.enum(["Breakfast", "Lunch", "Dinner"]),
        title: z.string().min(1).max(160).optional(),
        recipe_id: notionId,
        servings: z.number().positive().max(100).nullable().optional()
      },
      (args) => writer.saveWeeklyMeal(args)
    );
    register(
      "remove_weekly_meal",
      "Archive one authorized Weekly Meals assignment after checking its last_edited_time. Does not delete recipes, modify GitHub publication, or print anything.",
      {
        meal_id: notionId,
        expected_last_edited_time: z.string().min(1).max(50)
      },
      (args) => writer.removeWeeklyMeal(args)
    );
  }

  return server;
}

export default {
  async fetch(request, env, ctx) {
    // Intentionally no public health endpoint or unauthenticated discovery.
    if (new URL(request.url).pathname !== "/mcp") {
      return new Response("Not found", { status: 404 });
    }
    const denied = await requireHouseholdAccess(request, env);
    if (denied) return denied;
    return createMcpHandler(() => createServer(env), {
      route: "/mcp",
      corsOptions: false,
      responseMode: "json"
    })(request, env, ctx);
  }
};

import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import { requireHouseholdAccess } from "./auth.js";
import { createNotion } from "./notion.js";

function createServer(env) {
  const server = new McpServer({ name: "household-notion-readonly", version: "0.1.0" });
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

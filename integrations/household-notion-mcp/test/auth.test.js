import test from "node:test";
import assert from "node:assert/strict";
import { requireHouseholdAccess } from "../src/auth.js";

const env = {
  NOTION_TOKEN: "test-only",
  NOTION_RECIPES_DATA_SOURCE_ID: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  NOTION_MEALS_DATA_SOURCE_ID: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  ACCESS_TEAM_DOMAIN: "example.cloudflareaccess.com",
  ACCESS_AUD: "test-audience",
  ACCESS_ALLOWED_EMAIL: "tester@example.com"
};

test("fails closed before Cloudflare Access setup", async () => {
  const result = await requireHouseholdAccess(new Request("https://example.com/mcp"), {});
  assert.equal(result.status, 503);
});

test("denies requests missing Access assertion", async () => {
  const result = await requireHouseholdAccess(new Request("https://example.com/mcp"), env);
  assert.equal(result.status, 401);
});

test("rejects malformed assertion and never accesses Notion", async () => {
  const request = new Request("https://example.com/mcp", {
    headers: { "Cf-Access-Jwt-Assertion": "not-a-jwt" }
  });
  const result = await requireHouseholdAccess(request, env);
  assert.equal(result.status, 401);
});

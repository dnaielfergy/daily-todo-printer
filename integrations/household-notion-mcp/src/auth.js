import { createRemoteJWKSet, jwtVerify } from "jose";

// Cloudflare Access must protect the Worker custom domain and issue OAuth tokens
// to the MCP client. We additionally verify its signed assertion inside the Worker,
// so workers.dev or a misconfigured Access policy cannot silently expose household data.
export async function requireHouseholdAccess(request, env) {
  const required = [
    "NOTION_TOKEN", "NOTION_RECIPES_DATA_SOURCE_ID", "NOTION_MEALS_DATA_SOURCE_ID",
    "ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "ACCESS_ALLOWED_EMAIL"
  ];
  if (required.some((name) => !env[name])) {
    return new Response("Household MCP is not configured", { status: 503 });
  }
  const assertion = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!assertion) {
    return new Response("Access authentication required", {
      status: 401,
      headers: { "Cache-Control": "no-store" }
    });
  }
  try {
    const origin = "https://" + env.ACCESS_TEAM_DOMAIN;
    const jwks = createRemoteJWKSet(new URL(origin + "/cdn-cgi/access/certs"));
    const { payload } = await jwtVerify(assertion, jwks, {
      issuer: origin,
      audience: env.ACCESS_AUD,
      algorithms: ["RS256"]
    });
    const allowed = env.ACCESS_ALLOWED_EMAIL.trim().toLowerCase();
    if (typeof payload.email !== "string" || payload.email.toLowerCase() !== allowed) {
      return new Response("Access denied", { status: 403 });
    }
    return null;
  } catch {
    return new Response("Invalid Access assertion", { status: 401 });
  }
}

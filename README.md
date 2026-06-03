# Sentry MCP Server

MCP server for Sentry — exposes projects, issues, and performance traces as tools for Claude Code (or any MCP client).

> **Auth model: per-user.** Each user sends their **own Sentry token** in the `X-Sentry-Token` header and the server uses that token for that request. There is no shared server token — everyone authenticates as themselves instead of going through one person's token.

## Run

```bash
cp .env.example .env
# edit .env — only the shared (non-secret) bits:
SENTRY_ORG=your-org-slug
SENTRY_BASE_URL=https://sentry.your-company.com

docker compose up -d --build
```

Check it's up: `curl http://localhost:8765/health` → `ok`

Each user's Sentry token needs scopes: `org:read`, `project:read`, `event:read`, `member:read` — create at `<base-url>/settings/account/api/auth-tokens/`.

## Connect to Claude Code

Add to `~/.claude.json` — put **your own** token in the header:

```json
{
  "mcpServers": {
    "sentry": {
      "type": "http",
      "url": "http://localhost:8765/mcp",
      "headers": { "X-Sentry-Token": "sntryu_YOUR_OWN_TOKEN" }
    }
  }
}
```

Restart Claude Code. Done.

> A request to `/mcp` without an `X-Sentry-Token` header gets a **401** with a hint.

## Commands

```bash
docker compose up -d --build    # start / rebuild
docker compose restart          # after .env change
docker compose down             # stop
docker logs -f sentry-mcp       # logs
```

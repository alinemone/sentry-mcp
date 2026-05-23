# Sentry MCP Server

MCP server for Sentry — exposes projects, issues, and performance traces as tools for Claude Code (or any MCP client).

## Run

```bash
cp .env.example .env
# edit .env (see below)
SENTRY_TOKEN=sntryu_...                       # token with read scopes
SENTRY_ORG=your-org-slug
SENTRY_BASE_URL=https://sentry.your-company.com
MCP_API_KEY=any-long-random-string            # protects the HTTP endpoint

docker compose up -d --build
```

Check it's up: `curl http://localhost:8765/health` → `ok`


Sentry token needs scopes: `org:read`, `project:read`, `event:read`, `member:read` — create at `<base-url>/settings/account/api/auth-tokens/`.

## Connect to Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "sentry": {
      "type": "http",
      "url": "http://localhost:8765/mcp?api_key=YOUR_MCP_API_KEY"
    }
  }
}
```

Restart Claude Code. Done.

## Commands

```bash
docker compose up -d --build    # start / rebuild
docker compose restart          # after .env change
docker compose down             # stop
docker logs -f sentry-mcp       # logs
```

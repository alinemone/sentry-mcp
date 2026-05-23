# Sentry MCP Server

A Model Context Protocol (MCP) server that exposes Sentry data — projects, issues, performance traces — as tools an LLM client (Claude Code, Claude Desktop, etc.) can call.

Runs as an HTTP service inside Docker, with API-key auth.

## Features

- **Multi-project**: pick any project per call, or set a default
- **Issues**: recent errors, full issue details with stacktraces and breadcrumbs
- **Performance**: slow transactions, route analysis, deep-span traces, performance overview
- **Discovery**: `list_projects` tool to enumerate every Sentry project you can read

## Available Tools

| Tool | Purpose |
|---|---|
| `list_projects` | List all Sentry projects in your org |
| `get_recent_issues` | Recent unresolved errors for a project |
| `get_issue_details` | Stacktraces, breadcrumbs, tags for a specific issue |
| `get_slow_transactions` | Top slow routes for a project |
| `get_performance_overview` | All-route performance summary |
| `analyze_route_performance` | Stats for one specific route |
| `analyze_transaction_trace` | All spans of a single event |
| `get_route_detailed_traces` | Detailed spans across slow events for a route |

## Prerequisites

- Docker + Docker Compose
- A Sentry auth token with read scopes (`org:read`, `project:read`, `event:read`, `member:read`)
  - Create at `https://<your-sentry>/settings/account/api/auth-tokens/`

## Quick Start

```bash
# 1. Clone & enter
git clone <this-repo-url> sentry-mcp
cd sentry-mcp

# 2. Create .env from the template
cp .env.example .env

# 3. Generate a strong API key for the MCP endpoint
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Paste the output into MCP_API_KEY in .env

# 4. Edit .env and fill in SENTRY_TOKEN, SENTRY_ORG, SENTRY_BASE_URL
#    (optionally SENTRY_DEFAULT_PROJECT_SLUG)

# 5. Build & start
docker compose up -d --build

# 6. Verify
curl http://localhost:8765/healthz       # → ok
docker logs sentry-mcp                   # → "Application startup complete."
```

## Configuration

All config lives in `.env`. See `.env.example` for the full reference.

| Variable | Required | Description |
|---|---|---|
| `SENTRY_TOKEN` | yes | Sentry auth token with read scopes |
| `SENTRY_ORG` | yes | Org slug (from your Sentry URL) |
| `SENTRY_BASE_URL` | yes | e.g. `https://sentry.your-company.com` |
| `SENTRY_DEFAULT_PROJECT_SLUG` | no | Default project if a tool call omits `project` |
| `MCP_API_KEY` | yes | Long random string — clients must send this |
| `MCP_PORT` | no | Host port (default `8765`) |

## Connecting an MCP Client

Once the container is running on `http://localhost:8765`, register it in your client.

### Claude Code (`~/.claude.json`)

```jsonc
{
  "mcpServers": {
    "sentry": {
      "type": "http",
      "url": "http://localhost:8765/mcp?api_key=YOUR_MCP_API_KEY"
    }
  }
}
```

Restart Claude Code. The eight tools above will be available as `sentry__<tool_name>`.

### Other clients

Any client that speaks Streamable HTTP MCP. The API key may also be passed via the `X-API-Key` header instead of the query string.

## Common Commands

```bash
docker compose up -d --build   # rebuild and start
docker compose restart         # restart (after .env change)
docker compose down            # stop and remove container
docker logs -f sentry-mcp      # tail logs
```

## Example Prompts

- "List my Sentry projects"
- "Show the latest unresolved issues for the `order` project"
- "What are the slowest routes in `order-processing` over the last 7 days?"
- "Give me the stacktrace for issue 1393177"
- "Analyze route `/api/v1/checkout` — show spans for slow requests"

## Project Layout

```
.
├── docker-compose.yml      # Service definition
├── Dockerfile              # python:3.12-slim image
├── requirements.txt        # Pinned deps (mirrors pyproject.toml)
├── pyproject.toml          # Package metadata + deps
├── .env.example            # Config template
└── sentry_mcp/
    ├── __main__.py         # Entry point — launches HTTP server
    ├── http_server.py      # Starlette app + API-key middleware
    ├── server.py           # MCP Server with tool definitions
    └── client.py           # Sentry REST API wrapper
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` on `/mcp` | API key in URL doesn't match `MCP_API_KEY` |
| `403` from Sentry in logs | Token is missing read scopes |
| Container restarts in a loop | Required env var (`SENTRY_TOKEN` / `MCP_API_KEY`) not set |
| `list_projects` works but `get_issues` returns empty | Wrong `project` slug — call `list_projects` first |

## Security Notes

- `MCP_API_KEY` is the only thing protecting your Sentry data from anything that can reach the port. Don't expose port `8765` to the public internet without a reverse proxy + TLS.
- `docker-compose.yml` binds to `127.0.0.1:8765` by default — change at your own risk.
- Never commit `.env`. `.gitignore` already excludes it.

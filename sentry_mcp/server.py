"""MCP Server for Sentry Performance and Issues Analysis."""

import contextvars
import threading
from typing import Any
from mcp.server import Server
from mcp.types import Tool, TextContent

from .client import SentryClient


server = Server("sentry-mcp")

# Per-request Sentry token, populated by the HTTP layer's PerUserTokenMiddleware
# from the incoming header. call_tool runs in the same async task that served
# the request, so it sees the value set here.
_token_var: contextvars.ContextVar[str] = contextvars.ContextVar("sentry_token", default="")

# One SentryClient per distinct token (org/base_url come from shared env).
_clients_lock = threading.Lock()
_clients: dict[str, SentryClient] = {}


def get_client() -> SentryClient:
    """Return a SentryClient bound to the current request's token (from the
    X-Sentry-Token header)."""
    token = _token_var.get()
    if not token:
        raise ValueError(
            "missing Sentry token — send your token in the 'X-Sentry-Token' header"
        )
    with _clients_lock:
        client = _clients.get(token)
        if client is None:
            client = SentryClient(token=token)  # org / base_url / default slug from env
            _clients[token] = client
        return client


_PROJECT_PROPERTY = {
    "type": "string",
    "description": (
        "Sentry project slug (e.g. 'backend-api'). "
        "Use list_projects to discover available slugs. "
        "Optional if SENTRY_DEFAULT_PROJECT_SLUG is set."
    ),
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_projects",
            description=(
                "List all Sentry projects available in the organization. "
                "Call this first to discover which 'project' slug to pass to other tools."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_slow_transactions",
            description=(
                "Get slow API endpoints with detailed performance statistics for a project. "
                "Useful for identifying bottlenecks and performance issues."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": _PROJECT_PROPERTY,
                    "threshold_ms": {
                        "type": "integer",
                        "description": "Minimum duration in ms to consider a transaction slow",
                        "default": 2000,
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period (e.g., '24h', '7d', '14d')",
                        "default": "24h",
                    },
                },
            },
        ),
        Tool(
            name="analyze_transaction_trace",
            description=(
                "Deep dive into a specific transaction to see all operations (spans) "
                "and identify which parts are taking the most time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Sentry event ID"},
                    "project": _PROJECT_PROPERTY,
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="get_performance_overview",
            description="Get overall performance metrics for all API endpoints in a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": _PROJECT_PROPERTY,
                    "period": {"type": "string", "default": "24h"},
                },
            },
        ),
        Tool(
            name="get_recent_issues",
            description=(
                "Get recent errors and exceptions from Sentry for a project. "
                "By default filters for unresolved issues with high or medium priority."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": _PROJECT_PROPERTY,
                    "period": {"type": "string", "default": "24h"},
                    "limit": {"type": "integer", "default": 50},
                    "query": {
                        "type": "string",
                        "default": "is:unresolved issue.priority:[high, medium]",
                    },
                },
            },
        ),
        Tool(
            name="get_issue_details",
            description=(
                "Get detailed info about a specific issue including stack traces, "
                "breadcrumbs, tags, and the latest event data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Sentry issue ID"},
                    "project": _PROJECT_PROPERTY,
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="analyze_route_performance",
            description="Analyze performance of a specific API route/endpoint in a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "route": {"type": "string", "description": "Route pattern"},
                    "project": _PROJECT_PROPERTY,
                    "period": {"type": "string", "default": "24h"},
                },
                "required": ["route"],
            },
        ),
        Tool(
            name="get_route_detailed_traces",
            description=(
                "Get detailed traces with all spans for a specific route. "
                "Shows where time is spent in slow requests."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "route": {"type": "string"},
                    "project": _PROJECT_PROPERTY,
                    "period": {"type": "string", "default": "24h"},
                    "threshold_ms": {"type": "integer", "default": 2000},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["route"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    try:
        client = get_client()
        project = arguments.get("project") if arguments else None

        if name == "list_projects":
            projects = client.list_projects()
            if not projects:
                return [TextContent(type="text", text="No projects found.")]

            output = f"📁 Sentry Projects ({len(projects)} total)\n\n"
            for p in sorted(projects, key=lambda x: x.get("slug") or ""):
                slug = p.get("slug", "?")
                pid = p.get("id", "?")
                name_ = p.get("name", slug)
                platform = p.get("platform") or "n/a"
                output += f"- {slug}  (id={pid}, platform={platform})  — {name_}\n"
            output += "\n💡 Pass `project=<slug>` to other tools.\n"
            return [TextContent(type="text", text=output)]

        elif name == "get_slow_transactions":
            threshold = arguments.get("threshold_ms", 2000)
            period = arguments.get("period", "24h")
            result = client.analyze_slow_transactions(
                project_slug=project, threshold_ms=threshold, period=period
            )

            if "error" in result and "slow_routes" not in result:
                return [TextContent(type="text", text=f"❌ {result['error']}")]

            output = f"""🐌 Slow Transactions Analysis ({period})
📁 Project: {result.get('project', 'N/A')}

📊 Summary:
- Total Transactions: {result['total_transactions']}
- Total Routes: {result['total_routes']}
- Slow Routes (>{threshold}ms): {result['slow_routes_count']}

"""
            if result["slow_routes"]:
                output += "🔥 Top Slow Routes:\n\n"
                for i, route_data in enumerate(result["slow_routes"][:10], 1):
                    output += f"""{i}. {route_data['route']}
   📈 Stats:
      Method: {route_data['http_method']} | Operation: {route_data['transaction_op']}
      P50: {route_data['p50_ms']}ms
      P95: {route_data['p95_ms']}ms
      TPM: {route_data['tpm']} requests/min
      Failure Rate: {route_data['failure_rate']}%

"""
            else:
                output += "✅ No slow routes found!\n"

            return [TextContent(type="text", text=output)]

        elif name == "analyze_transaction_trace":
            event_id = arguments.get("event_id")
            if not event_id:
                return [TextContent(type="text", text="❌ Error: event_id is required")]

            result = client.get_transaction_trace(event_id, project_slug=project)
            if "error" in result:
                return [TextContent(type="text", text=f"❌ Error: {result['error']}")]

            output = f"""🔍 Transaction Trace Analysis
📁 Project: {result.get('project', 'N/A')}

📋 Transaction: {result['transaction']}
⏱️  Total Duration: {result['total_duration_ms']:.0f}ms
📅 Timestamp: {result['timestamp']}
🔢 Total Spans: {result['spans_count']}

⚡ Top Slowest Operations (Spans):

"""
            for i, span in enumerate(result["spans"][:10], 1):
                output += f"""{i}. [{span['op']}] {span['description'][:80]}
   Duration: {span['duration_ms']:.2f}ms
   Tags: {span.get('tags', {})}

"""
            return [TextContent(type="text", text=output)]

        elif name == "get_performance_overview":
            period = arguments.get("period", "24h")
            result = client.analyze_slow_transactions(
                project_slug=project, threshold_ms=0, period=period
            )

            output = f"""📊 Performance Overview ({period})
📁 Project: {result.get('project', 'N/A')}

Total Transactions: {result['total_transactions']}
Total Routes: {result['total_routes']}

📈 All Routes Performance:

"""
            for i, route_data in enumerate(result["slow_routes"], 1):
                output += f"""{i}. {route_data['route']}
   Method: {route_data['http_method']} | Operation: {route_data['transaction_op']}
   P50: {route_data['p50_ms']}ms | P95: {route_data['p95_ms']}ms
   TPM: {route_data['tpm']} requests/min | Failure Rate: {route_data['failure_rate']}%

"""
            return [TextContent(type="text", text=output)]

        elif name == "get_recent_issues":
            period = arguments.get("period", "24h")
            limit = arguments.get("limit", 50)
            query = arguments.get("query", "is:unresolved issue.priority:[high, medium]")

            issues = client.get_issues(
                project_slug=project, period=period, limit=limit, query=query
            )

            if not issues:
                return [TextContent(type="text", text="✅ No issues found!")]

            output = f"""🐛 Recent Issues ({period})
📁 Project: {project or client.default_project_slug or 'N/A'}

Query: {query}
Total Issues: {len(issues)}

"""
            for i, issue in enumerate(issues[:20], 1):
                priority = issue.get("priority", "unknown")
                output += f"""{i}. {issue.get('title', 'Unknown')}
   ID: {issue.get('id', 'N/A')}
   Priority: {priority} | Level: {issue.get('level', 'unknown')}
   Count: {issue.get('count', 0)} events
   First Seen: {issue.get('firstSeen', 'N/A')}
   Last Seen: {issue.get('lastSeen', 'N/A')}
   Status: {issue.get('status', 'unknown')}

"""
            return [TextContent(type="text", text=output)]

        elif name == "get_issue_details":
            issue_id = arguments.get("issue_id")
            if not issue_id:
                return [TextContent(type="text", text="❌ Error: issue_id is required")]

            try:
                issue = client.get_issue_details(issue_id, project_slug=project)

                output = f"""🐛 Issue Details

Title: {issue.get('title', 'Unknown')}
ID: {issue.get('id', 'N/A')}
Status: {issue.get('status', 'unknown')}
Level: {issue.get('level', 'unknown')}
Type: {issue.get('type', 'unknown')}

📊 Statistics:
- Total Events: {issue.get('count', 0)}
- Unique Users Affected: {issue.get('userCount', 0)}
- First Seen: {issue.get('firstSeen', 'N/A')}
- Last Seen: {issue.get('lastSeen', 'N/A')}

📍 Metadata:
- Project: {issue.get('project', {}).get('name', 'N/A')}
- Platform: {issue.get('platform', 'N/A')}
- Culprit: {issue.get('culprit', 'N/A')}

"""

                if issue.get("tags"):
                    output += "🏷️  Tags:\n"
                    for tag in issue["tags"][:10]:
                        output += f"   - {tag.get('key')}: {tag.get('value')}\n"
                    output += "\n"

                if issue.get("latestEventDetails"):
                    event = issue["latestEventDetails"]
                    output += f"""📋 Latest Event:
Event ID: {event.get('eventID', 'N/A')}
Timestamp: {event.get('dateCreated', 'N/A')}

"""
                    entries = event.get("entries", [])
                    exception_found = False

                    for entry in entries:
                        if entry.get("type") == "exception":
                            exception_found = True
                            output += "🔍 Exception Details:\n\n"
                            values = entry.get("data", {}).get("values", [])

                            for idx, exc in enumerate(values, 1):
                                if idx > 1:
                                    output += "\n" + "─" * 60 + "\n\n"
                                output += f"Exception #{idx}:\n"
                                output += f"   Type: {exc.get('type', 'Unknown')}\n"
                                output += f"   Value: {exc.get('value', 'N/A')}\n"

                                mechanism = exc.get("mechanism", {})
                                if mechanism:
                                    output += f"   Handled: {mechanism.get('handled', 'N/A')}\n"

                                stacktrace = exc.get("stacktrace", {})
                                frames = stacktrace.get("frames", [])

                                if frames:
                                    output += f"\n   📚 Stack Trace ({len(frames)} frames):\n"
                                    for frame in frames[-5:]:
                                        filename = frame.get("filename", "unknown")
                                        function = frame.get("function", "unknown")
                                        lineno = frame.get("lineNo", "N/A")
                                        in_app = frame.get("inApp", False)
                                        marker = "→" if in_app else " "
                                        output += f"   {marker}  {filename}:{lineno}\n"
                                        output += f"      in {function}()\n"

                                        context = frame.get("context", [])
                                        if context and in_app:
                                            output += "      Code:\n"
                                            for line_no, line_code in context[-3:]:
                                                prefix = ">>>" if line_no == lineno else "   "
                                                output += f"      {prefix} {line_no}: {line_code}\n"
                                        output += "\n"
                                output += "\n"

                    if not exception_found:
                        output += "ℹ️  No exception details available in latest event\n\n"

                    breadcrumbs_found = False
                    for entry in entries:
                        if entry.get("type") == "breadcrumbs":
                            breadcrumbs_found = True
                            output += "🍞 Breadcrumbs:\n\n"
                            breadcrumbs_data = entry.get("data", {}).get("values", [])
                            for crumb in breadcrumbs_data[-3:]:
                                timestamp = crumb.get("timestamp", "")
                                if timestamp and "T" in str(timestamp):
                                    timestamp = str(timestamp).split("T")[1][:8]
                                category = crumb.get("category", "default")
                                level = crumb.get("level", "info")
                                message = crumb.get("message", "")
                                level_icon = {
                                    "error": "❌",
                                    "warning": "⚠️",
                                    "info": "ℹ️",
                                    "debug": "🔍",
                                }.get(level, "•")
                                output += f"   {level_icon} [{timestamp}] [{category}]"
                                if message:
                                    output += f" {message}"
                                output += "\n"
                                data = crumb.get("data", {})
                                if data:
                                    for key, value in data.items():
                                        output += f"      {key}: {value}\n"
                            output += "\n"
                            break

                    if not breadcrumbs_found:
                        output += "ℹ️  No breadcrumbs available in latest event\n\n"

                output += f"\n🔗 View in Sentry: {issue.get('permalink', 'N/A')}\n"
                return [TextContent(type="text", text=output)]

            except Exception as e:
                return [TextContent(type="text", text=f"❌ Error fetching issue details: {str(e)}")]

        elif name == "analyze_route_performance":
            route = arguments.get("route")
            period = arguments.get("period", "24h")
            if not route:
                return [TextContent(type="text", text="❌ Error: route is required")]

            result = client.analyze_slow_transactions(
                project_slug=project, threshold_ms=0, period=period
            )

            route_data = None
            for r in result.get("slow_routes", []):
                if r["route"] == route:
                    route_data = r
                    break

            if not route_data:
                return [
                    TextContent(
                        type="text",
                        text=f"❌ Route '{route}' not found in project {result.get('project', '?')} for period {period}",
                    )
                ]

            output = f"""📊 Route Performance Analysis

Project: {result.get('project', 'N/A')}
Route: {route}
Period: {period}

📈 Statistics:
- HTTP Method: {route_data['http_method']}
- Transaction Operation: {route_data['transaction_op']}
- P50 Duration: {route_data['p50_ms']}ms
- P95 Duration: {route_data['p95_ms']}ms
- Throughput (TPM): {route_data['tpm']} requests/min
- Failure Rate: {route_data['failure_rate']}%

💡 Use get_route_detailed_traces to see detailed breakdown.
"""
            return [TextContent(type="text", text=output)]

        elif name == "get_route_detailed_traces":
            route = arguments.get("route")
            period = arguments.get("period", "24h")
            threshold_ms = arguments.get("threshold_ms", 2000)
            limit = arguments.get("limit", 5)
            if not route:
                return [TextContent(type="text", text="❌ Error: route is required")]

            result = client.get_route_detailed_traces(
                route=route,
                project_slug=project,
                period=period,
                threshold_ms=threshold_ms,
                limit=limit,
            )

            if "error" in result and "traces" not in result:
                return [TextContent(type="text", text=f"❌ Error: {result['error']}")]

            output = f"""🔍 Detailed Trace Analysis for Route

📁 Project: {result.get('project', 'N/A')}
📋 Route: {result['route']}
⏱️  Period: {result['period']}
🎯 Threshold: >{result['threshold_ms']}ms
📊 Total Events: {result['total_events']}
🐌 Slow Events: {result['slow_events_count']}
🔎 Traces Analyzed: {result['traces_analyzed']}

"""

            if not result["traces"]:
                output += result.get("message", "No slow traces found.")
                return [TextContent(type="text", text=output)]

            for i, trace in enumerate(result["traces"], 1):
                output += f"""
{'='*70}
🔥 Trace #{i}
{'='*70}
Event ID: {trace['event_id']}
Total Duration: {trace['total_duration_ms']:.0f}ms
Timestamp: {trace['timestamp']}
Spans Count: {trace['spans_count']}

⚡ Top Slowest Operations:

"""
                for j, span in enumerate(trace["spans"][:10], 1):
                    desc = span["description"][:100]
                    output += f"""{j}. [{span['op']}] {desc}
   Duration: {span['duration_ms']:.2f}ms
   Tags: {span.get('tags', {})}

"""

            output += f"\n{'='*70}\n"
            output += "💡 Focus on the slowest spans to optimize your code!\n"
            return [TextContent(type="text", text=output)]

        else:
            return [TextContent(type="text", text=f"❌ Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

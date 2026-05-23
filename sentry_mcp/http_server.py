"""HTTP (Streamable HTTP) entry point for the Sentry MCP server."""

import contextlib
import logging
import os

import uvicorn
from dotenv import load_dotenv
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .server import server

logger = logging.getLogger(__name__)


class ApiKeyMiddleware:
    """Reject requests to /mcp without the right ?api_key=… query param."""

    def __init__(self, app: ASGIApp, api_key: str, protected_path: str = "/mcp") -> None:
        self.app = app
        self.api_key = api_key
        self.protected_path = protected_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self.protected_path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        provided = request.query_params.get("api_key") or request.headers.get("x-api-key")
        if provided != self.api_key:
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def healthz(_request: Request) -> Response:
    return PlainTextResponse("ok")


def build_app(api_key: str) -> Starlette:
    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=False,
        stateless=True,
    )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with session_manager.run():
            logger.info("MCP session manager started")
            yield
            logger.info("MCP session manager stopped")

    app = Starlette(
        debug=False,
        routes=[
            Route("/health", endpoint=healthz),
            Mount("/mcp", app=handle_mcp),
        ],
        lifespan=lifespan,
    )

    return ApiKeyMiddleware(app, api_key=api_key, protected_path="/mcp")


def main() -> None:
    load_dotenv()

    api_key = os.getenv("MCP_API_KEY")
    if not api_key:
        raise SystemExit(
            "MCP_API_KEY is required for HTTP mode. Set it in .env (long random string)."
        )

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    app = build_app(api_key=api_key)
    logger.info(f"Starting Sentry MCP HTTP server on {host}:{port} (path /mcp)")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

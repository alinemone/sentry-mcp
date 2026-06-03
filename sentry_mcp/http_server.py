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

from .server import server, _token_var

logger = logging.getLogger(__name__)

TOKEN_HEADER = os.getenv("SENTRY_TOKEN_HEADER", "X-Sentry-Token").lower()


class PerUserTokenMiddleware:
    """Pull each user's Sentry token from the request header into a contextvar
    for the duration of the request, so every tool call authenticates as the
    caller instead of a single shared server token.

    Replaces the old shared ?api_key gate: the user's own token IS the auth now
    (an invalid token is rejected by Sentry).
    """

    def __init__(self, app: ASGIApp, header_name: str = TOKEN_HEADER, protected_path: str = "/mcp") -> None:
        self.app = app
        self.header = header_name.lower().encode()
        self.protected_path = protected_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.protected_path):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        token = headers.get(self.header, b"").decode().strip()
        if not token:
            auth = headers.get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()

        if not token:
            response = PlainTextResponse(
                f"missing Sentry token — send your token in the '{TOKEN_HEADER}' header",
                status_code=401,
            )
            await response(scope, receive, send)
            return

        reset = _token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _token_var.reset(reset)


async def healthz(_request: Request) -> Response:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
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

    return PerUserTokenMiddleware(app, protected_path="/mcp")


def main() -> None:
    load_dotenv()

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    app = build_app()
    logger.info(
        f"Starting Sentry MCP HTTP server on {host}:{port} "
        f"(path /mcp, per-user token via '{TOKEN_HEADER}' header)"
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

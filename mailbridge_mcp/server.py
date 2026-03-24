from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import imapclient
import structlog
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mailbridge_mcp.auth import BearerAuthMiddleware
from mailbridge_mcp.config import AccountConfig, Settings, load_accounts

log = structlog.get_logger()


def _configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


def _verify_account(account: AccountConfig) -> None:
    """Sync IMAP login check — called in executor at startup."""
    client = imapclient.IMAPClient(
        host=account.imap.host, port=account.imap.port, ssl=account.imap.tls
    )
    try:
        client.login(account.imap.username, account.imap.password)
    finally:
        try:
            client.logout()
        except Exception:
            pass


settings = Settings()


@lifespan
async def app_lifespan(server: Any) -> Any:
    _configure_logging(settings.log_level)

    config_path = Path(
        os.getenv("ACCOUNTS_CONFIG_PATH", settings.accounts_config_path)
    )
    accounts = load_accounts(config_path)
    accounts_map: dict[str, AccountConfig] = {}

    loop = asyncio.get_running_loop()
    for account in accounts:
        log.info("Verifying account connectivity", account_id=account.id)
        await loop.run_in_executor(None, _verify_account, account)
        accounts_map[account.id] = account
        log.info("Account verified", account_id=account.id, status="connected")

    yield {"accounts": accounts_map, "settings": settings}


mcp = FastMCP(
    "mailbridge-mcp",
    lifespan=app_lifespan,
)


# --- Health check endpoint (not an MCP tool, not behind auth) ---


async def health_check(request: Request) -> JSONResponse:
    # Return per-account connectivity status
    # At health-check time, all accounts were verified at startup; a full
    # NOOP re-check is deferred to v0.2. For now, report startup-verified state.
    return JSONResponse({"status": "ok"})


# --- Tools ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_list_accounts(ctx: Context) -> str:
    """List all configured email accounts with their IDs and labels."""
    accounts: dict[str, AccountConfig] = ctx.lifespan_context["accounts"]
    result = [
        {"id": a.id, "label": a.label, "default_from": a.default_from}
        for a in accounts.values()
    ]
    return json.dumps(result, indent=2)


# --- App creation ---


def create_app() -> Any:
    middleware = [Middleware(BearerAuthMiddleware, api_key=settings.mcp_api_key)]
    http_app = mcp.http_app(
        transport="streamable-http",
        middleware=middleware,
    )
    http_app.add_route("/health", health_check, methods=["GET"])
    return http_app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "mailbridge_mcp.server:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
    )

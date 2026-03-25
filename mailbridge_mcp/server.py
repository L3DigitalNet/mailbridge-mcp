from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import imapclient
import structlog
from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from mailbridge_mcp.config import AccountConfig, Settings, load_accounts
from mailbridge_mcp.tools_read import (
    get_message,
    get_thread,
    list_accounts,
    list_folders,
    list_messages,
    search_messages,
)
from mailbridge_mcp.tools_write import (
    delete_message,
    move_message,
    reply_tool,
    send_email_tool,
    set_flags,
)

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
    accounts_list = load_accounts(config_path)
    accounts_map: dict[str, AccountConfig] = {}

    loop = asyncio.get_running_loop()
    for account in accounts_list:
        log.info("Verifying account connectivity", account_id=account.id)
        await loop.run_in_executor(None, _verify_account, account)
        accounts_map[account.id] = account
        log.info("Account verified", account_id=account.id, status="connected")

    yield {"accounts": accounts_map, "settings": settings}


# GitHub OAuth for Claude.ai web access (handles full OAuth 2.1 flow)
_github_client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
_github_client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")

auth_provider = (
    GitHubProvider(
        client_id=_github_client_id,
        client_secret=_github_client_secret,
        base_url=f"https://{os.getenv('MCP_PUBLIC_HOST', 'localhost')}",
    )
    if _github_client_id and _github_client_secret
    else None
)

mcp = FastMCP(
    "mailbridge-mcp",
    auth=auth_provider,
    lifespan=app_lifespan,
)


# --- Health check endpoint (not an MCP tool, not behind auth) ---


async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# --- Read tools ---


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_list_accounts(ctx: Context) -> str:
    """List all configured email accounts with their IDs and labels."""
    return await list_accounts(ctx.lifespan_context["accounts"])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_list_folders(account_id: str, ctx: Context) -> str:
    """List all IMAP folders/mailboxes for an account."""
    return await list_folders(ctx.lifespan_context["accounts"], account_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_list_messages(
    account_id: str,
    ctx: Context,
    folder: str = "INBOX",
    limit: int = 20,
    offset: int = 0,
    unread_only: bool = False,
    sort_by: str = "date_desc",
    response_format: str = "markdown",
) -> str:
    """List messages in a folder with summary metadata. Supports pagination."""
    return await list_messages(
        ctx.lifespan_context["accounts"],
        account_id, folder, limit, offset, unread_only, sort_by, response_format,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_get_message(
    account_id: str,
    folder: str,
    uid: int,
    ctx: Context,
    prefer_plain: bool = True,
    include_headers: bool = False,
    response_format: str = "json",
) -> str:
    """Fetch the full content of a single message by UID."""
    return await get_message(
        ctx.lifespan_context["accounts"],
        account_id, folder, uid, prefer_plain, include_headers, response_format,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_search_messages(
    account_id: str,
    ctx: Context,
    folder: str = "INBOX",
    query: str = "",
    from_address: str | None = None,
    to_address: str | None = None,
    subject: str | None = None,
    since_date: str | None = None,
    before_date: str | None = None,
    is_unread: bool | None = None,
    is_flagged: bool | None = None,
    limit: int = 20,
    offset: int = 0,
    response_format: str = "markdown",
) -> str:
    """Search messages using IMAP SEARCH criteria."""
    return await search_messages(
        ctx.lifespan_context["accounts"],
        account_id, folder, query, from_address, to_address, subject,
        since_date, before_date, is_unread, is_flagged, limit, offset,
        response_format,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def imap_get_thread(
    account_id: str,
    folder: str,
    uid: int,
    ctx: Context,
    limit: int = 20,
    offset: int = 0,
    response_format: str = "markdown",
) -> str:
    """Fetch all messages in a thread via Message-ID / References headers."""
    return await get_thread(
        ctx.lifespan_context["accounts"],
        account_id, folder, uid, limit, offset, response_format,
    )


# --- Write tools ---


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
)
async def imap_send_email(
    account_id: str,
    to: list[str],
    subject: str,
    body: str,
    ctx: Context,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
) -> str:
    """Compose and send a new email via SMTP."""
    return await send_email_tool(
        ctx.lifespan_context["accounts"],
        account_id, to, subject, body, cc, bcc, reply_to,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
)
async def imap_reply(
    account_id: str,
    folder: str,
    uid: int,
    body: str,
    ctx: Context,
    reply_all: bool = False,
    include_original: bool = True,
) -> str:
    """Reply to an existing message, preserving threading headers."""
    return await reply_tool(
        ctx.lifespan_context["accounts"],
        account_id, folder, uid, body, reply_all, include_original,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
async def imap_move_message(
    account_id: str,
    folder: str,
    uid: int,
    destination_folder: str,
    ctx: Context,
) -> str:
    """Move a message to a different folder."""
    return await move_message(
        ctx.lifespan_context["accounts"],
        account_id, folder, uid, destination_folder,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
async def imap_delete_message(
    account_id: str,
    folder: str,
    uid: int,
    ctx: Context,
) -> str:
    """Move a message to Trash (does NOT permanently expunge)."""
    return await delete_message(
        ctx.lifespan_context["accounts"],
        account_id, folder, uid,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
async def imap_set_flags(
    account_id: str,
    folder: str,
    uids: list[int],
    ctx: Context,
    mark_read: bool | None = None,
    mark_flagged: bool | None = None,
) -> str:
    """Mark messages as read, unread, flagged, or unflagged."""
    return await set_flags(
        ctx.lifespan_context["accounts"],
        account_id, folder, uids, mark_read, mark_flagged,
    )


# --- App creation ---


def create_app() -> Any:
    http_app = mcp.http_app(transport="streamable-http")
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

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import imapclient
import structlog

from mailbridge_mcp.config import AccountConfig
from mailbridge_mcp.smtp_client import RateLimiter

log = structlog.get_logger()

# Per-account IMAP rate limiters: prevent overwhelming mail servers with connections.
# Default 60 ops/min per account (each op opens a fresh TCP+TLS connection).
_imap_rate_limiters: dict[str, RateLimiter] = {}
_imap_rate_limit = int(os.getenv("IMAP_RATE_LIMIT", "60"))


def _get_imap_limiter(account_id: str) -> RateLimiter:
    if account_id not in _imap_rate_limiters:
        _imap_rate_limiters[account_id] = RateLimiter(max_per_minute=_imap_rate_limit)
    return _imap_rate_limiters[account_id]


@contextmanager
def imap_connection(account: AccountConfig) -> Generator[Any]:
    """Open an IMAP connection, yield the client, then close. One per tool call."""
    client = imapclient.IMAPClient(
        host=account.imap.host,
        port=account.imap.port,
        ssl=account.imap.tls,
    )
    try:
        client.login(account.imap.username, account.imap.password)
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def run_imap(
    account: AccountConfig,
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run a sync IMAP operation in an executor with timeout and single retry.

    Retries once on ConnectionError/OSError (transient network failures).
    Auth failures, timeouts, and IMAP protocol errors are NOT retried.
    Rate-limited per account (default 60 ops/min).
    """
    limiter = _get_imap_limiter(account.id)
    if not limiter.check():
        raise RuntimeError(f"IMAP rate limit exceeded for account {account.id}")

    loop = asyncio.get_running_loop()
    timeout = int(os.getenv("IMAP_TIMEOUT", "30"))

    def _run() -> Any:
        with imap_connection(account) as client:
            return operation(client, *args, **kwargs)

    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run), timeout=timeout
        )
    except (ConnectionError, OSError):
        await asyncio.sleep(1)
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run), timeout=timeout
        )


def get_uidvalidity(client: Any, folder: str) -> int:
    """SELECT a folder and return its UIDVALIDITY value."""
    result = client.select_folder(folder)
    return int(result[b"UIDVALIDITY"])

# Simplified Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Docker/Podman deployment with Caddy auto-TLS, simplify config to convention-based passwords, and support dual auth modes (Bearer default, GitHub OAuth optional).

**Architecture:** Docker Compose with two services (mailbridge + caddy). Config split into `.env` (secrets/server params) and `accounts.yaml` (account structure, no passwords). `server.py` refactored to defer `mcp` instantiation to `create_app()`, enabling conditional auth mode selection and clean test imports.

**Tech Stack:** Python 3.13, FastMCP, Docker/Podman, Caddy 2, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-03-24-simplified-deployment-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `mailbridge_mcp/config.py` | Modify | Add convention-based password resolution, make `password` optional |
| `mailbridge_mcp/server.py` | Modify | Move auth + mcp instantiation into `create_app()`, dual auth mode |
| `tests/test_config.py` | Modify | Add tests for convention-based password resolution |
| `tests/test_contracts.py` | Modify | Update import to use public `register_tools()` |
| `.github/workflows/ci.yml` | Modify | Switch to `AUTH_MODE=bearer` + `MCP_API_KEY=test` |
| `.env.example` | Modify | Add `AUTH_MODE`, `DOMAIN`, restructure |
| `config/accounts.yaml.example` | Modify | Remove password fields |
| `Dockerfile` | Create | Multi-stage Python 3.13-slim build |
| `docker-compose.yml` | Create | mailbridge + caddy services |
| `Caddyfile` | Create | Auto-TLS reverse proxy |
| `.dockerignore` | Create | Exclude .venv, .git, tests from build context |
| `README.md` | Modify | Docker + bare-metal quick starts, compatible clients |

---

### Task 1: Convention-Based Password Resolution in `config.py`

**Files:**
- Modify: `mailbridge_mcp/config.py:13-26` (ImapConfig, SmtpConfig password fields)
- Modify: `mailbridge_mcp/config.py:83-89` (load_accounts function)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for convention-based password resolution**

Add to `tests/test_config.py`:

```python
def test_load_accounts_convention_passwords(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Passwords resolve from {ACCOUNT_ID}_IMAP_PASSWORD env vars when omitted from YAML."""
    data = {
        "accounts": [
            {
                "id": "personal",
                "label": "Personal",
                "imap": {
                    "host": "imap.test.com",
                    "port": 993,
                    "tls": True,
                    "username": "user@test.com",
                },
                "smtp": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "starttls": True,
                    "username": "user@test.com",
                },
                "default_from": "Test <user@test.com>",
            }
        ]
    }
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.dump(data))
    monkeypatch.setenv("PERSONAL_IMAP_PASSWORD", "imap-secret")
    monkeypatch.setenv("PERSONAL_SMTP_PASSWORD", "smtp-secret")
    accounts = load_accounts(p)
    assert accounts[0].imap.password == "imap-secret"
    assert accounts[0].smtp.password == "smtp-secret"


def test_load_accounts_convention_password_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Clear error when password is omitted from YAML and env var is not set."""
    data = {
        "accounts": [
            {
                "id": "work",
                "label": "Work",
                "imap": {
                    "host": "imap.test.com",
                    "port": 993,
                    "tls": True,
                    "username": "user@test.com",
                },
                "smtp": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "starttls": True,
                    "username": "user@test.com",
                },
                "default_from": "Test <user@test.com>",
            }
        ]
    }
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(ValueError, match="WORK_IMAP_PASSWORD"):
        load_accounts(p)


def test_load_accounts_yaml_password_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Backward compat: passwords in YAML (via ${VAR} or direct) still work."""
    data = {
        "accounts": [
            {
                "id": "legacy",
                "label": "Legacy",
                "imap": {
                    "host": "imap.test.com",
                    "port": 993,
                    "tls": True,
                    "username": "user@test.com",
                    "password": "${LEGACY_IMAP_PASSWORD}",
                },
                "smtp": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "starttls": True,
                    "username": "user@test.com",
                    "password": "${LEGACY_SMTP_PASSWORD}",
                },
                "default_from": "Test <user@test.com>",
            }
        ]
    }
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.dump(data))
    monkeypatch.setenv("LEGACY_IMAP_PASSWORD", "legacy-imap")
    monkeypatch.setenv("LEGACY_SMTP_PASSWORD", "legacy-smtp")
    accounts = load_accounts(p)
    assert accounts[0].imap.password == "legacy-imap"
    assert accounts[0].smtp.password == "legacy-smtp"


def test_yaml_password_takes_precedence_over_convention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """YAML password (via ${VAR}) wins over convention env var if both exist."""
    data = {
        "accounts": [
            {
                "id": "test",
                "label": "Test",
                "imap": {
                    "host": "imap.test.com",
                    "port": 993,
                    "tls": True,
                    "username": "user@test.com",
                    "password": "${TEST_IMAP_PASSWORD}",
                },
                "smtp": {
                    "host": "smtp.test.com",
                    "port": 587,
                    "starttls": True,
                    "username": "user@test.com",
                    "password": "${TEST_SMTP_PASSWORD}",
                },
                "default_from": "Test <user@test.com>",
            }
        ]
    }
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.dump(data))
    monkeypatch.setenv("TEST_IMAP_PASSWORD", "from-yaml-ref")
    monkeypatch.setenv("TEST_SMTP_PASSWORD", "from-yaml-ref")
    # Convention env vars with DIFFERENT values
    monkeypatch.setenv("TEST_IMAP_PASSWORD", "from-yaml-ref")
    monkeypatch.setenv("TEST_SMTP_PASSWORD", "from-yaml-ref")
    accounts = load_accounts(p)
    # ${VAR} resolves first, then convention skips (password is not None)
    assert accounts[0].imap.password == "from-yaml-ref"
    assert accounts[0].smtp.password == "from-yaml-ref"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AUTH_MODE=bearer MCP_API_KEY=test pytest tests/test_config.py -v -k "convention or yaml_password_still"`
Expected: FAIL — `password` is currently required in ImapConfig/SmtpConfig

- [ ] **Step 3: Make password optional in Pydantic models**

In `mailbridge_mcp/config.py`, change lines 18 and 26:

```python
class ImapConfig(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    tls: bool = True
    username: str
    password: str | None = None  # resolved from env if None


class SmtpConfig(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    starttls: bool = True
    username: str
    password: str | None = None  # resolved from env if None
```

- [ ] **Step 4: Add convention-based resolution in `load_accounts()`**

Replace `load_accounts()` in `mailbridge_mcp/config.py`:

```python
def _resolve_convention_passwords(
    accounts: list[AccountConfig],
) -> list[AccountConfig]:
    """Fill in empty passwords from env vars using {ACCOUNT_ID}_IMAP_PASSWORD convention."""
    for account in accounts:
        if account.imap.password is None:
            env_var = f"{account.id.upper()}_IMAP_PASSWORD"
            val = os.environ.get(env_var)
            if val is None:
                raise ValueError(
                    f"No password for account '{account.id}' IMAP: "
                    f"set {env_var} in environment or password in accounts.yaml"
                )
            account.imap.password = val
        if account.smtp.password is None:
            env_var = f"{account.id.upper()}_SMTP_PASSWORD"
            val = os.environ.get(env_var)
            if val is None:
                raise ValueError(
                    f"No password for account '{account.id}' SMTP: "
                    f"set {env_var} in environment or password in accounts.yaml"
                )
            account.smtp.password = val
    return accounts


def load_accounts(config_path: Path) -> list[AccountConfig]:
    """Load and validate account configs from YAML, resolving env var placeholders."""
    if not config_path.exists():
        raise FileNotFoundError(f"Accounts config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    resolved = _resolve_dict(raw)
    accounts = [AccountConfig(**acct) for acct in resolved["accounts"]]
    return _resolve_convention_passwords(accounts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `AUTH_MODE=bearer MCP_API_KEY=test pytest tests/test_config.py -v`
Expected: ALL PASS (new tests + existing tests)

- [ ] **Step 6: Run full test suite for regressions**

Run: `AUTH_MODE=bearer MCP_API_KEY=test GITHUB_OAUTH_CLIENT_ID=test GITHUB_OAUTH_CLIENT_SECRET=test pytest -v`
Expected: ALL PASS

- [ ] **Step 7: Lint and type check**

Run: `ruff check mailbridge_mcp/config.py && GITHUB_OAUTH_CLIENT_ID=test GITHUB_OAUTH_CLIENT_SECRET=test mypy mailbridge_mcp/config.py`
Expected: No errors

- [ ] **Step 8: Commit**

```bash
git add mailbridge_mcp/config.py tests/test_config.py
git commit -m "feat: convention-based password resolution from env vars

Passwords can now be omitted from accounts.yaml and resolved
automatically from {ACCOUNT_ID}_IMAP_PASSWORD env vars. Backward
compatible with existing \${VAR} placeholder syntax."
```

---

### Task 2: Refactor `server.py` — Dual Auth Mode

**Files:**
- Modify: `mailbridge_mcp/server.py` (full refactor: move mcp + auth into `create_app()`)
- Modify: `tests/test_contracts.py:9` (update import)
- Test: `tests/test_config.py` (existing tests verify Settings)

This is the most significant change. The current `server.py` creates `mcp` at module level (line 107) and crashes at import without OAuth vars. We move everything into `create_app()`.

- [ ] **Step 1: Write test for bearer auth mode startup**

Add these tests to `tests/test_auth.py` (which already exists with 8 `BearerAuthMiddleware` tests — append at the end):

```python
# --- Auth mode factory tests ---


def test_create_app_bearer_mode(monkeypatch: pytest.MonkeyPatch):
    """create_app() should succeed with AUTH_MODE=bearer and MCP_API_KEY set."""
    monkeypatch.setenv("AUTH_MODE", "bearer")
    monkeypatch.setenv("MCP_API_KEY", "test-key-12345")
    from mailbridge_mcp.server import create_app
    app = create_app()
    assert app is not None


def test_create_app_bearer_mode_missing_key_raises(monkeypatch: pytest.MonkeyPatch):
    """create_app() should fail if AUTH_MODE=bearer but MCP_API_KEY is empty."""
    monkeypatch.setenv("AUTH_MODE", "bearer")
    monkeypatch.setenv("MCP_API_KEY", "")
    from mailbridge_mcp.server import create_app
    with pytest.raises(RuntimeError, match="MCP_API_KEY"):
        create_app()


def test_create_app_invalid_auth_mode_raises(monkeypatch: pytest.MonkeyPatch):
    """create_app() should fail with an unknown AUTH_MODE."""
    monkeypatch.setenv("AUTH_MODE", "invalid")
    from mailbridge_mcp.server import create_app
    with pytest.raises(RuntimeError, match="Unknown AUTH_MODE"):
        create_app()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AUTH_MODE=bearer MCP_API_KEY=test pytest tests/test_auth.py -v -k "create_app"`
Expected: FAIL — `create_app()` doesn't have auth mode logic yet

- [ ] **Step 3: Refactor `server.py`**

Rewrite `mailbridge_mcp/server.py`. The key changes:

1. Remove module-level OAuth check (lines 90-111) and module-level `mcp` (line 107)
2. Remove module-level `mcp.add_middleware` (line 143) and all `@mcp.tool()` decorators (lines 165-345)
3. Remove module-level `app = create_app()` (line 360)
4. Move everything into `create_app()` which creates `mcp`, registers tools, configures auth, and returns the ASGI app

The refactored structure:

```python
from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from pathlib import Path
from typing import Any

import imapclient
import structlog
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from mailbridge_mcp.auth import BearerAuthMiddleware
from mailbridge_mcp.config import AccountConfig, Settings, load_accounts
from mailbridge_mcp.tools_read import (
    get_message, get_thread, list_accounts, list_folders,
    list_messages, search_messages,
)
from mailbridge_mcp.tools_write import (
    delete_message, move_message, reply_tool, send_email_tool, set_flags,
)

log = structlog.get_logger()

# _configure_logging, _verify_account, app_lifespan — unchanged
# health_check — unchanged
# _log_tool_calls — unchanged


def register_tools(mcp: FastMCP) -> None:
    """Register all 11 MCP tools on the given FastMCP instance."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def imap_list_accounts(ctx: Context) -> str:
        """List all configured email accounts with their IDs and labels."""
        return await list_accounts(ctx.lifespan_context["accounts"])

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def imap_list_folders(account_id: str, ctx: Context) -> str:
        """List all IMAP folders/mailboxes for an account."""
        return await list_folders(ctx.lifespan_context["accounts"], account_id)

    # ... all 11 tools, same signatures and bodies as current code ...
    # (exact same decorator + function pairs, just inside this function)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def imap_list_messages(
        account_id: str, ctx: Context, folder: str = "INBOX",
        limit: int = 20, offset: int = 0, unread_only: bool = False,
        sort_by: str = "date_desc", response_format: str = "markdown",
    ) -> str:
        """List messages in a folder with summary metadata. Supports pagination."""
        return await list_messages(
            ctx.lifespan_context["accounts"],
            account_id, folder, limit, offset, unread_only, sort_by, response_format,
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def imap_get_message(
        account_id: str, folder: str, uid: int, ctx: Context,
        prefer_plain: bool = True, include_headers: bool = False,
        response_format: str = "json",
    ) -> str:
        """Fetch the full content of a single message by UID."""
        return await get_message(
            ctx.lifespan_context["accounts"],
            account_id, folder, uid, prefer_plain, include_headers, response_format,
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def imap_search_messages(
        account_id: str, ctx: Context, folder: str = "INBOX",
        query: str = "", from_address: str | None = None,
        to_address: str | None = None, subject: str | None = None,
        since_date: str | None = None, before_date: str | None = None,
        is_unread: bool | None = None, is_flagged: bool | None = None,
        limit: int = 20, offset: int = 0, response_format: str = "markdown",
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
        account_id: str, folder: str, uid: int, ctx: Context,
        limit: int = 20, offset: int = 0, response_format: str = "markdown",
    ) -> str:
        """Fetch all messages in a thread via Message-ID / References headers."""
        return await get_thread(
            ctx.lifespan_context["accounts"],
            account_id, folder, uid, limit, offset, response_format,
        )

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    ))
    async def imap_send_email(
        account_id: str, to: list[str], subject: str, body: str, ctx: Context,
        cc: list[str] | None = None, bcc: list[str] | None = None,
        reply_to: str | None = None,
    ) -> str:
        """Compose and send a new email via SMTP."""
        return await send_email_tool(
            ctx.lifespan_context["accounts"],
            account_id, to, subject, body, cc, bcc, reply_to,
        )

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    ))
    async def imap_reply(
        account_id: str, folder: str, uid: int, body: str, ctx: Context,
        reply_all: bool = False, include_original: bool = True,
    ) -> str:
        """Reply to an existing message, preserving threading headers."""
        return await reply_tool(
            ctx.lifespan_context["accounts"],
            account_id, folder, uid, body, reply_all, include_original,
        )

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    ))
    async def imap_move_message(
        account_id: str, folder: str, uid: int,
        destination_folder: str, ctx: Context,
    ) -> str:
        """Move a message to a different folder."""
        return await move_message(
            ctx.lifespan_context["accounts"],
            account_id, folder, uid, destination_folder,
        )

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True,
    ))
    async def imap_delete_message(
        account_id: str, folder: str, uid: int, ctx: Context,
    ) -> str:
        """Move a message to Trash (does NOT permanently expunge)."""
        return await delete_message(
            ctx.lifespan_context["accounts"],
            account_id, folder, uid,
        )

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    ))
    async def imap_set_flags(
        account_id: str, folder: str, uids: list[int], ctx: Context,
        mark_read: bool | None = None, mark_flagged: bool | None = None,
    ) -> str:
        """Mark messages as read, unread, flagged, or unflagged."""
        return await set_flags(
            ctx.lifespan_context["accounts"],
            account_id, folder, uids, mark_read, mark_flagged,
        )


def create_app() -> Any:
    """Build the ASGI app with auth mode from AUTH_MODE env var."""
    settings = Settings()
    auth_mode = os.getenv("AUTH_MODE", "bearer").lower()

    if auth_mode == "github_oauth":
        from fastmcp.server.auth.providers.github import GitHubProvider

        client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
        client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
        if not (client_id and client_secret):
            raise RuntimeError(
                "AUTH_MODE=github_oauth requires GITHUB_OAUTH_CLIENT_ID "
                "and GITHUB_OAUTH_CLIENT_SECRET to be set."
            )
        auth_provider = GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=f"https://{os.getenv('MCP_PUBLIC_HOST', 'localhost')}",
        )
        mcp = FastMCP("mailbridge-mcp", auth=auth_provider, lifespan=app_lifespan)
    elif auth_mode == "bearer":
        if not settings.mcp_api_key:
            raise RuntimeError(
                "AUTH_MODE=bearer requires MCP_API_KEY to be set."
            )
        mcp = FastMCP("mailbridge-mcp", lifespan=app_lifespan)
    else:
        raise RuntimeError(
            f"Unknown AUTH_MODE: {auth_mode}. Use 'bearer' or 'github_oauth'."
        )

    register_tools(mcp)
    mcp.add_middleware(_log_tool_calls)  # type: ignore[arg-type]

    http_app = mcp.http_app(transport="streamable-http", path="/")

    if auth_mode == "bearer":
        http_app.add_middleware(BearerAuthMiddleware, api_key=settings.mcp_api_key)

    http_app.add_route("/health", health_check, methods=["GET"])
    return http_app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "mailbridge_mcp.server:create_app",
        host=Settings().mcp_host,
        port=Settings().mcp_port,
        log_level="info",
        factory=True,
    )
```

**CRITICAL: No module-level `app = create_app()`.** Uvicorn uses `factory=True` to call `create_app()` at startup rather than expecting a pre-built `app` object. This means importing `server.py` does NOT execute any auth validation — it only defines functions. Tests can safely `from mailbridge_mcp.server import create_app` without env vars being set.

For the deploy workflow and any production setup using uvicorn directly, the command becomes:
`uvicorn mailbridge_mcp.server:create_app --factory --host 0.0.0.0 --port 8765`

Key points:
- `register_tools(mcp)` (public) puts all `@mcp.tool()` registrations inside a function that receives the `mcp` instance. Tests can call this directly.
- `create_app()` handles auth mode, creates `mcp`, registers tools, builds ASGI app
- No module-level `app` — importing `server.py` is side-effect-free
- `GitHubProvider` import is deferred to inside the `if` branch so it's not needed at all in bearer mode
- `settings` is created inside `create_app()` (remove the module-level `settings = Settings()` on line 67)
- `app_lifespan` must also create its own `Settings()` instance since the module-level one is removed. Change line 72 from `_configure_logging(settings.log_level)` to `_configure_logging(Settings().log_level)`, or accept the `settings` created in `create_app()` through the lifespan context. The simplest approach: `app_lifespan` creates `Settings()` internally since it only reads `log_level` and `accounts_config_path`.

- [ ] **Step 4: Update `tests/test_contracts.py` import**

Replace `from mailbridge_mcp.server import mcp` on line 9. Since `mcp` is no longer module-level, use the public `register_tools()` function:

```python
"""Contract tests — verify MCP tool schemas, annotations, and error response shapes."""

from __future__ import annotations

import json

import pytest
from fastmcp import FastMCP

from mailbridge_mcp.server import register_tools


@pytest.fixture
async def tools():
    """Get all registered MCP tools via the public register_tools function.

    No auth env vars needed — register_tools() only registers tool
    definitions on a FastMCP instance without triggering auth or
    account verification.
    """
    mcp = FastMCP("mailbridge-mcp-test")
    register_tools(mcp)
    return await mcp.list_tools()

# ... rest of test file unchanged (EXPECTED_ANNOTATIONS dict and all test functions) ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `AUTH_MODE=bearer MCP_API_KEY=test pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Run lint and type check**

Run: `ruff check mailbridge_mcp/server.py && AUTH_MODE=bearer MCP_API_KEY=test mypy mailbridge_mcp/server.py`
Expected: No errors. If mypy complains about `Any` return type of `create_app`, add `-> Starlette` import.

- [ ] **Step 7: Commit**

```bash
git add mailbridge_mcp/server.py tests/test_contracts.py tests/test_auth.py
git commit -m "refactor: move auth + mcp setup into create_app(), dual auth mode

AUTH_MODE env var controls auth: 'bearer' (default) uses
BearerAuthMiddleware, 'github_oauth' uses GitHubProvider.
No more import-time crash without OAuth vars."
```

---

### Task 3: Update CI and Config Examples

**Files:**
- Modify: `.github/workflows/ci.yml:24-32`
- Modify: `.env.example`
- Modify: `config/accounts.yaml.example`

- [ ] **Step 1: Update CI workflow env vars**

In `.github/workflows/ci.yml`, replace the `env:` blocks (lines 24-26 and 29-31):

```yaml
      - name: Type check
        env:
          AUTH_MODE: bearer
          MCP_API_KEY: test-ci-key
        run: mypy mailbridge_mcp/
      - name: Test
        env:
          AUTH_MODE: bearer
          MCP_API_KEY: test-ci-key
        run: pytest --cov=mailbridge_mcp --cov-fail-under=35
```

- [ ] **Step 2: Update `.env.example`**

Replace the entire contents of `.env.example`:

```env
# ── Auth (pick one mode) ─────────────────────────────────────────────────
AUTH_MODE=bearer                          # "bearer" or "github_oauth"
MCP_API_KEY=                              # required if AUTH_MODE=bearer

# Uncomment for Claude.ai web access (requires GitHub OAuth App):
# AUTH_MODE=github_oauth
# GITHUB_OAUTH_CLIENT_ID=
# GITHUB_OAUTH_CLIENT_SECRET=
# MCP_PUBLIC_HOST=mcp.example.com

# ── Server ────────────────────────────────────────────────────────────────
DOMAIN=mcp.example.com                   # used by Caddy for auto-TLS
MCP_HOST=0.0.0.0                         # listen address
MCP_PORT=8765                            # listen port

# ── Timeouts ─────────────────────────────────────────────────────────────
IMAP_TIMEOUT=30                          # seconds per IMAP operation
SMTP_TIMEOUT=30                          # seconds per SMTP send

# ── Rate limits ──────────────────────────────────────────────────────────
SMTP_RATE_LIMIT=10                       # max sends per minute (0 = unlimited)
IMAP_RATE_LIMIT=60                       # max IMAP ops per minute per account (0 = unlimited)

# ── Application ──────────────────────────────────────────────────────────
LOG_LEVEL=INFO                           # DEBUG | INFO | WARNING | ERROR
# ACCOUNTS_CONFIG_PATH=/etc/mailbridge-mcp/accounts.yaml  # default path

# ── Account passwords ───────────────────────────────────────────────────
# Convention: {ACCOUNT_ID}_IMAP_PASSWORD and {ACCOUNT_ID}_SMTP_PASSWORD
# The account ID from accounts.yaml becomes the prefix (uppercased).
PERSONAL_IMAP_PASSWORD=
PERSONAL_SMTP_PASSWORD=
WORK_IMAP_PASSWORD=
WORK_SMTP_PASSWORD=
```

- [ ] **Step 3: Update `config/accounts.yaml.example`**

Remove password fields:

```yaml
accounts:
  - id: personal
    label: "Personal (name@example.com)"
    imap:
      host: mail.example.com
      port: 993
      tls: true
      username: name@example.com
      # password resolved from PERSONAL_IMAP_PASSWORD env var
    smtp:
      host: mail.example.com
      port: 587
      starttls: true
      username: name@example.com
      # password resolved from PERSONAL_SMTP_PASSWORD env var
    default_from: "Your Name <name@example.com>"

  - id: work
    label: "Work (you@company.com)"
    imap:
      host: imap.company.com
      port: 993
      tls: true
      username: you@company.com
    smtp:
      host: smtp.company.com
      port: 587
      starttls: true
      username: you@company.com
    default_from: "Your Name <you@company.com>"
```

- [ ] **Step 4: Run CI checks locally**

Run: `ruff check . && AUTH_MODE=bearer MCP_API_KEY=test mypy mailbridge_mcp/ && AUTH_MODE=bearer MCP_API_KEY=test pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .env.example config/accounts.yaml.example
git commit -m "chore: update CI env vars and config examples for dual auth mode

CI uses AUTH_MODE=bearer instead of dummy OAuth vars.
accounts.yaml.example uses convention-based passwords.
.env.example documents AUTH_MODE and DOMAIN."
```

---

### Task 4: Docker + Caddy Files

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `Caddyfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
.venv
.git
.github
.mypy_cache
.pytest_cache
.ruff_cache
__pycache__
tests
docs
*.md
.env
.env.*
.claude
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY mailbridge_mcp/ mailbridge_mcp/
RUN pip install --no-cache-dir .

FROM python:3.13-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY mailbridge_mcp/ mailbridge_mcp/
COPY config/accounts.yaml.example config/accounts.yaml.example
EXPOSE 8765
USER nobody
CMD ["uvicorn", "mailbridge_mcp.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765"]
```

- [ ] **Step 3: Create `Caddyfile`**

```
{$DOMAIN} {
    reverse_proxy mailbridge:8765
}
```

- [ ] **Step 4: Create `docker-compose.yml`**

```yaml
services:
  mailbridge:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./accounts.yaml:/etc/mailbridge-mcp/accounts.yaml:ro
    expose:
      - "8765"

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    env_file: .env
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 5: Test Docker build**

Run: `docker build -t mailbridge-mcp:test .`
Expected: Build succeeds. If on a system without Docker, skip — CI will validate.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml Caddyfile .dockerignore
git commit -m "feat: add Docker/Podman deployment with Caddy auto-TLS

Multi-stage Dockerfile (Python 3.13-slim), docker-compose with
Caddy sidecar for automatic Let's Encrypt certificates.
Compatible with both Docker and Podman (podman compose)."
```

---

### Task 5: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README with dual quick start paths**

Update `README.md` with:

1. Docker quick start (primary path): clone, configure `.env` + `accounts.yaml`, `docker compose up`
2. Bare-metal quick start (alternative): clone, configure, venv, systemd — with `apt` and `dnf` commands
3. Authentication section covering both Bearer and GitHub OAuth modes
4. Compatible MCP Clients section
5. Keep existing Tools table, Development section, Security section

The Docker quick start should be ~10 lines. The bare-metal section should be ~20 lines with both `apt` and `dnf` package install commands.

Key content for the Compatible Clients section:

```markdown
## Compatible Clients

Any MCP-compatible client can connect. MCP is a JSON-RPC protocol (not REST/OpenAPI); clients must speak the MCP wire format.

| Client | Auth Mode | Notes |
|--------|-----------|-------|
| Claude Code (CLI) | Bearer or OAuth | `claude mcp add` with `--header` for Bearer |
| Claude.ai (web) | GitHub OAuth only | Requires `AUTH_MODE=github_oauth` |
| Cursor | Bearer | MCP client support built in |
| Windsurf | Bearer | MCP client support built in |
| VS Code | Bearer | Via MCP extensions |
| Custom clients | Bearer | Use the `mcp` Python/TypeScript SDK |
```

- [ ] **Step 2: Verify README renders correctly**

Read through the updated README for formatting issues, broken links, and accuracy.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Docker + bare-metal quick starts, compatible clients

README now leads with Docker deployment (2-step setup) with
bare-metal as an alternative. Documents both auth modes and
lists compatible MCP clients."
```

---

### Task 6: Update CLAUDE.md and Design Doc

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/mailbridge-mcp-design.md`

- [ ] **Step 1: Update CLAUDE.md**

Update the Architecture section to reflect both Docker and bare-metal paths. Update the Commands section to use `AUTH_MODE=bearer MCP_API_KEY=test` instead of `GITHUB_OAUTH_CLIENT_ID=xxx`. Update the Config layering description.

Key changes:
- Architecture diagram: add Docker/Caddy path alongside Nginx
- Commands: `AUTH_MODE=bearer MCP_API_KEY=test pytest` replaces `GITHUB_OAUTH_CLIENT_ID=test ...`
- Config layering: document convention-based passwords
- Testing: update env var instructions

- [ ] **Step 2: Update deploy workflow systemd command**

The deploy workflow (`deploy.yml` line 29) currently restarts `mailbridge-mcp` systemd service which uses `python -m mailbridge_mcp.server`. The systemd `ExecStart` on the server needs updating to:
`ExecStart=/opt/mailbridge-mcp/.venv/bin/uvicorn mailbridge_mcp.server:create_app --factory --host 0.0.0.0 --port 8765`

This is a manual change on the deployed server's systemd unit. Note it in the deploy workflow or CLAUDE.md.

- [ ] **Step 3: Add Docker deployment section to design doc**

Add a new section 17.x to `docs/mailbridge-mcp-design.md` documenting the Docker/Podman deployment option and dual auth mode as post-implementation changes.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/mailbridge-mcp-design.md
git commit -m "docs: update CLAUDE.md and design doc for Docker + dual auth

Reflect convention-based passwords, AUTH_MODE switch, and
Docker deployment option in project documentation."
```

---

### Task 7: Full Verification

- [ ] **Step 1: Run full test suite**

Run: `AUTH_MODE=bearer MCP_API_KEY=test pytest -v --cov=mailbridge_mcp`
Expected: ALL PASS, coverage >= 35%

- [ ] **Step 2: Run lint + type check**

Run: `ruff check . && ruff format --check . && AUTH_MODE=bearer MCP_API_KEY=test mypy mailbridge_mcp/`
Expected: No errors

- [ ] **Step 3: Test Docker build (if Docker available)**

Run: `docker build -t mailbridge-mcp:test . && docker run --rm -e AUTH_MODE=bearer -e MCP_API_KEY=testkey mailbridge-mcp:test python -c "from mailbridge_mcp.server import create_app; print('import OK')"`
Expected: "import OK" printed, exit 0. Note: the import alone should NOT trigger auth validation (no module-level side effects).

- [ ] **Step 4: Verify GitHub OAuth mode still works**

Run: `python -c "import os; os.environ['AUTH_MODE']='github_oauth'; os.environ['GITHUB_OAUTH_CLIENT_ID']='test'; os.environ['GITHUB_OAUTH_CLIENT_SECRET']='test'; from mailbridge_mcp.server import create_app; app = create_app(); print('OAuth mode OK')"`
Expected: "OAuth mode OK"

- [ ] **Step 5: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: address verification issues from full test run"
```

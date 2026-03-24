# mailbridge-mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a remote MCP server that exposes 11 IMAP/SMTP tools to Claude.ai over streamable HTTP, deployed in a Proxmox LXC container.

**Architecture:** FastMCP ASGI app with Bearer auth middleware, sync imapclient wrapped in asyncio executors, aiosmtplib for sends, structlog JSON logging. Vertical slice approach: working server with one tool first, then layer on tools incrementally.

**Tech Stack:** Python 3.13, FastMCP (mcp[cli]>=1.9), imapclient, aiosmtplib, Pydantic, structlog, nh3, uvicorn

**Spec:** `docs/superpowers/specs/2026-03-24-mailbridge-implementation-design.md`
**Design:** `docs/mailbridge-mcp-design.md`

---

## File Map

| File | Responsibility | Task |
|------|---------------|------|
| `pyproject.toml` | Dependencies, build config | 1 |
| `.env.example` | All env var declarations | 1 |
| `config/accounts.yaml.example` | Example account config | 1 |
| `mailbridge_mcp/__init__.py` | Package marker with version | 1 |
| `mailbridge_mcp/config.py` | Pydantic settings + YAML loader + env interpolation | 2 |
| `tests/conftest.py` | Shared fixtures (accounts, env vars) | 2 |
| `tests/test_config.py` | Config loading, validation, env interpolation | 2 |
| `mailbridge_mcp/auth.py` | BearerAuthMiddleware (Starlette) | 3 |
| `mailbridge_mcp/server.py` | FastMCP app, lifespan, /health, tool registration | 4 |
| `mailbridge_mcp/imap_client.py` | Connection manager, run_imap, UIDVALIDITY | 5 |
| `tests/test_imap_client.py` | IMAP client unit tests | 5 |
| `mailbridge_mcp/formatters.py` | Markdown/JSON formatters, body truncation, HTML strip | 6 |
| `mailbridge_mcp/models.py` | Pydantic input models for all tools | 7 |
| `mailbridge_mcp/tools_read.py` | 6 read tool implementations | 8 |
| `mailbridge_mcp/smtp_client.py` | aiosmtplib helpers, rate limiter | 9 |
| `tests/test_smtp_client.py` | SMTP client + rate limiter tests | 9 |
| `mailbridge_mcp/tools_write.py` | 5 write tool implementations | 10 |
| `tests/test_tools.py` | Tool-level integration tests | 11 |
| `.github/workflows/ci.yml` | Lint + type check + test | 12 |
| `.github/workflows/deploy.yml` | Deploy on merge to main | 12 |
| `README.md` | Project docs | 12 |

**Design deviation note:** The design spec puts all tools in `server.py`. This plan splits tools into `tools_read.py` and `tools_write.py` for focused files (~150 lines each) rather than one 500+ line `server.py`. The server module imports and registers them. All other contracts (signatures, annotations, error codes) match the design spec exactly.

---

## SLICE 1: Skeleton + First Tool

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `mailbridge_mcp/__init__.py`
- Create: `.env.example`
- Create: `config/accounts.yaml.example`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "mailbridge-mcp"
version = "0.1.0"
requires-python = ">=3.13"
description = "MCP server bridging Claude.ai to IMAP/SMTP email accounts"

dependencies = [
    "mcp[cli]>=1.9",
    "imapclient>=3.0",
    "aiosmtplib>=3.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
    "email-validator>=2.1",
    "nh3>=0.2",
    "uvicorn[standard]>=0.29",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.14",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py313"
line-length = 99

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version = "3.13"
strict = true
warn_return_any = true
```

- [ ] **Step 2: Create package init**

```python
# mailbridge_mcp/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 3: Create .env.example**

Copy the full env var block from design spec section 5.1 (lines 130-152 of `docs/mailbridge-mcp-design.md`).

- [ ] **Step 4: Create config/accounts.yaml.example**

Copy the full account config from design spec section 5.2 (lines 157-189 of `docs/mailbridge-mcp-design.md`).

- [ ] **Step 5: Install and verify**

Run: `pip install -e ".[dev]"`
Expected: installs without errors, all dependencies resolve

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml mailbridge_mcp/__init__.py .env.example config/
git commit -m "feat: project scaffolding with dependencies"
```

---

### Task 2: Configuration module

**Files:**
- Create: `mailbridge_mcp/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write config tests**

```python
# tests/test_config.py
import os
from pathlib import Path

import pytest
import yaml

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig, Settings, load_accounts


@pytest.fixture
def accounts_yaml(tmp_path: Path) -> Path:
    data = {
        "accounts": [
            {
                "id": "test",
                "label": "Test Account",
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
                "default_from": "Test User <user@test.com>",
            }
        ]
    }
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_load_accounts_resolves_env_vars(accounts_yaml: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEST_IMAP_PASSWORD", "imap-secret")
    monkeypatch.setenv("TEST_SMTP_PASSWORD", "smtp-secret")
    accounts = load_accounts(accounts_yaml)
    assert len(accounts) == 1
    assert accounts[0].id == "test"
    assert accounts[0].imap.password == "imap-secret"
    assert accounts[0].smtp.password == "smtp-secret"


def test_load_accounts_missing_env_var_raises(accounts_yaml: Path):
    # No env vars set — ${TEST_IMAP_PASSWORD} unresolved
    with pytest.raises(ValueError, match="TEST_IMAP_PASSWORD"):
        load_accounts(accounts_yaml)


def test_load_accounts_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_accounts(Path("/nonexistent/accounts.yaml"))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEY", "test-key")
    settings = Settings()
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 8765
    assert settings.imap_timeout == 30
    assert settings.smtp_timeout == 30
    assert settings.smtp_rate_limit == 10
    assert settings.log_level == "INFO"


def test_imap_config_validates_port():
    with pytest.raises(Exception):
        ImapConfig(host="x", port=-1, tls=True, username="u", password="p")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `mailbridge_mcp.config` does not exist yet

- [ ] **Step 3: Create shared test fixtures**

```python
# tests/__init__.py
```

```python
# tests/conftest.py
import pytest


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch):
    """Set minimal env vars for testing."""
    monkeypatch.setenv("MCP_API_KEY", "test-api-key-1234567890abcdef")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8765")
```

- [ ] **Step 4: Implement config.py**

```python
# mailbridge_mcp/config.py
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ImapConfig(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    tls: bool = True
    username: str
    password: str


class SmtpConfig(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    starttls: bool = True
    username: str
    password: str


class AccountConfig(BaseModel):
    id: str
    label: str
    imap: ImapConfig
    smtp: SmtpConfig
    default_from: str


class Settings(BaseSettings):
    mcp_api_key: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    imap_timeout: int = 30
    smtp_timeout: int = 30
    log_level: str = "INFO"
    smtp_rate_limit: int = 10
    accounts_config_path: str = "/etc/mailbridge-mcp/accounts.yaml"

    model_config = {"env_prefix": "", "case_sensitive": False}


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(
                f"Environment variable {var_name} referenced in accounts.yaml "
                f"but not set"
            )
        return val
    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve env vars in a dict."""
    resolved = {}
    for k, v in d.items():
        if isinstance(v, str):
            resolved[k] = _resolve_env_vars(v)
        elif isinstance(v, dict):
            resolved[k] = _resolve_dict(v)
        elif isinstance(v, list):
            resolved[k] = [_resolve_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            resolved[k] = v
    return resolved


def load_accounts(config_path: Path) -> list[AccountConfig]:
    """Load and validate account configs from YAML, resolving env var placeholders."""
    if not config_path.exists():
        raise FileNotFoundError(f"Accounts config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    resolved = _resolve_dict(raw)
    return [AccountConfig(**acct) for acct in resolved["accounts"]]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: all 6 tests PASS

- [ ] **Step 6: Lint and type check**

Run: `ruff check mailbridge_mcp/config.py && mypy mailbridge_mcp/config.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add mailbridge_mcp/config.py tests/
git commit -m "feat: config module with YAML loader and env interpolation"
```

---

### Task 3: Auth middleware

**Files:**
- Create: `mailbridge_mcp/auth.py`

- [ ] **Step 1: Implement auth.py**

```python
# mailbridge_mcp/auth.py
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(
            auth[7:], self.api_key
        ):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
```

- [ ] **Step 2: Lint and type check**

Run: `ruff check mailbridge_mcp/auth.py && mypy mailbridge_mcp/auth.py`
Expected: no errors (mypy may need `# type: ignore` on Starlette's untyped dispatch signature)

- [ ] **Step 3: Commit**

```bash
git add mailbridge_mcp/auth.py
git commit -m "feat: bearer auth middleware with timing-safe comparison"
```

---

### Task 4: Server with lifespan, health check, and first tool

**Files:**
- Create: `mailbridge_mcp/server.py`

- [ ] **Step 1: Implement server.py with imap_list_accounts**

```python
# mailbridge_mcp/server.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
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
    """Sync IMAP login check — runs in executor."""
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
    log = structlog.get_logger()
    _configure_logging(settings.log_level)

    config_path = Path(settings.accounts_config_path)
    accounts = load_accounts(config_path)
    accounts_map: dict[str, AccountConfig] = {}

    loop = asyncio.get_running_loop()
    for account in accounts:
        await log.ainfo("Verifying account connectivity", account_id=account.id)
        await loop.run_in_executor(None, _verify_account, account)
        accounts_map[account.id] = account
        await log.ainfo("Account verified", account_id=account.id, status="connected")

    yield {"accounts": accounts_map, "settings": settings}


mcp = FastMCP(
    "mailbridge-mcp",
    lifespan=app_lifespan,
)


# --- Health check (not an MCP tool — raw ASGI route) ---

async def health_check(request: Request) -> JSONResponse:
    # lifespan_context is not directly available here; use app.state
    accounts: dict[str, AccountConfig] = getattr(request.app.state, "_accounts", {})
    account_status = {aid: "connected" for aid in accounts}
    return JSONResponse({"status": "ok", "accounts": account_status})


# --- Tools ---

@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def imap_list_accounts(ctx: Context) -> str:
    """List all configured email accounts with their IDs and labels."""
    accounts: dict[str, AccountConfig] = ctx.lifespan_context["accounts"]
    result = [
        {"id": a.id, "label": a.label, "default_from": a.default_from}
        for a in accounts.values()
    ]
    return json.dumps(result, indent=2)


# --- App factory ---

def create_app():
    middleware = [Middleware(BearerAuthMiddleware, api_key=settings.mcp_api_key)]
    app = mcp.http_app(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        middleware=middleware,
    )
    app.add_route("/health", health_check, methods=["GET"])
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "mailbridge_mcp.server:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
    )
```

- [ ] **Step 2: Verify the import chain works**

Run: `python -c "from mailbridge_mcp.server import mcp; print('OK')"`
Expected: prints `OK` (this validates that all imports resolve)

- [ ] **Step 3: Lint and type check**

Run: `ruff check mailbridge_mcp/server.py && mypy mailbridge_mcp/server.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add mailbridge_mcp/server.py
git commit -m "feat: FastMCP server with lifespan, health check, and imap_list_accounts"
```

---

## SLICE 2: IMAP Read Operations

### Task 5: IMAP client module

**Files:**
- Create: `mailbridge_mcp/imap_client.py`
- Create: `tests/test_imap_client.py`

- [ ] **Step 1: Write IMAP client tests**

```python
# tests/test_imap_client.py
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig
from mailbridge_mcp.imap_client import imap_connection, run_imap


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="test",
        label="Test",
        imap=ImapConfig(
            host="imap.test.com", port=993, tls=True,
            username="user@test.com", password="pass",
        ),
        smtp=SmtpConfig(
            host="smtp.test.com", port=587, starttls=True,
            username="user@test.com", password="pass",
        ),
        default_from="Test <user@test.com>",
    )


def test_imap_connection_logs_in_and_out(account: AccountConfig):
    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        with imap_connection(account) as client:
            assert client is mock_client
            mock_client.login.assert_called_once_with("user@test.com", "pass")

        mock_client.logout.assert_called_once()


def test_imap_connection_logout_on_exception(account: AccountConfig):
    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        with pytest.raises(RuntimeError):
            with imap_connection(account) as client:
                raise RuntimeError("boom")

        mock_client.logout.assert_called_once()


async def test_run_imap_calls_operation(account: AccountConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def fake_op(client, folder):
        return f"listed {folder}"

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        result = await run_imap(account, fake_op, "INBOX")
        assert result == "listed INBOX"


async def test_run_imap_retries_on_connection_error(account: AccountConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")
    call_count = 0

    def flaky_op(client):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("dropped")
        return "success"

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        result = await run_imap(account, flaky_op)
        assert result == "success"
        assert call_count == 2


async def test_run_imap_timeout(account: AccountConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IMAP_TIMEOUT", "1")

    def slow_op(client):
        import time
        time.sleep(5)

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        with pytest.raises(asyncio.TimeoutError):
            await run_imap(account, slow_op)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imap_client.py -v`
Expected: FAIL — `mailbridge_mcp.imap_client` does not exist

- [ ] **Step 3: Implement imap_client.py**

```python
# mailbridge_mcp/imap_client.py
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any, Callable

import imapclient

from mailbridge_mcp.config import AccountConfig


@contextmanager
def imap_connection(account: AccountConfig):
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
    """Run a sync IMAP operation in an executor with timeout and single retry."""
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


def get_uidvalidity(client: imapclient.IMAPClient, folder: str) -> int:
    """SELECT a folder and return its UIDVALIDITY value."""
    result = client.select_folder(folder)
    return int(result[b"UIDVALIDITY"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_imap_client.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mailbridge_mcp/imap_client.py tests/test_imap_client.py
git commit -m "feat: IMAP client with connection manager, executor wrapper, retry"
```

---

### Task 6: Formatters module

**Files:**
- Create: `mailbridge_mcp/formatters.py`

- [ ] **Step 1: Implement formatters.py**

```python
# mailbridge_mcp/formatters.py
from __future__ import annotations

import json
from typing import Any

import nh3

BODY_TRUNCATION_LIMIT = 50_000


def strip_html(html: str) -> str:
    """Strip all HTML tags, returning plain text."""
    return nh3.clean(html, tags=set())


def truncate_body(body: str) -> tuple[str, bool]:
    """Truncate body to BODY_TRUNCATION_LIMIT chars. Returns (body, was_truncated)."""
    if len(body) <= BODY_TRUNCATION_LIMIT:
        return body, False
    return body[:BODY_TRUNCATION_LIMIT], True


def format_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def format_message_summary_markdown(messages: list[dict[str, Any]]) -> str:
    """Format message summaries as a markdown table."""
    if not messages:
        return "*No messages found.*"
    lines = [
        "| UID | From | Subject | Date | Read | Flagged |",
        "|-----|------|---------|------|------|---------|",
    ]
    for m in messages:
        read = "yes" if m.get("is_read") else "no"
        flagged = "yes" if m.get("is_flagged") else "no"
        subj = (m.get("subject") or "(no subject)")[:60]
        lines.append(
            f"| {m['uid']} | {m.get('from', '')} | {subj} | {m.get('date', '')} | {read} | {flagged} |"
        )
    return "\n".join(lines)


def pagination_envelope(
    items: list[Any], total: int, offset: int, limit: int
) -> dict[str, Any]:
    """Wrap a list of items with pagination metadata."""
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "has_more": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }
```

- [ ] **Step 2: Lint**

Run: `ruff check mailbridge_mcp/formatters.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add mailbridge_mcp/formatters.py
git commit -m "feat: formatters for markdown, JSON, body truncation, HTML strip"
```

---

### Task 7: Pydantic input models for read tools

**Files:**
- Create: `mailbridge_mcp/models.py`

- [ ] **Step 1: Implement models.py (read tool models)**

```python
# mailbridge_mcp/models.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AccountIdInput(BaseModel):
    account_id: str = Field(description="Account ID from imap_list_accounts")


class ListFoldersInput(AccountIdInput):
    pass


class ListMessagesInput(AccountIdInput):
    folder: str = Field(default="INBOX", description="IMAP folder name")
    limit: int = Field(default=20, ge=1, le=100, description="Max messages to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    unread_only: bool = Field(default=False, description="Only return unread messages")
    sort_by: Literal["date_desc", "date_asc", "from", "subject"] = Field(
        default="date_desc", description="Sort order"
    )
    response_format: Literal["json", "markdown"] = Field(
        default="markdown", description="Response format"
    )


class GetMessageInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(description="Message UID from imap_list_messages")
    prefer_plain: bool = Field(default=True, description="Strip HTML to plain text via nh3")
    include_headers: bool = Field(default=False, description="Include raw email headers")
    response_format: Literal["json", "markdown"] = Field(default="json")


class SearchMessagesInput(AccountIdInput):
    folder: str = Field(default="INBOX", description="IMAP folder name")
    query: str = Field(
        default="", description="Maps to IMAP TEXT criterion (full message search)"
    )
    from_address: str | None = Field(default=None, description="Filter by sender")
    to_address: str | None = Field(default=None, description="Filter by recipient")
    subject: str | None = Field(default=None, description="Filter by subject")
    since_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    before_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    is_unread: bool | None = Field(default=None, description="Filter unread only")
    is_flagged: bool | None = Field(default=None, description="Filter flagged only")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    response_format: Literal["json", "markdown"] = Field(default="markdown")


class GetThreadInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(description="Any UID in the thread")
    limit: int = Field(default=20, ge=1, le=50, description="Max thread messages")
    offset: int = Field(default=0, ge=0)
    response_format: Literal["json", "markdown"] = Field(default="markdown")
```

- [ ] **Step 2: Lint and type check**

Run: `ruff check mailbridge_mcp/models.py && mypy mailbridge_mcp/models.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add mailbridge_mcp/models.py
git commit -m "feat: Pydantic input models for read tools"
```

---

### Task 8: Read tool implementations

**Files:**
- Create: `mailbridge_mcp/tools_read.py`
- Modify: `mailbridge_mcp/server.py` (import and register)

This is the largest task. Each tool follows the same pattern: validate input, resolve account from lifespan context, call `run_imap`, format response. Implement all 5 read tools (plus update `imap_list_accounts`), then register them.

- [ ] **Step 1: Implement tools_read.py**

Create `mailbridge_mcp/tools_read.py` containing all 6 read tool functions. Each returns `str`.

**Common helper pattern** (define at top of file):

```python
# mailbridge_mcp/tools_read.py
from __future__ import annotations

import json
from typing import Any

import structlog

from mailbridge_mcp.config import AccountConfig
from mailbridge_mcp.formatters import (
    format_json, format_message_summary_markdown, pagination_envelope,
    strip_html, truncate_body,
)
from mailbridge_mcp.imap_client import get_uidvalidity, run_imap

log = structlog.get_logger()


def _error(code: str, message: str, account_id: str = "") -> str:
    return json.dumps({"error": code, "message": message, "account_id": account_id})


def _get_account(accounts: dict[str, AccountConfig], account_id: str) -> AccountConfig | str:
    """Return the account or an error JSON string."""
    if account_id not in accounts:
        return _error("ACCOUNT_NOT_FOUND", f"Unknown account: {account_id}", account_id)
    return accounts[account_id]
```

**list_folders pattern** (UIDVALIDITY + error handling):

```python
async def list_folders(accounts: dict[str, AccountConfig], account_id: str) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:
        def _op(client):
            folders = client.list_folders()
            result = []
            for flags, delimiter, name in folders:
                client.select_folder(name, readonly=True)
                status = client.folder_status(name, ["MESSAGES", "UNSEEN"])
                result.append({
                    "name": name,
                    "flags": [f.decode() if isinstance(f, bytes) else str(f) for f in flags],
                    "delimiter": delimiter,
                    "message_count": status.get(b"MESSAGES", 0),
                    "unread_count": status.get(b"UNSEEN", 0),
                })
            return result
        data = await run_imap(acct, _op)
        return format_json(data)
    except Exception as e:
        return _error("IMAP_CONNECTION_ERROR", str(e), account_id)
```

**get_message pattern** (UIDVALIDITY check + body truncation + HTML strip):

```python
async def get_message(
    accounts: dict[str, AccountConfig], account_id: str, folder: str,
    uid: int, prefer_plain: bool, include_headers: bool,
) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:
        def _op(client):
            uidvalidity = get_uidvalidity(client, folder)
            data = client.fetch([uid], ["ENVELOPE", "BODY[]", "FLAGS", "RFC822.SIZE"])
            if uid not in data:
                return {"error": "IMAP_MESSAGE_NOT_FOUND", "message": f"UID {uid} not found"}
            # Parse ENVELOPE, extract body from BODY[], strip HTML if prefer_plain
            # Truncate body via truncate_body()
            # Return {uid, subject, from, to, cc, bcc, date, body, body_truncated, attachments, uidvalidity}
            ...
        return format_json(await run_imap(acct, _op))
    except Exception as e:
        return _error("IMAP_CONNECTION_ERROR", str(e), account_id)
```

**search_messages** uses IMAP criteria building:

```python
async def search_messages(accounts, account_id, folder, query, from_address,
                          to_address, subject, since_date, before_date,
                          is_unread, is_flagged, limit, offset) -> str:
    # Build IMAP SEARCH criteria list:
    criteria = []
    if query:
        criteria.extend(["TEXT", query])
    if from_address:
        criteria.extend(["FROM", from_address])
    if to_address:
        criteria.extend(["TO", to_address])
    if subject:
        criteria.extend(["SUBJECT", subject])
    if since_date:
        criteria.extend(["SINCE", since_date])  # IMAP date format: "24-Mar-2026"
    if before_date:
        criteria.extend(["BEFORE", before_date])
    if is_unread is True:
        criteria.append("UNSEEN")
    if is_flagged is True:
        criteria.append("FLAGGED")
    if not criteria:
        criteria = ["ALL"]
    # client.search(criteria), then fetch summaries for UIDs[offset:offset+limit]
    ...
```

**get_thread** uses Message-ID/References matching:

```python
async def get_thread(accounts, account_id, folder, uid, limit, offset) -> str:
    # 1. Fetch target message's Message-ID and References headers
    # 2. Collect all message-ids from References + Message-ID
    # 3. Search folder: OR(HEADER Message-ID <id1>) OR(HEADER Message-ID <id2>) ...
    # 4. Also search: HEADER References <target-message-id>
    # 5. Deduplicate, sort by date, apply offset/limit, return with pagination
    ...
```

Implement all functions fully. The patterns above show the structure; fill in the email parsing (use Python `email` stdlib for ENVELOPE/BODY parsing).

- [ ] **Step 2: Register tools in server.py**

Update `server.py` to import from `tools_read.py` and register each function with `@mcp.tool()` with correct annotations from the design spec. Move the existing `imap_list_accounts` implementation into `tools_read.py`.

- [ ] **Step 3: Lint and type check**

Run: `ruff check mailbridge_mcp/tools_read.py mailbridge_mcp/server.py && mypy mailbridge_mcp/tools_read.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add mailbridge_mcp/tools_read.py mailbridge_mcp/server.py
git commit -m "feat: 6 IMAP read tools (list accounts/folders/messages, get message, search, thread)"
```

---

## SLICE 3: Write Operations + SMTP

### Task 9: SMTP client with rate limiter

**Files:**
- Create: `mailbridge_mcp/smtp_client.py`
- Create: `tests/test_smtp_client.py`

- [ ] **Step 1: Write SMTP client tests**

```python
# tests/test_smtp_client.py
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from mailbridge_mcp.smtp_client import RateLimiter, send_email
from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="test", label="Test",
        imap=ImapConfig(host="imap.test.com", port=993, tls=True, username="u", password="p"),
        smtp=SmtpConfig(host="smtp.test.com", port=587, starttls=True, username="u", password="p"),
        default_from="Test <test@test.com>",
    )


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(max_per_minute=3)
        assert rl.check() is True
        assert rl.check() is True
        assert rl.check() is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_per_minute=2)
        assert rl.check() is True
        assert rl.check() is True
        assert rl.check() is False

    def test_unlimited_when_zero(self):
        rl = RateLimiter(max_per_minute=0)
        for _ in range(100):
            assert rl.check() is True

    def test_window_expires(self):
        rl = RateLimiter(max_per_minute=1)
        assert rl.check() is True
        assert rl.check() is False
        # Manually expire the oldest entry
        rl._timestamps[0] = time.monotonic() - 61
        assert rl.check() is True


async def test_send_email_builds_mime(account: AccountConfig):
    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send") as mock_send:
        mock_send.return_value = ({}, "OK")
        result = await send_email(
            account=account,
            to=["recipient@test.com"],
            subject="Test",
            body="Hello",
            timeout=5,
        )
        assert result["status"] == "sent"
        assert "message_id" in result
        mock_send.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_smtp_client.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement smtp_client.py**

```python
# mailbridge_mcp/smtp_client.py
from __future__ import annotations

import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
from email_validator import validate_email, EmailNotValidError

from mailbridge_mcp.config import AccountConfig


class RateLimiter:
    """Sliding window rate limiter. Thread-safe not required (single event loop)."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []

    def check(self) -> bool:
        if self.max_per_minute == 0:
            return True  # unlimited
        now = time.monotonic()
        cutoff = now - 60.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


def validate_addresses(addresses: list[str]) -> None:
    """Validate email addresses. Raises ValueError with details on failure."""
    for addr in addresses:
        try:
            validate_email(addr, check_deliverability=False)
        except EmailNotValidError as e:
            raise ValueError(f"Invalid email address '{addr}': {e}") from e


def _build_mime(
    from_addr: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4()}@mailbridge-mcp>"
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.attach(MIMEText(body, "plain"))
    return msg


async def send_email(
    account: AccountConfig,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Build and send an email via SMTP. Returns {status, message_id}."""
    all_addrs = list(to) + (cc or []) + (bcc or [])
    validate_addresses(all_addrs)

    msg = _build_mime(
        from_addr=account.default_from,
        to=to, subject=subject, body=body,
        cc=cc, bcc=bcc, reply_to=reply_to,
        in_reply_to=in_reply_to, references=references,
    )

    recipients = list(to) + (cc or []) + (bcc or [])
    await aiosmtplib.send(
        msg,
        hostname=account.smtp.host,
        port=account.smtp.port,
        username=account.smtp.username,
        password=account.smtp.password,
        start_tls=account.smtp.starttls,
        timeout=timeout,
        recipients=recipients,
    )
    return {"status": "sent", "message_id": msg["Message-ID"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_smtp_client.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mailbridge_mcp/smtp_client.py tests/test_smtp_client.py
git commit -m "feat: SMTP client with MIME builder, rate limiter, address validation"
```

---

### Task 10: Write tool implementations

**Files:**
- Create: `mailbridge_mcp/tools_write.py`
- Modify: `mailbridge_mcp/models.py` (add write tool input models)
- Modify: `mailbridge_mcp/server.py` (register write tools)

- [ ] **Step 1: Add write tool input models to models.py**

Append to `mailbridge_mcp/models.py`:

```python
# --- Write tool models ---

class SendEmailInput(AccountIdInput):
    to: list[str] = Field(description="Recipient email addresses")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Plain text body")
    cc: list[str] = Field(default_factory=list, description="CC recipients")
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    reply_to: str | None = Field(default=None, description="Reply-To address")


class ReplyInput(AccountIdInput):
    folder: str = Field(description="IMAP folder containing the message")
    uid: int = Field(description="UID of message being replied to")
    body: str = Field(description="Plain text reply body")
    reply_all: bool = Field(default=False, description="Reply to all recipients")
    include_original: bool = Field(default=True, description="Include original message text")


class MoveMessageInput(AccountIdInput):
    folder: str = Field(description="Source folder")
    uid: int = Field(description="Message UID")
    destination_folder: str = Field(description="Destination folder")


class DeleteMessageInput(AccountIdInput):
    folder: str = Field(description="Folder containing the message")
    uid: int = Field(description="Message UID")


class SetFlagsInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uids: list[int] = Field(description="One or more message UIDs")
    mark_read: bool | None = Field(default=None, description="Set read/unread")
    mark_flagged: bool | None = Field(default=None, description="Set flagged/unflagged")
```

- [ ] **Step 2: Implement tools_write.py**

Create `mailbridge_mcp/tools_write.py` with 5 write tool functions. Use the same `_error` and `_get_account` helpers as tools_read (extract to a shared `tools_common.py` or import from tools_read).

**Rate limiter initialization** (module-level singleton):

```python
# mailbridge_mcp/tools_write.py
import os
from mailbridge_mcp.smtp_client import RateLimiter, send_email, validate_addresses

_rate_limiter = RateLimiter(max_per_minute=int(os.getenv("SMTP_RATE_LIMIT", "10")))
```

**send_email_tool** pattern:

```python
async def send_email_tool(accounts, account_id, to, subject, body,
                          cc=None, bcc=None, reply_to=None, timeout=30) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    if not _rate_limiter.check():
        return _error("SMTP_RATE_LIMITED", "Send rate exceeded, retry after cooldown", account_id)
    try:
        validate_addresses(to + (cc or []) + (bcc or []))
    except ValueError as e:
        return _error("INVALID_EMAIL_ADDRESS", str(e), account_id)
    try:
        result = await send_email(acct, to, subject, body, cc, bcc, reply_to, timeout=timeout)
        return format_json(result)
    except Exception as e:
        return _error("SMTP_SEND_FAILED", str(e), account_id)
```

**reply_tool** — fetch original then send with threading headers:

```python
async def reply_tool(accounts, account_id, folder, uid, body,
                     reply_all=False, include_original=True, timeout=30) -> str:
    # 1. Check rate limiter
    # 2. Fetch original via run_imap to get: Message-ID, References, Subject, From, To, Cc, body
    def _fetch_original(client):
        get_uidvalidity(client, folder)
        data = client.fetch([uid], ["ENVELOPE", "BODY[]"])
        if uid not in data:
            return None
        # Parse Message-ID → in_reply_to
        # Parse References → append Message-ID to build new References
        # Parse Subject → prepend "Re:" if not already present
        # If reply_all: collect To + Cc addresses (minus self)
        return {...}
    original = await run_imap(acct, _fetch_original)
    # 3. Build reply body (prepend original if include_original)
    # 4. Call send_email() with in_reply_to and references params
```

**delete_message** — Trash folder auto-detection:

```python
async def delete_message(accounts, account_id, folder, uid) -> str:
    def _op(client):
        get_uidvalidity(client, folder)
        # Find Trash folder: check list_folders() for \Trash flag first,
        # then fall back to common names
        folders = client.list_folders()
        trash = None
        for flags, delim, name in folders:
            if b"\\Trash" in flags:
                trash = name
                break
        if not trash:
            for candidate in ["Trash", "Deleted Items", "INBOX.Trash"]:
                if any(name == candidate for _, _, name in folders):
                    trash = candidate
                    break
        if not trash:
            trash = "Trash"  # last resort
        # COPY to Trash, then add \Deleted flag. NEVER call EXPUNGE.
        client.select_folder(folder)
        client.copy([uid], trash)
        client.add_flags([uid], [imapclient.DELETED])
        return {"status": "trashed", "uid": uid}
    return format_json(await run_imap(acct, _op))
```

**move_message** — COPY + flag delete:

```python
async def move_message(accounts, account_id, folder, uid, destination_folder) -> str:
    def _op(client):
        get_uidvalidity(client, folder)
        client.select_folder(folder)
        client.copy([uid], destination_folder)
        client.add_flags([uid], [imapclient.DELETED])
        # Note: some servers support MOVE command — use copy+delete for portability
        return {"status": "moved", "uid": uid, "destination": destination_folder}
    return format_json(await run_imap(acct, _op))
```

**set_flags**:

```python
async def set_flags(accounts, account_id, folder, uids, mark_read=None, mark_flagged=None) -> str:
    def _op(client):
        client.select_folder(folder)
        flags_set = []
        if mark_read is True:
            client.add_flags(uids, [imapclient.SEEN])
            flags_set.append("\\Seen")
        elif mark_read is False:
            client.remove_flags(uids, [imapclient.SEEN])
        if mark_flagged is True:
            client.add_flags(uids, [imapclient.FLAGGED])
            flags_set.append("\\Flagged")
        elif mark_flagged is False:
            client.remove_flags(uids, [imapclient.FLAGGED])
        return {"status": "updated", "uids": uids, "flags_set": flags_set}
    return format_json(await run_imap(acct, _op))
```

- [ ] **Step 3: Register write tools in server.py**

Import from `tools_write.py` and register with `@mcp.tool()` with annotations matching the design spec:
- `imap_send_email`: `readOnlyHint=False, destructiveHint=False, idempotentHint=False`
- `imap_reply`: same as send
- `imap_move_message`: `readOnlyHint=False, destructiveHint=False, idempotentHint=True`
- `imap_delete_message`: `readOnlyHint=False, destructiveHint=True, idempotentHint=True`
- `imap_set_flags`: `readOnlyHint=False, destructiveHint=False, idempotentHint=True`

- [ ] **Step 4: Lint and type check**

Run: `ruff check mailbridge_mcp/ && mypy mailbridge_mcp/`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add mailbridge_mcp/tools_write.py mailbridge_mcp/models.py mailbridge_mcp/server.py
git commit -m "feat: 5 write tools (send, reply, move, delete, set flags)"
```

---

### Task 11: Tool-level integration tests

**Files:**
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write tool-level tests**

Create `tests/test_tools.py` with tests for all 11 tools. Each test:
- Mocks `imapclient.IMAPClient` and `aiosmtplib.send` at the library boundary
- Verifies the tool returns the expected JSON shape
- Tests at least one error path per tool (e.g., `ACCOUNT_NOT_FOUND`, `IMAP_FOLDER_NOT_FOUND`)
- For send/reply: verifies rate limiter integration
- For delete: verifies Trash detection and no EXPUNGE
- For search: verifies IMAP TEXT criterion mapping

Key test scenarios:
- `test_list_accounts_returns_all_configured`
- `test_list_folders_returns_folder_metadata`
- `test_list_messages_pagination`
- `test_get_message_truncates_body`
- `test_get_message_strips_html`
- `test_search_messages_with_date_filter`
- `test_get_thread_respects_limit`
- `test_send_email_validates_addresses`
- `test_send_email_rate_limited`
- `test_reply_sets_threading_headers`
- `test_move_message_copies_and_deletes`
- `test_delete_message_moves_to_trash`
- `test_set_flags_marks_read`
- `test_unknown_account_returns_error`
- `test_uidvalidity_mismatch_returns_error`

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Check coverage**

Run: `pytest --cov=mailbridge_mcp --cov-report=term-missing`
Expected: 80%+ overall, 90%+ on config/imap_client/smtp_client

- [ ] **Step 4: Commit**

```bash
git add tests/test_tools.py
git commit -m "test: tool-level integration tests for all 11 MCP tools"
```

---

## SLICE 4: Polish + Deploy

### Task 12: CI/CD and README

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`
- Modify: `README.md`

- [ ] **Step 1: Create CI workflow**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, testing]
  pull_request:
    branches: [main]

jobs:
  lint-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy mailbridge_mcp/
      - run: pytest --cov=mailbridge_mcp --cov-fail-under=80
```

- [ ] **Step 2: Create deploy workflow**

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    steps:
      - name: Deploy to LXC
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.LXC_HOST }}
          username: ${{ secrets.LXC_USER }}
          key: ${{ secrets.LXC_SSH_KEY }}
          script: |
            cd /opt/mailbridge-mcp
            git pull
            .venv/bin/pip install -e .
            sudo systemctl restart mailbridge-mcp
            sleep 3
            curl -sf http://localhost:8765/health || exit 1
```

- [ ] **Step 3: Write README.md**

Replace the placeholder README with project overview, local dev quickstart, deployment instructions, and Claude.ai connection steps. Refer to design spec sections 12-13 for deployment details.

- [ ] **Step 4: Final lint and test pass**

Run: `ruff check . && mypy mailbridge_mcp/ && pytest --cov=mailbridge_mcp`
Expected: all clean, all tests pass, coverage >= 80%

- [ ] **Step 5: Commit**

```bash
git add .github/ README.md
git commit -m "feat: CI/CD workflows and README"
```

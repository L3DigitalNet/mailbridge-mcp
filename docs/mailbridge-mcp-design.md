# mailbridge-mcp — Design & Implementation Specification

**Project:** `mailbridge-mcp`
**Runtime:** Python 3.13 · FastMCP · Streamable HTTP transport
**Host:** Hetzner EX130-R · Proxmox 9.1 · Debian 13 Trixie LXC container (bare metal install)
**Purpose:** Remote MCP server that exposes IMAP/SMTP operations to Claude.ai

---

## 1. Goals

- Provide Claude.ai with full read/write access to one or more IMAP email accounts
- Run as a persistent bare-metal service inside a dedicated Proxmox LXC container
- Expose a secure, subdomain-accessible HTTPS endpoint Claude.ai connects to as a remote MCP server
- Support multiple accounts via a single server instance
- Keep secrets out of the codebase (environment-variable-driven config)
- Greenfield build — no forks. Existing projects (`codefuturist/email-mcp`, `non-dirty/imap-mcp`) used as reference only

### Out of Scope (v0.1)

- Attachment download or binary content retrieval (metadata only, per P3)
- Email forwarding (`imap_forward_message`)
- Draft management (save, list, send drafts)
- From-address override on send (uses `default_from` from account config)
- OAuth2 / XOAUTH2 authentication (password auth only)
- IMAP IDLE / push notifications

---

## 2. Architecture Overview

```
Claude.ai (browser / API)
        │
        │  HTTPS  (e.g. mailbridge.l3digital.net)
        ▼
  Nginx reverse proxy  (existing vhost stack, Proxmox host)
        │
        │  HTTP  <lxc-ip>:8765
        ▼
  mailbridge-mcp  FastMCP server  (Proxmox LXC container, bare metal Python)
        │                │
        │ IMAP/TLS       │ SMTP/TLS
        ▼                ▼
  Mail server(s)   Mail server(s)
```

- **Transport:** `streamable_http` — stateless JSON, no session state
- **Auth:** Bearer token in `Authorization` header (API key in `.env`)
- **Deployment:** bare metal Python venv, systemd service, dedicated LXC container
- **Port:** internal `8765`, proxied via Nginx on the existing vhost pattern

---

## 3. Repository Layout

```
mailbridge-mcp/
├── mailbridge_mcp/
│   ├── __init__.py
│   ├── server.py          # FastMCP app entrypoint, lifespan, tool registration
│   ├── config.py          # AccountConfig Pydantic models + YAML loader + env interpolation
│   ├── imap_client.py     # Sync IMAP wrapper (imapclient) + asyncio executor helpers
│   ├── smtp_client.py     # Async SMTP wrapper (aiosmtplib)
│   ├── models.py          # Pydantic input models for every tool
│   ├── formatters.py      # Shared markdown + JSON response formatters
│   └── auth.py            # BearerAuthMiddleware (Starlette)
├── config/
│   └── accounts.yaml.example
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_imap_client.py
│   ├── test_smtp_client.py
│   └── test_tools.py
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── .env.example
├── pyproject.toml
└── README.md
```

---

## 4. Dependencies

```toml
[project]
name = "mailbridge-mcp"
version = "0.1.0"
requires-python = ">=3.13"

dependencies = [
    "mcp[cli]>=1.9",           # FastMCP + streamable HTTP transport
    "imapclient>=3.0",         # High-level IMAP (wraps imaplib)
    "aiosmtplib>=3.0",         # Async SMTP
    "pydantic>=2.7",           # Input/output model validation
    "pydantic-settings>=2.3",  # Settings from environment
    "python-dotenv>=1.0",      # .env file loading
    "pyyaml>=6.0",             # accounts.yaml parsing
    "email-validator>=2.1",    # RFC-compliant address validation
    "nh3>=0.2",                # HTML → plain-text stripping for message bodies
    "uvicorn[standard]>=0.29", # ASGI runner for streamable HTTP
    "structlog>=24.1",         # Structured JSON logging
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.14",
    "ruff>=0.4",
    "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## 5. Configuration

### 5.1 Environment Variables (`.env`)

```dotenv
# ── Auth ──────────────────────────────────────────────────────────────────
MCP_API_KEY=<strong-random-token>        # Bearer token Claude.ai must send

# ── Server ────────────────────────────────────────────────────────────────
MCP_HOST=0.0.0.0
MCP_PORT=8765

# ── Timeouts ─────────────────────────────────────────────────────────────
IMAP_TIMEOUT=30                          # seconds per IMAP operation
SMTP_TIMEOUT=30                          # seconds per SMTP send

# ── Application ──────────────────────────────────────────────────────────
LOG_LEVEL=INFO                           # DEBUG | INFO | WARNING | ERROR
SMTP_RATE_LIMIT=10                       # max sends per minute (0 = unlimited)

# ── Config paths ──────────────────────────────────────────────────────────
ACCOUNTS_CONFIG_PATH=/etc/mailbridge-mcp/accounts.yaml

# ── Per-account credentials (referenced via ${VAR} in accounts.yaml) ──────
PERSONAL_IMAP_PASSWORD=
PERSONAL_SMTP_PASSWORD=
WORK_IMAP_PASSWORD=
WORK_SMTP_PASSWORD=
```

### 5.2 Account Config (`/etc/mailbridge-mcp/accounts.yaml`)

```yaml
accounts:
  - id: personal
    label: "Personal (name@example.com)"
    imap:
      host: mail.example.com
      port: 993
      tls: true
      username: name@example.com
      password: "${PERSONAL_IMAP_PASSWORD}"    # resolved from env at startup
    smtp:
      host: mail.example.com
      port: 587
      starttls: true
      username: name@example.com
      password: "${PERSONAL_SMTP_PASSWORD}"
    default_from: "Your Name <name@example.com>"

  - id: work
    label: "Work (you@company.com)"
    imap:
      host: imap.company.com
      port: 993
      tls: true
      username: you@company.com
      password: "${WORK_IMAP_PASSWORD}"
    smtp:
      host: smtp.company.com
      port: 587
      starttls: true
      username: you@company.com
      password: "${WORK_SMTP_PASSWORD}"
    default_from: "Your Name <you@company.com>"
```

All `${VAR}` references in the YAML are resolved from the process environment at startup. Passwords never appear in code or version control.

---

## 6. Authentication Middleware

Every request must carry a valid Bearer token before any tool is dispatched.

```python
# auth.py
import hmac
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], self.api_key):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
```

Register via FastMCP's ASGI middleware hook in `server.py`.

---

## 7. IMAP Client Design (`imap_client.py`)

`imapclient` is synchronous. All blocking calls must be wrapped in `asyncio.get_running_loop().run_in_executor(None, ...)` to avoid blocking the event loop.

### UIDVALIDITY tracking

IMAP UIDs are stable only while a folder's UIDVALIDITY value is unchanged. On folder SELECT, capture the UIDVALIDITY value and include it in list/search responses (`uidvalidity` field). When a tool receives a UID for a single-message operation (`imap_get_message`, `imap_reply`, `imap_move_message`, `imap_delete_message`), compare the current UIDVALIDITY against the value from the listing. If mismatched, return `IMAP_UIDVALIDITY_CHANGED` instead of operating on the wrong message.

### Connection pattern

Open, operate, and close within a single tool call. Do not hold persistent IMAP connections — mail servers drop idle connections unpredictably.

```python
from contextlib import contextmanager
import imapclient

@contextmanager
def imap_connection(account: AccountConfig):
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
```

### Async wrapper pattern

```python
import asyncio

async def run_imap(account: AccountConfig, operation, *args, **kwargs):
    loop = asyncio.get_running_loop()
    timeout = int(os.getenv("IMAP_TIMEOUT", "30"))
    def _run():
        with imap_connection(account) as client:
            return operation(client, *args, **kwargs)
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=timeout)
    except (ConnectionError, OSError):
        await asyncio.sleep(1)  # single retry for transient connection failures
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=timeout)
```

Retry policy: one retry with 1-second backoff for connection errors (`ConnectionError`, `OSError`) only. Auth failures, timeouts, and IMAP protocol errors are not retried.

---

## 8. Tool Specification

All tools follow the FastMCP pattern:
- Name: `imap_{action}` snake_case
- Input: Pydantic `BaseModel` with `Field(description=...)` on every parameter
- Output: JSON string (default) or Markdown string (when `response_format="markdown"`)
- Annotations set correctly per tool

### 8.1 `imap_list_accounts`

```
List all configured email accounts with their IDs and labels.
No parameters.
readOnlyHint: true
```

**Returns:** JSON list of `{id, label, default_from}`

---

### 8.2 `imap_list_folders`

```
List all IMAP folders/mailboxes for an account.

Parameters:
  account_id: str  — ID from imap_list_accounts
```

**Returns:** JSON list of `{name, flags, delimiter, message_count, unread_count}`

**Annotations:** `readOnlyHint: true`

---

### 8.3 `imap_list_messages`

```
List messages in a folder with summary metadata. Supports pagination.

Parameters:
  account_id: str
  folder: str           — default "INBOX"
  limit: int            — default 20, max 100
  offset: int           — default 0
  unread_only: bool     — default false
  sort_by: enum         — "date_desc" | "date_asc" | "from" | "subject"  default "date_desc"
  response_format: enum — "json" | "markdown"  default "markdown"
```

**Returns:** Paginated list of message summaries: `{uid, subject, from, to, date, size_kb, has_attachments, is_read, is_flagged}`
Include `total`, `offset`, `has_more`, `next_offset` pagination envelope.

**Annotations:** `readOnlyHint: true`

---

### 8.4 `imap_get_message`

```
Fetch the full content of a single message by UID.

Parameters:
  account_id: str
  folder: str
  uid: int              — message UID from imap_list_messages
  prefer_plain: bool    — default true (strip HTML to plain text via nh3)
  include_headers: bool — default false
  response_format: enum
```

**Returns:** `{uid, subject, from, to, cc, bcc, date, body, attachments: [{filename, content_type, size_kb}]}`
- Strip HTML to clean plaintext unless `prefer_plain=false`
- Never return raw attachment binary — metadata only
- Truncate body at 50,000 characters; include `body_truncated: true` flag if hit

**Annotations:** `readOnlyHint: true`

---

### 8.5 `imap_search_messages`

```
Search messages using IMAP SEARCH criteria.

Parameters:
  account_id: str
  folder: str           — default "INBOX"
  query: str            — maps to IMAP TEXT criterion (searches full message including headers)
  from_address: str     — optional
  to_address: str       — optional
  subject: str          — optional
  since_date: str       — ISO date "YYYY-MM-DD"
  before_date: str      — ISO date "YYYY-MM-DD"
  is_unread: bool       — optional
  is_flagged: bool      — optional
  limit: int            — default 20, max 100
  response_format: enum
```

**Returns:** Same shape as `imap_list_messages` + pagination envelope.

**Annotations:** `readOnlyHint: true`

---

### 8.6 `imap_send_email`

```
Compose and send a new email via SMTP.

Parameters:
  account_id: str
  to: list[str]         — recipient addresses
  subject: str
  body: str             — plain text body
  cc: list[str]         — optional
  bcc: list[str]        — optional
  reply_to: str         — optional
```

**Returns:** `{status: "sent", message_id: str}`
- Validate all addresses via `email-validator` before sending
- Build MIME message via `email.mime`
- Send via `aiosmtplib`
- Enforce `SMTP_RATE_LIMIT` (sliding window, in-memory) — return `SMTP_RATE_LIMITED` if exceeded

**Annotations:** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`

---

### 8.7 `imap_reply`

```
Reply to an existing message, preserving threading headers.

Parameters:
  account_id: str
  folder: str
  uid: int              — UID of message being replied to
  body: str             — plain text reply body
  reply_all: bool       — default false
  include_original: bool — default true
```

**Returns:** `{status: "sent", message_id: str}`

Fetch original to populate `In-Reply-To`, `References`, and `Re:` subject prefix correctly. Subject to `SMTP_RATE_LIMIT` (shared counter with `imap_send_email`).

**Annotations:** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`

---

### 8.8 `imap_move_message`

```
Move a message to a different folder.

Parameters:
  account_id: str
  folder: str           — source folder
  uid: int
  destination_folder: str
```

**Returns:** `{status: "moved", uid: int, destination: str}`

**Annotations:** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`

---

### 8.9 `imap_delete_message`

```
Move a message to Trash (does NOT permanently expunge).

Parameters:
  account_id: str
  folder: str
  uid: int
```

**Returns:** `{status: "trashed", uid: int}`

Auto-detect Trash folder via `\Trash` flag or common names (`Trash`, `Deleted Items`, `INBOX.Trash`). Never call `EXPUNGE` directly.

**Annotations:** `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: true`

---

### 8.10 `imap_set_flags`

```
Mark messages as read, unread, flagged, or unflagged.

Parameters:
  account_id: str
  folder: str
  uids: list[int]       — one or more UIDs
  mark_read: bool       — optional
  mark_flagged: bool    — optional
```

**Returns:** `{status: "updated", uids: list[int], flags_set: list[str]}`

**Annotations:** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`

---

### 8.11 `imap_get_thread`

```
Fetch all messages in a thread via Message-ID / References headers.

Parameters:
  account_id: str
  folder: str
  uid: int              — any UID in the thread
  limit: int            — default 20, max 50
  offset: int           — default 0
  response_format: enum
```

**Returns:** Ordered list of message summaries (same shape as `imap_list_messages`), oldest first. Includes `total`, `offset`, `has_more`, `next_offset` pagination envelope.

Thread reconstruction is scoped to the specified folder. Messages in other folders (e.g., Sent) are not included.

**Annotations:** `readOnlyHint: true`

---

## 9. Lifespan & Connection Management

Use FastMCP lifespan to validate config and verify connectivity at startup:

```python
@asynccontextmanager
async def app_lifespan():
    config = load_accounts_config()
    loop = asyncio.get_running_loop()
    for account in config.accounts:
        await loop.run_in_executor(None, verify_account_connectivity, account)
    yield {"accounts": {a.id: a for a in config.accounts}}
```

Fail fast with a clear error if any account cannot connect at startup.

### Health Check Endpoint

Register a `/health` route on the ASGI app (outside MCP tool dispatch, not behind Bearer auth). Returns HTTP 200 with JSON body:

```json
{"status": "ok", "accounts": {"personal": "connected", "work": "connected"}}
```

Or HTTP 503 if any account fails a lightweight IMAP NOOP check. Nginx or external monitors can poll this to distinguish "process running" from "service healthy."

---

## 10. Error Handling

All tools must catch IMAP/SMTP exceptions and return structured errors:

```json
{
  "error": "IMAP_AUTH_FAILED",
  "message": "Login failed for account 'personal'. Check credentials in accounts.yaml.",
  "account_id": "personal"
}
```

| Error Code | Trigger |
|---|---|
| `IMAP_AUTH_FAILED` | Bad credentials |
| `IMAP_CONNECTION_ERROR` | Host unreachable / TLS failure |
| `IMAP_FOLDER_NOT_FOUND` | Invalid folder name |
| `IMAP_MESSAGE_NOT_FOUND` | UID no longer exists |
| `SMTP_SEND_FAILED` | SMTP rejection / auth failure |
| `INVALID_EMAIL_ADDRESS` | Validation failure before send |
| `ACCOUNT_NOT_FOUND` | Unknown `account_id` |
| `BODY_FETCH_FAILED` | Partial IMAP FETCH failure |
| `IMAP_TIMEOUT` | IMAP operation exceeded `IMAP_TIMEOUT` seconds |
| `SMTP_TIMEOUT` | SMTP send exceeded `SMTP_TIMEOUT` seconds |
| `IMAP_UIDVALIDITY_CHANGED` | Folder UIDVALIDITY changed since listing — UIDs are stale |
| `SMTP_RATE_LIMITED` | Send rate exceeded `SMTP_RATE_LIMIT` per minute — retry after cooldown |

### 10.1 Logging

Use `structlog` configured for JSON output to stdout (captured by journald via the systemd unit).

**Log events:**

| Event | Level | Bound fields |
|---|---|---|
| Tool invocation start | INFO | `tool`, `account_id`, `params` (sanitized — no passwords) |
| Tool invocation complete | INFO | `tool`, `account_id`, `duration_ms`, `result_size` |
| Auth failure | WARNING | `remote_ip`, `reason` |
| IMAP/SMTP error | ERROR | `tool`, `account_id`, `error_code`, `message` |
| Timeout | WARNING | `tool`, `account_id`, `timeout_seconds` |
| Startup account verification | INFO | `account_id`, `status` |

Configure structlog in `server.py` lifespan:

```python
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    ),
)
```

---

## 11. Nginx Vhost Config

Add to the Nginx config on the **Proxmox host** (not inside the LXC). TLS hardening (`ssl_protocols`, `ssl_ciphers`, HSTS) is configured in the host's shared Nginx SSL snippet, not repeated per-vhost:

```nginx
server {
    listen 443 ssl http2;
    server_name mailbridge.l3digital.net;

    ssl_certificate     /etc/letsencrypt/live/l3digital.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/l3digital.net/privkey.pem;

    location / {
        proxy_pass         http://<lxc-static-ip>:8765;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_buffering    off;              # required for SSE streaming
        proxy_cache        off;
        proxy_read_timeout 300s;             # streamable HTTP may hold connections open
    }
}
```

Replace `<lxc-static-ip>` with the static IP assigned to the container.

---

## 12. Proxmox LXC Container Setup

### 12.1 Create the container (on Proxmox host)

```bash
# Update template list and download Debian 13 if not cached
pveam update
pveam download local debian-13-standard_13.0-1_amd64.tar.zst

# Create unprivileged LXC — adjust CTID, storage, bridge, and IP to match your environment
pct create 120 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname mailbridge-mcp \
  --cores 2 \
  --memory 512 \
  --swap 256 \
  --storage local-lvm \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.120/24,gw=192.168.1.1 \
  --unprivileged 1 \
  --start 1

pct enter 120
```

Use a static IP — not DHCP — so the Nginx proxy target never changes.

### 12.2 Bootstrap inside the container

```bash
apt update && apt install -y python3.13 python3.13-venv python3-pip git

# Create dedicated service user (no login shell)
useradd -r -s /bin/false -d /opt/mailbridge-mcp imapmcp
mkdir -p /opt/mailbridge-mcp /etc/mailbridge-mcp

# Clone repo
git clone https://github.com/l3digital/mailbridge-mcp.git /opt/mailbridge-mcp

# Deploy config files
cp /opt/mailbridge-mcp/config/accounts.yaml.example /etc/mailbridge-mcp/accounts.yaml
cp /opt/mailbridge-mcp/.env.example /opt/mailbridge-mcp/.env

# Fill in real credentials, then lock down permissions
chmod 600 /etc/mailbridge-mcp/accounts.yaml /opt/mailbridge-mcp/.env
chown -R imapmcp:imapmcp /opt/mailbridge-mcp /etc/mailbridge-mcp

# Create venv and install
cd /opt/mailbridge-mcp
python3.13 -m venv .venv
.venv/bin/pip install -e .
```

### 12.3 Systemd service unit

Create `/etc/systemd/system/mailbridge-mcp.service`:

```ini
[Unit]
Description=mailbridge-mcp MCP Server
After=network.target

[Service]
Type=simple
User=imapmcp
Group=imapmcp
WorkingDirectory=/opt/mailbridge-mcp
EnvironmentFile=/opt/mailbridge-mcp/.env
ExecStart=/opt/mailbridge-mcp/.venv/bin/python -m mailbridge_mcp.server
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now mailbridge-mcp
systemctl status mailbridge-mcp
journalctl -fu mailbridge-mcp
```

### 12.4 CI/CD via GitHub Actions

- **CI (on push/PR):** lint (`ruff`), type check (`mypy`), run `pytest` suite
- **Deploy (on merge to `main`):** SSH into the LXC, `git pull`, `.venv/bin/pip install -e .`, `systemctl restart mailbridge-mcp`, verify `/health` returns 200

Deploy workflow uses a GitHub Actions self-hosted runner or SSH action targeting the LXC via Tailscale.

---

## 13. Claude.ai Connection

Once deployed, add the server in Claude.ai → Settings → Integrations → Add MCP Server:

```
URL:    https://mailbridge.l3digital.net/mcp
Header: Authorization: Bearer <MCP_API_KEY>
```

Claude.ai will enumerate available tools on connect.

---

## 14. Security Checklist

- [ ] `MCP_API_KEY` is a cryptographically random token (≥32 bytes, base64url encoded)
- [ ] All IMAP/SMTP passwords live in env vars only — never in YAML plaintext
- [ ] `/etc/mailbridge-mcp/accounts.yaml` is chmod 600, owned by `imapmcp`
- [ ] `/opt/mailbridge-mcp/.env` is chmod 600, owned by `imapmcp`
- [ ] Systemd service runs as unprivileged `imapmcp` user, not root
- [ ] LXC container has no public IP — only the Nginx proxy on the host is exposed
- [ ] Nginx enforces TLS 1.2+ only, HSTS header set
- [ ] Body truncated at 50,000 chars — prevents context-window exhaustion
- [ ] `imap_delete_message` moves to Trash only — never calls `EXPUNGE`
- [ ] No attachment binary data ever returned to the model

---

## 15. Reference Projects (inspiration only, not forked)

| Project | Language | Notable patterns to reference |
|---|---|---|
| `codefuturist/email-mcp` | TypeScript | IMAP IDLE design, AI triage, email scheduling, provider presets |
| `non-dirty/imap-mcp` | Python | MCP resources pattern, OAuth2 flow, folder allowlist config |
| `nikolausm/imap-mcp-server` | TypeScript | AES-256 credential storage approach, connection pooling design |

---

## 16. Implementation Order for Claude Code

1. `pyproject.toml` — all dependency declarations
2. `config.py` — `AccountConfig` Pydantic models, YAML loader, env interpolation
3. `imap_client.py` — connection context manager, async executor wrapper, per-operation functions
4. `smtp_client.py` — `aiosmtplib` send and reply helpers
5. `models.py` — Pydantic input models for all 11 tools
6. `formatters.py` — shared markdown and JSON formatters
7. `auth.py` — `BearerAuthMiddleware`
8. `server.py` — FastMCP app, lifespan, middleware registration, all tool registrations
9. `tests/` — unit tests for config, IMAP client, SMTP client, and tool layer
10. `.github/workflows/` — CI workflow (lint + type check + test) and deploy workflow
11. `README.md` — deployment steps and Claude.ai connection instructions

### Testing Strategy

- **Unit tests** (`pytest` + `pytest-mock`): mock `imapclient` and `aiosmtplib` at the library boundary. Test config loading, env interpolation, YAML validation, error handling, rate limiting, and response formatting.
- **Tool-level tests** (`pytest-asyncio`): test each MCP tool function with mocked IMAP/SMTP clients. Verify annotations, input validation, pagination, and response shapes.
- **No integration tests against live IMAP servers in CI.** Manual smoke tests against a real account during development only.
- **Coverage target:** aim for 90%+ on `config.py`, `imap_client.py`, `smtp_client.py`; 80%+ overall.

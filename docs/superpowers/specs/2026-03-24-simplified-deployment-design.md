# Simplified Deployment Design

**Date:** 2026-03-24
**Status:** Draft
**Scope:** Docker/Podman deployment, config simplification, dual auth mode, updated docs

---

## 1. Problem

The current deployment requires ~9 manual steps across 4 files and 3 infrastructure layers (LXC container, Nginx vhost, systemd unit). Configuration is split between `.env` (secrets) and `accounts.yaml` (structure with `${VAR}` password placeholders that cross-reference `.env`). GitHub OAuth is mandatory even for simple testing. This is too much friction for self-hosters.

## 2. Goals

- Reduce deployment to: edit `.env` + `accounts.yaml`, run `docker compose up` (or `podman compose up`)
- Eliminate the `${VAR}` password placeholder indirection between config files
- Make OAuth optional by supporting Bearer token auth as the default
- Provide auto-TLS via Caddy so users don't need Nginx knowledge
- Keep bare-metal installation as a documented alternative
- Document compatible MCP clients
- Preserve all existing functionality: 11 MCP tools, test suite, CI/CD

## 3. Non-Goals

- REST/OpenAPI layer for non-MCP clients (separate future project)
- Changing tool signatures, behavior, or wire protocol
- Replacing the existing bare-metal deployment on the author's infrastructure
- Docker-based CI (CI stays GitHub Actions with direct Python)

---

## 4. Docker Image

Multi-stage build targeting Python 3.13-slim. Podman compatible (no BuildKit-only syntax, no Docker-specific features).

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
CMD ["python", "-m", "mailbridge_mcp.server"]
```

**Design decisions:**
- `USER nobody` replaces the dedicated service user from bare-metal. Container isolation handles process separation.
- No dev dependencies in final image.
- `accounts.yaml.example` included as a reference; actual config is bind-mounted at runtime.

---

## 5. Docker Compose + Caddy Auto-TLS

Two services: the MCP server and a Caddy reverse proxy that auto-provisions Let's Encrypt certificates.

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

The mailbridge volume mount targets `/etc/mailbridge-mcp/accounts.yaml` to match the existing `ACCOUNTS_CONFIG_PATH` default in `Settings`. The caddy service gets `env_file: .env` so it can resolve `{$DOMAIN}` from the same config file.

**Caddyfile:**

```
{$DOMAIN} {
    reverse_proxy mailbridge:8765
}
```

`DOMAIN` is read from `.env`. Caddy handles:
- Let's Encrypt certificate provisioning on first start
- Automatic renewal (~30 days before expiry, checked every 12 hours)
- HTTP-to-HTTPS redirect
- HSTS headers

The `caddy_data` volume persists the ACME account key and issued certificates across container restarts. Without it, restarts would re-request certs and hit Let's Encrypt rate limits.

Note: OCSP stapling is no longer relevant. Let's Encrypt shut down their OCSP responders on 2025-08-06 and moved to CRLs.

**Podman compatibility:**
- Works with `podman compose` (podman 4.7+) or `podman-compose`
- Named volumes, `expose`, and inter-service networking all work via podman pods
- No `depends_on` with health checks (limited podman-compose support); the mailbridge app already handles startup ordering via fail-fast account verification

---

## 6. Simplified Configuration

### Current state (two files, cross-referenced)

`.env` contains passwords as named variables (`PERSONAL_IMAP_PASSWORD=xxx`). `accounts.yaml` references them via `password: "${PERSONAL_IMAP_PASSWORD}"`. Adding an account requires editing both files and inventing matching variable names.

### New state (two files, no cross-referencing)

**`accounts.yaml`** defines account structure without passwords:

```yaml
accounts:
  - id: personal
    label: "Personal (me@example.com)"
    imap:
      host: mail.example.com
      port: 993
      tls: true
      username: me@example.com
    smtp:
      host: mail.example.com
      port: 587
      starttls: true
      username: me@example.com
    default_from: "My Name <me@example.com>"
```

**`.env`** contains passwords by convention:

```env
PERSONAL_IMAP_PASSWORD=xxx
PERSONAL_SMTP_PASSWORD=xxx
```

At load time, `config.py` resolves passwords from env vars using the convention `{ACCOUNT_ID_UPPER}_IMAP_PASSWORD` and `{ACCOUNT_ID_UPPER}_SMTP_PASSWORD`. The account `id` field is the namespace.

### Changes to `config.py`

- `password` field in `ImapConfig` and `SmtpConfig` becomes `Optional[str] = None`
- After YAML load, if `password` is `None`, look up `{account.id.upper()}_IMAP_PASSWORD` (or `_SMTP_PASSWORD`) from env
- If neither YAML nor env provides a password, raise a clear error at startup with the expected env var name
- The `${VAR}` interpolation code remains for backward compatibility but is not the documented primary path

### Updated `.env.example`

```env
# Auth (pick one mode)
AUTH_MODE=bearer                          # "bearer" or "github_oauth"
MCP_API_KEY=                              # required if AUTH_MODE=bearer

# Uncomment for Claude.ai web access (GitHub OAuth):
# AUTH_MODE=github_oauth
# GITHUB_OAUTH_CLIENT_ID=
# GITHUB_OAUTH_CLIENT_SECRET=
# MCP_PUBLIC_HOST=mcp.example.com

# Server
DOMAIN=mcp.example.com                   # used by Caddy for auto-TLS
MCP_HOST=0.0.0.0                         # listen address
MCP_PORT=8765                            # listen port

# Timeouts
IMAP_TIMEOUT=30                          # seconds per IMAP operation
SMTP_TIMEOUT=30                          # seconds per SMTP send

# Rate limits
SMTP_RATE_LIMIT=10                       # max sends per minute (0 = unlimited)
IMAP_RATE_LIMIT=60                       # max IMAP ops per minute per account (0 = unlimited)

# Log
LOG_LEVEL=INFO                           # DEBUG | INFO | WARNING | ERROR

# Config path (default works for both Docker mount and bare-metal)
# ACCOUNTS_CONFIG_PATH=/etc/mailbridge-mcp/accounts.yaml

# Account passwords (convention: {ACCOUNT_ID}_IMAP_PASSWORD / _SMTP_PASSWORD)
PERSONAL_IMAP_PASSWORD=
PERSONAL_SMTP_PASSWORD=
WORK_IMAP_PASSWORD=
WORK_SMTP_PASSWORD=
```

---

## 7. Dual Auth Mode

Controlled by `AUTH_MODE` env var.

### `AUTH_MODE=bearer` (default)

- Wires `BearerAuthMiddleware` (already exists in `auth.py`) into the ASGI app
- Requires `MCP_API_KEY` env var; startup fails without it
- No GitHub OAuth env vars needed
- `/health` bypasses auth (already implemented in `BearerAuthMiddleware`)
- Claude Code connects with `--header "Authorization: Bearer <key>"`
- Claude.ai web client cannot use this mode (requires OAuth)

### `AUTH_MODE=github_oauth`

- Current behavior: `GitHubProvider` handles the full OAuth 2.1 flow
- Requires `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `MCP_PUBLIC_HOST`
- Claude.ai web users connect via the OAuth redirect flow

### Changes to `server.py`

**Key structural change (proposed — the current code does not do this yet):** Move all auth setup and `mcp` instantiation out of module-level code and into `create_app()`. Currently, `server.py` creates the `mcp` instance and configures `GitHubProvider` at module level (lines 92-111), which crashes at import time without OAuth env vars and blocks test imports. The proposed refactor defers all of this to `create_app()`, making the auth mode conditional on a function call rather than on import. This also requires moving `@mcp.tool()` decorator registration into `create_app()` (or using a deferred registration pattern), since the decorators currently run at module level against the module-level `mcp` instance.

```python
# Module level: only define lifespan and tools as registerable functions.
# No auth setup, no mcp instantiation.

def create_app() -> Any:
    auth_mode = os.getenv("AUTH_MODE", "bearer").lower()

    if auth_mode == "github_oauth":
        client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
        client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
        if not (client_id and client_secret):
            raise RuntimeError(
                "AUTH_MODE=github_oauth requires GITHUB_OAUTH_CLIENT_ID "
                "and GITHUB_OAUTH_CLIENT_SECRET."
            )
        auth_provider = GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=f"https://{os.getenv('MCP_PUBLIC_HOST', 'localhost')}",
        )
        mcp = FastMCP("mailbridge-mcp", auth=auth_provider, lifespan=app_lifespan)
    elif auth_mode == "bearer":
        api_key = settings.mcp_api_key
        if not api_key:
            raise RuntimeError(
                "AUTH_MODE=bearer requires MCP_API_KEY to be set."
            )
        mcp = FastMCP("mailbridge-mcp", lifespan=app_lifespan)
    else:
        raise RuntimeError(
            f"Unknown AUTH_MODE: {auth_mode}. Use 'bearer' or 'github_oauth'."
        )

    # Register tools on the mcp instance...
    # Register middleware...

    http_app = mcp.http_app(transport="streamable-http", path="/")

    if auth_mode == "bearer":
        http_app.add_middleware(BearerAuthMiddleware, api_key=settings.mcp_api_key)

    http_app.add_route("/health", health_check, methods=["GET"])
    return http_app
```

This requires moving tool registration into `create_app()` (or using a deferred registration pattern). The tool functions themselves stay in `tools_read.py` and `tools_write.py`; only the `@mcp.tool()` decorator calls move.

### Impact on tests

- Tests currently set dummy `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` to avoid the import-time crash
- With auth setup moved to `create_app()`, importing `server` no longer crashes
- Tests set `AUTH_MODE=bearer` + `MCP_API_KEY=test` in their environment
- CI workflow updated: replace `GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` env vars with `AUTH_MODE=bearer` and `MCP_API_KEY=test`
- Local test invocation: `AUTH_MODE=bearer MCP_API_KEY=test pytest` (or set in a `.env.test` / `pyproject.toml` `[tool.pytest.ini_options]`)

---

## 8. Bare-Metal Installation

Kept as a documented alternative alongside Docker. Instructions are distro-agnostic with package commands for both `apt` (Debian/Ubuntu) and `dnf` (Fedora/RHEL). Python requirement: 3.13+.

### Package installation

**Debian/Ubuntu (apt):**
```bash
apt update && apt install -y python3 python3-venv python3-pip git
```

**Fedora/RHEL (dnf):**
```bash
dnf install -y python3 python3-pip git
```

### Setup steps

```bash
# Clone
git clone https://github.com/L3DigitalNet/mailbridge-mcp.git /opt/mailbridge-mcp
cd /opt/mailbridge-mcp

# Configure
cp .env.example .env
mkdir -p /etc/mailbridge-mcp
cp config/accounts.yaml.example /etc/mailbridge-mcp/accounts.yaml
# Edit .env (set AUTH_MODE, MCP_API_KEY, account passwords)
# Edit /etc/mailbridge-mcp/accounts.yaml (set hosts, usernames, account IDs)

# Create venv and install
python3 -m venv .venv
.venv/bin/pip install -e .

# Create service user
useradd -r -s /bin/false -d /opt/mailbridge-mcp mailbridge
chown -R mailbridge:mailbridge /opt/mailbridge-mcp
chown -R mailbridge:mailbridge /etc/mailbridge-mcp
chmod 600 .env /etc/mailbridge-mcp/accounts.yaml

# Systemd unit (same as current)
# Reverse proxy (user's choice: Nginx, Caddy, etc.)
```

The default `ACCOUNTS_CONFIG_PATH` is `/etc/mailbridge-mcp/accounts.yaml`, which works for both Docker (bind-mounted to this path) and bare-metal. Override via env var if needed.

The bare-metal path benefits from the same config and auth simplifications (convention-based passwords, `AUTH_MODE=bearer` default).

---

## 9. Compatible MCP Clients

A new README section documenting which LLM tools can connect:

- **Claude Code** (CLI) -- Bearer token or OAuth
- **Claude.ai** (web) -- GitHub OAuth required
- **Cursor** -- MCP client support
- **Windsurf** -- MCP client support
- **VS Code** (via MCP extensions) -- MCP client support
- **OpenAI tools** (where MCP support has been announced)
- **Any client using the `mcp` Python or TypeScript SDK**

Note: MCP is a JSON-RPC protocol, not REST/OpenAPI. Non-MCP clients cannot connect directly.

---

## 10. Files Changed

| File | Change |
|------|--------|
| `Dockerfile` | **New** -- multi-stage Python 3.13-slim build |
| `docker-compose.yml` | **New** -- mailbridge + caddy services |
| `Caddyfile` | **New** -- 3-line reverse proxy config |
| `.env.example` | **Updated** -- `AUTH_MODE`, `DOMAIN`, removed `${VAR}` references |
| `config/accounts.yaml.example` | **Updated** -- removed `password` fields |
| `mailbridge_mcp/config.py` | **Updated** -- convention-based password resolution |
| `mailbridge_mcp/server.py` | **Updated** -- conditional auth mode |
| `README.md` | **Updated** -- Docker + bare-metal quick starts, compatible clients |
| `.github/workflows/ci.yml` | **Updated** -- `AUTH_MODE=bearer` in test env |
| Tests | **Updated** -- use `AUTH_MODE=bearer` + `MCP_API_KEY=test` |

## 11. What Stays the Same

- All 11 MCP tools, their signatures, annotations, and behavior
- IMAP/SMTP client code (`imap_client.py`, `smtp_client.py`)
- Input validation (`models.py`), error sanitization (`formatters.py`)
- Rate limiting (IMAP per-account, SMTP shared)
- Test suite (108 tests, updated env vars only)
- Deploy workflow (SSH + systemd, can be updated to Docker separately)
- Design doc remains authoritative spec

---

## 12. User Experience: Before and After

| Before | After |
|--------|-------|
| Create Proxmox LXC container | `docker compose up` (or `podman compose up`) |
| Install Python 3.13, create venv | Built into Docker image |
| Create service user, set permissions | Container runs as `nobody` |
| Write systemd unit | `restart: unless-stopped` in compose |
| Configure Nginx vhost + TLS certs | Caddy auto-TLS (3-line Caddyfile) |
| Register GitHub OAuth App | Optional (Bearer mode is default) |
| Cross-reference `.env` and `accounts.yaml` with `${VAR}` | Passwords auto-resolve from `{ID}_IMAP_PASSWORD` env vars |
| ~9 manual steps | 2 steps: edit config, start containers |

Bare-metal users also benefit from the config and auth simplifications even without Docker.

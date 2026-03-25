# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Remote MCP server exposing IMAP/SMTP email operations to Claude.ai over streamable HTTP. Python 3.13, FastMCP, deployed as a bare-metal systemd service inside a Proxmox LXC container (Debian 13 Trixie) on Hetzner EX130-R.

The authoritative specification is `docs/mailbridge-mcp-design.md`. All implementation decisions, tool signatures, error codes, and deployment details are defined there. Read it before making changes.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run server locally (requires GitHub OAuth env vars)
GITHUB_OAUTH_CLIENT_ID=xxx GITHUB_OAUTH_CLIENT_SECRET=xxx python -m mailbridge_mcp.server

# Lint + format
ruff check .
ruff format .

# Type check
mypy mailbridge_mcp/

# Test (OAuth env vars required — server module raises RuntimeError at import without them)
GITHUB_OAUTH_CLIENT_ID=test GITHUB_OAUTH_CLIENT_SECRET=test pytest
pytest tests/test_config.py              # single file
pytest tests/test_tools.py -k "test_send" # single test by name
pytest --cov=mailbridge_mcp              # with coverage

# CI reproduces: ruff check + mypy + pytest
```

## Architecture

```
Claude.ai ──HTTPS──▶ Nginx (Proxmox host) ──HTTP:8765──▶ FastMCP server (LXC)
                        (limit_req burst=200)                ├── IMAP/TLS ──▶ mail servers
                                                             └── SMTP/TLS ──▶ mail servers
```

**Request flow:** Claude.ai initiates a GitHub OAuth flow (via `GitHubProvider` from `fastmcp.server.auth.providers.github`). After OAuth completes, Claude.ai sends POST requests to the root path `/`. The MCP app is mounted at `path="/"` (not `/mcp`) because Claude.ai probes `POST /` and expects `/.well-known/oauth-protected-resource` at the root. `/health` bypasses auth.

**IMAP is sync, server is async:** `imapclient` is synchronous. Every IMAP operation runs inside `asyncio.get_running_loop().run_in_executor(None, ...)` wrapped with `asyncio.wait_for` for timeout enforcement. Each tool call opens a fresh connection (connect, operate, disconnect) — no persistent connections or pooling.

**Config layering:** `.env` holds secrets and server params. `accounts.yaml` defines mail accounts with `${VAR}` placeholders resolved from env at startup. Pydantic models validate both layers.

## Design Principles

These were established during design review and must be maintained:

1. **Secrets externalization** — credentials only in env vars, never in code/YAML/VCS
2. **Stateless connections** — open, operate, close per tool call; no connection pooling
3. **No binary exposure** — attachment metadata only, never binary content
4. **Safe deletion** — move to Trash only, never EXPUNGE
5. **Context-window safety** — body truncated at 50K chars; thread/list responses paginated
6. **Fail-fast startup** — verify all account connectivity at startup, abort on failure
7. **Greenfield** — original code only, reference projects for patterns not code

## Key Implementation Patterns

**IMAP async wrapper** — all IMAP operations go through `run_imap()` which handles executor dispatch, 30s timeout, and single retry on `ConnectionError`/`OSError`. Auth failures and timeouts are not retried.

**IMAP rate limiting** — per-account sliding window counter (default 60 ops/min via `IMAP_RATE_LIMIT` env var). Each tool call opens a fresh TCP+TLS connection, so this prevents overwhelming mail servers. Raises `RuntimeError` when exceeded.

**UIDVALIDITY** — capture on folder SELECT, include in list/search responses. On UID-based operations, compare current UIDVALIDITY against the listing epoch. Return `IMAP_UIDVALIDITY_CHANGED` on mismatch rather than operating on the wrong message.

**SMTP rate limiting** — shared in-memory sliding window counter (default 10/min via `SMTP_RATE_LIMIT`). Applies to both `imap_send_email` and `imap_reply`. Return `SMTP_RATE_LIMITED` when exceeded.

**Tool invocation logging** — MCP middleware (`_log_tool_calls`) logs every tool call start/complete/error with `tool`, `account_id`, `duration_ms`, and `result_size`. Passwords filtered from logged params.

**Error sanitization** — `_sanitize_error_message()` in `formatters.py` strips email addresses, hostnames, and IPs from error messages before returning them to the model. IMAP/SMTP libraries leak server details in exceptions.

**Input validation** — Pydantic models in `models.py` enforce: folder names via safe-character regex (max 255 chars), search queries max 500 chars, UIDs `ge=1`, UID lists max 1000, subject/reply_to reject CR/LF (header injection prevention).

**Error responses** — structured JSON: `{error: "ERROR_CODE", message: "...", account_id: "..."}`. All error codes are enumerated in the design doc section 10.

**MCP tool annotations** — every tool must set `readOnlyHint`, `destructiveHint`, `idempotentHint`. Read tools are `readOnlyHint: true`. Only `imap_delete_message` is `destructiveHint: true`.

## Testing

108 tests. Mock `imapclient` and `aiosmtplib` at the library boundary. No live IMAP server in CI. Coverage target: 90%+ on core modules (`config.py`, `imap_client.py`, `smtp_client.py`), 80%+ overall. Tests require `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` env vars (any non-empty value works for test runs).

## Deployment

GitHub Actions CI (ruff + mypy + pytest) on push/PR. Deploy on merge to `main`: SSH into LXC via Tailscale, git pull, pip install, systemctl restart, verify `/health` returns 200.

## TLS Notes

**OCSP stapling is dead.** Let's Encrypt shut down their OCSP responders on 2025-08-06 and stopped including OCSP URLs in certs from 2025-05-07. The industry moved to CRLs. Do not reference OCSP stapling in docs or configs. Caddy auto-TLS still works, it just no longer staples OCSP responses.

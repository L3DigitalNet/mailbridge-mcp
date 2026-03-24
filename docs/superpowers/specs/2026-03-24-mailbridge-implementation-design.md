# mailbridge-mcp Implementation Design

**Date:** 2026-03-24
**Status:** Approved
**Design spec:** `docs/mailbridge-mcp-design.md` (reviewed, 25 findings resolved across 3 passes)

## Context

The design spec defines a complete MCP server with 11 tools, IMAP/SMTP clients, bearer auth, structured logging, and LXC deployment. This document defines the implementation strategy: how to build it, in what order, and what each milestone validates.

## Approach: Vertical Slices

Build a working server with one tool first, validate the stack end-to-end, then add tools incrementally. This catches integration issues early (FastMCP transport, auth middleware, structlog, ASGI lifecycle) before investing in all 11 tools.

## Slice 1: Skeleton + First Tool

**Goal:** A running MCP server that Claude.ai can connect to with one working tool.

**Files created:**
- `pyproject.toml` — full dependency declarations from design spec section 4
- `mailbridge_mcp/__init__.py` — package marker
- `mailbridge_mcp/config.py` — `AccountConfig` Pydantic models, YAML loader with `${VAR}` env interpolation, `Settings` from pydantic-settings
- `mailbridge_mcp/auth.py` — `BearerAuthMiddleware` with `hmac.compare_digest` and `/health` bypass
- `mailbridge_mcp/server.py` — FastMCP app, lifespan (structlog config, accounts loading, connectivity verify), `/health` endpoint, `imap_list_accounts` tool
- `.env.example` — all env vars from design spec section 5.1
- `config/accounts.yaml.example` — example account config from design spec section 5.2

**Validation:**
- `python -m mailbridge_mcp.server` starts without error
- `curl localhost:8765/health` returns 200
- `curl -H "Authorization: Bearer <key>" localhost:8765/mcp` returns tool list
- `imap_list_accounts` returns configured accounts

**What this proves:** FastMCP streamable HTTP transport works, bearer auth works, config loading works, structlog JSON output works, lifespan lifecycle works.

## Slice 2: IMAP Read Operations

**Goal:** Claude.ai can browse and search email across all configured accounts.

**Files created:**
- `mailbridge_mcp/imap_client.py` — `imap_connection` context manager, `run_imap` async wrapper (executor dispatch, `IMAP_TIMEOUT`, single retry on connection error), UIDVALIDITY capture helper
- `mailbridge_mcp/models.py` — Pydantic input models for all read tools (list_folders, list_messages, get_message, search_messages, get_thread)
- `mailbridge_mcp/formatters.py` — markdown table formatter and JSON response helpers, body truncation (50K limit), HTML-to-plaintext via nh3

**Files modified:**
- `mailbridge_mcp/server.py` — register 5 new tools: `imap_list_folders`, `imap_list_messages`, `imap_get_message`, `imap_search_messages`, `imap_get_thread`

**Files created (tests):**
- `tests/__init__.py`
- `tests/test_config.py` — config loading, env interpolation, YAML validation, missing var handling
- `tests/test_imap_client.py` — connection lifecycle, timeout, retry, UIDVALIDITY

**Validation:**
- All 6 read tools respond correctly with mocked IMAP
- Pagination envelope correct on list/search/thread tools
- Body truncation fires at 50K chars
- UIDVALIDITY mismatch returns `IMAP_UIDVALIDITY_CHANGED`
- Unit tests pass

**What this proves:** The sync-in-executor IMAP pattern works, UIDVALIDITY tracking works, formatters produce correct output, Pydantic models validate input correctly.

## Slice 3: Write Operations + SMTP

**Goal:** Full 11-tool suite. Claude.ai can send, reply, move, delete, and flag messages.

**Files created:**
- `mailbridge_mcp/smtp_client.py` — `send_email` and `send_reply` via aiosmtplib, `SMTP_TIMEOUT` enforcement, sliding window rate limiter (shared counter, `SMTP_RATE_LIMIT`)

**Files modified:**
- `mailbridge_mcp/models.py` — add Pydantic input models for write tools (send_email, reply, move_message, delete_message, set_flags)
- `mailbridge_mcp/server.py` — register 5 new tools: `imap_send_email`, `imap_reply`, `imap_move_message`, `imap_delete_message`, `imap_set_flags`

**Files created (tests):**
- `tests/test_smtp_client.py` — send, rate limiting, timeout
- `tests/test_tools.py` — tool-level tests for all 11 tools with mocked IMAP/SMTP

**Validation:**
- All 11 tools registered with correct annotations
- Rate limiter blocks after 10 sends/minute, shared across send + reply
- `imap_delete_message` moves to Trash, never expunges
- Trash folder auto-detection works
- `imap_reply` sets `In-Reply-To`, `References`, `Re:` prefix correctly
- All structured error codes returned for appropriate failure modes
- Unit tests pass, coverage meets targets (90%+ core, 80%+ overall)

**What this proves:** SMTP integration works, rate limiting works, all error codes are exercised, the full tool suite is operational.

## Slice 4: Polish + Deploy

**Goal:** Production-ready deployment with CI/CD.

**Files created:**
- `.github/workflows/ci.yml` — ruff check + mypy + pytest on push/PR
- `.github/workflows/deploy.yml` — on merge to main: SSH to LXC, git pull, pip install, restart service, verify /health
- `README.md` — project overview, local dev setup, deployment instructions, Claude.ai connection steps

**Validation:**
- CI passes on GitHub Actions
- Deploy workflow successfully updates the LXC
- Claude.ai connects to `https://mailbridge.l3digital.net/mcp` and enumerates all 11 tools
- End-to-end smoke test: list accounts, list folders, read a message, send a test email

## Dependencies Between Slices

```
Slice 1 ──▶ Slice 2 ──▶ Slice 3 ──▶ Slice 4
(skeleton)  (read)      (write)     (deploy)
```

Strictly sequential. Each slice builds on the previous one. No parallelism between slices, though work within a slice can be parallelized where modules are independent (e.g., in Slice 2, `imap_client.py` and `formatters.py` can be written in parallel).

## Testing Strategy

Per design spec section 16:
- Mock `imapclient` and `aiosmtplib` at the library boundary
- No live IMAP/SMTP in CI — manual smoke tests only
- `pytest` + `pytest-asyncio` + `pytest-mock`
- Coverage targets: 90%+ on `config.py`, `imap_client.py`, `smtp_client.py`; 80%+ overall

## Out of Scope

Everything listed in design spec section 1 "Out of Scope (v0.1)" remains out of scope for implementation: attachment downloads, forwarding, drafts, from-address override, OAuth2, IMAP IDLE.

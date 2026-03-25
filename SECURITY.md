# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in mailbridge-mcp, please report it privately rather than opening a public issue.

**Email:** security@l3digital.net

Include:
- Description of the vulnerability
- Steps to reproduce
- Impact assessment (what could an attacker do?)

You should receive a response within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## Scope

This project handles email credentials (IMAP/SMTP passwords) and authenticates via GitHub OAuth. Vulnerabilities in credential handling, authentication bypass, or unauthorized email access are considered critical.

## Security Hardening (as of 2026-03-24)

**Authentication:**
- GitHub OAuth via `GitHubProvider` (FastMCP). Server refuses to start without `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` set.
- No unauthenticated mode exists; the startup check is a hard `RuntimeError`, not a config warning.

**Input validation (Pydantic models in `models.py`):**
- IMAP folder names validated against a safe-character regex (`^[a-zA-Z0-9][a-zA-Z0-9./ _-]{0,254}$`), rejecting glob chars (`*`, `%`), newlines, and other injection vectors.
- Search queries length-limited to 500 characters.
- Email subject and reply_to fields reject CR/LF characters (prevents email header injection).
- UIDs validated `ge=1`; UID lists capped at 1000 entries.

**Error sanitization (`formatters.py`):**
- All error messages returned to the model pass through `_sanitize_error_message()`, which strips email addresses, hostnames, and IP addresses using regex replacement. IMAP/SMTP libraries include server details and usernames in exception messages.

**Rate limiting:**
- SMTP: 10 sends/min shared counter (`SMTP_RATE_LIMIT` env var).
- IMAP: 60 ops/min per account (`IMAP_RATE_LIMIT` env var). Each operation opens a fresh TCP+TLS connection.
- Nginx: `limit_req` with `burst=200` on the vhost for the OAuth flow.

**Supply chain:**
- Dependabot enabled for Python dependencies.
- GitHub secret scanning and push protection enabled on the repository.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

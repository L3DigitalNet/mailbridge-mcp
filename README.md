# Mailbridge MCP

Self-hosted MCP server that gives Claude.ai read/write access to IMAP/SMTP email accounts over streamable HTTP.

```
Claude.ai ──HTTPS──> Nginx (Proxmox host) ──HTTP:8765──> mailbridge-mcp (LXC)
                                                            ├── IMAP/TLS ──> mail servers
                                                            └── SMTP/TLS ──> mail servers
```

Python 3.13, FastMCP, bearer auth, structlog JSON logging. Runs as a systemd service inside a dedicated Proxmox LXC container (Debian 13 Trixie), proxied through Nginx with TLS.

## Tools

11 MCP tools spanning read and write operations across multiple email accounts:

| Tool | Description |
|------|-------------|
| `imap_list_accounts` | List configured accounts with IDs and labels |
| `imap_list_folders` | List all IMAP folders with message/unread counts |
| `imap_list_messages` | Paginated message summaries (JSON or markdown) |
| `imap_get_message` | Full message content with HTML-to-plaintext, 50K body truncation |
| `imap_search_messages` | Search by text, sender, date range, flags |
| `imap_get_thread` | Thread reconstruction via Message-ID/References headers |
| `imap_send_email` | Compose and send with address validation and rate limiting |
| `imap_reply` | Reply with correct `In-Reply-To`/`References` threading |
| `imap_move_message` | Move between folders (COPY + delete pattern) |
| `imap_delete_message` | Move to Trash (auto-detected); never expunges |
| `imap_set_flags` | Mark read/unread, flagged/unflagged |

All tools return structured JSON errors on failure. Attachment content is never returned to the model (metadata only).

## Quick Start

```bash
git clone https://github.com/L3DigitalNet/mailbridge-mcp.git
cd mailbridge-mcp
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Copy and edit the config files:

```bash
cp .env.example .env          # set MCP_API_KEY and mail passwords
cp config/accounts.yaml.example config/accounts.yaml
```

Run the server:

```bash
python -m mailbridge_mcp.server
# Listening on http://0.0.0.0:8765
# Health check: curl http://localhost:8765/health
```

## Configuration

### Environment variables (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_API_KEY` | (required) | Bearer token Claude.ai sends in the `Authorization` header |
| `MCP_HOST` | `0.0.0.0` | Listen address |
| `MCP_PORT` | `8765` | Listen port |
| `IMAP_TIMEOUT` | `30` | Seconds per IMAP operation before timeout |
| `SMTP_TIMEOUT` | `30` | Seconds per SMTP send before timeout |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `SMTP_RATE_LIMIT` | `10` | Max sends per minute across all tools (0 = unlimited) |
| `ACCOUNTS_CONFIG_PATH` | `/etc/mailbridge-mcp/accounts.yaml` | Path to account definitions |

### Account definitions (`accounts.yaml`)

Each account specifies IMAP and SMTP connection details. Passwords use `${VAR}` placeholders resolved from environment variables at startup; they never appear in the YAML.

```yaml
accounts:
  - id: personal
    label: "Personal (me@example.com)"
    imap:
      host: mail.example.com
      port: 993
      tls: true
      username: me@example.com
      password: "${PERSONAL_IMAP_PASSWORD}"
    smtp:
      host: mail.example.com
      port: 587
      starttls: true
      username: me@example.com
      password: "${PERSONAL_SMTP_PASSWORD}"
    default_from: "My Name <me@example.com>"
```

## Deployment

The design doc (`docs/mailbridge-mcp-design.md`) has full deployment instructions. The short version:

1. **LXC container:** Create an unprivileged Debian 13 container on Proxmox with a static IP and Python 3.13.

2. **Install:** Clone the repo to `/opt/mailbridge-mcp`, create a venv, `pip install -e .`, deploy `.env` and `accounts.yaml` with `chmod 600`.

3. **Systemd service:** The unit runs as a dedicated `imapmcp` user:
   ```ini
   ExecStart=/opt/mailbridge-mcp/.venv/bin/python -m mailbridge_mcp.server
   ```

4. **Nginx reverse proxy** on the Proxmox host forwards HTTPS to the container:
   ```nginx
   location / {
       proxy_pass http://<lxc-ip>:8765;
       proxy_buffering off;    # required for SSE streaming
       proxy_read_timeout 300s;
   }
   ```

5. **Connect Claude.ai:** Settings > Integrations > Add MCP Server
   - URL: `https://your-domain.com/mcp`
   - Header: `Authorization: Bearer <MCP_API_KEY>`

### CI/CD

GitHub Actions runs `ruff check`, `mypy`, and `pytest` on every push. A deploy workflow SSH-deploys to the LXC on merge to `main`.

## Development

```bash
ruff check .                          # lint
ruff format .                         # format
mypy mailbridge_mcp/                  # type check
pytest                                # run tests
pytest tests/test_config.py           # single file
pytest -k "test_send"                 # single test by name
pytest --cov=mailbridge_mcp           # coverage report
```

107 tests covering config, IMAP client, SMTP client, formatters, auth middleware, tool behavior, and MCP contract verification.

## Security

- Bearer token validated with `hmac.compare_digest` (timing-safe)
- `/health` endpoint bypasses auth for monitoring
- Credentials in environment variables only, never in code or YAML
- SMTP rate limiting prevents runaway sends
- Delete operations move to Trash; `EXPUNGE` is never called
- Email bodies truncated at 50,000 characters to protect context windows
- Attachment binary content is never returned (metadata only)

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

MIT

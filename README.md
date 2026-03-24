# Mailbridge MCP

Self-hosted MCP server bridging Claude.ai to IMAP/SMTP email accounts over streamable HTTP.

## What it does

Gives Claude.ai read/write access to email through 11 MCP tools: list accounts, list folders, list/search/read messages, follow threads, send email, reply, move, delete (to Trash), and set flags. Runs as a systemd service inside a Proxmox LXC container, proxied through Nginx with TLS.

## Quick start (local dev)

```bash
# Clone and install
git clone https://github.com/l3digital/mailbridge-mcp.git
cd mailbridge-mcp
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Configure
cp .env.example .env
cp config/accounts.yaml.example config/accounts.yaml
# Edit .env and accounts.yaml with your credentials

# Run
python -m mailbridge_mcp.server
```

The server starts on `http://0.0.0.0:8765`. Health check at `/health`.

## Configuration

**`.env`** holds server settings, timeouts, and the bearer token (`MCP_API_KEY`).

**`accounts.yaml`** defines mail accounts. Passwords use `${VAR}` syntax resolved from environment variables at startup; they never appear in the YAML file itself.

See `.env.example` and `config/accounts.yaml.example` for all available options.

## Deployment

The server runs inside a dedicated Proxmox LXC container (Debian 13, Python 3.13). Nginx on the host reverse-proxies HTTPS to the container's port 8765.

1. Create the LXC and bootstrap Python (see `docs/mailbridge-mcp-design.md` section 12)
2. Clone, install, configure `.env` and `accounts.yaml`
3. Install the systemd unit, enable and start the service
4. Add the Nginx vhost (section 11 of the design doc)
5. In Claude.ai: Settings > Integrations > Add MCP Server
   - URL: `https://mailbridge.l3digital.net/mcp`
   - Header: `Authorization: Bearer <MCP_API_KEY>`

GitHub Actions CI runs on push (lint, type check, test). Deploy workflow triggers on merge to `main` and SSH-deploys to the LXC.

## Development

```bash
ruff check .              # lint
ruff format .             # format
mypy mailbridge_mcp/      # type check
pytest                    # test
pytest --cov=mailbridge_mcp  # test with coverage
```

## License

MIT

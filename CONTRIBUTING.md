# Contributing

Thanks for your interest in mailbridge-mcp. This project is in early development.

## Getting Started

```bash
git clone https://github.com/L3DigitalNet/mailbridge-mcp.git
cd mailbridge-mcp
uv venv --python 3.13 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Development Workflow

1. Create a branch from `main`
2. Make your changes
3. Run the checks: `ruff check . && mypy mailbridge_mcp/ && pytest`
4. Commit with a descriptive message
5. Open a PR against `main`

## Code Standards

- Python 3.13+, type hints throughout
- `ruff` for linting and formatting (config in `pyproject.toml`)
- `mypy` strict mode for type checking
- `pytest` with async support for tests
- Mock `imapclient` and `aiosmtplib` at the library boundary; no live IMAP/SMTP in CI

## Architecture

Read `docs/mailbridge-mcp-design.md` for the full specification. The design doc is the source of truth for tool signatures, error codes, and deployment details.

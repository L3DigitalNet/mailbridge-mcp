from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

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
                f"Environment variable {var_name} referenced in accounts.yaml but not set"
            )
        return val

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve env vars in a dict."""
    resolved: dict[str, Any] = {}
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

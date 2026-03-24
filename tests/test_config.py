from pathlib import Path

import pytest
import yaml

from mailbridge_mcp.config import AccountConfig, ImapConfig, Settings, load_accounts


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


def test_load_accounts_resolves_env_vars(
    accounts_yaml: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TEST_IMAP_PASSWORD", "imap-secret")
    monkeypatch.setenv("TEST_SMTP_PASSWORD", "smtp-secret")
    accounts = load_accounts(accounts_yaml)
    assert len(accounts) == 1
    assert accounts[0].id == "test"
    assert accounts[0].imap.password == "imap-secret"
    assert accounts[0].smtp.password == "smtp-secret"


def test_load_accounts_missing_env_var_raises(accounts_yaml: Path):
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


def test_account_config_roundtrip():
    """Verify a fully specified AccountConfig validates and serializes."""
    account = AccountConfig(
        id="demo",
        label="Demo",
        imap=ImapConfig(
            host="imap.demo.com", port=993, tls=True,
            username="u@demo.com", password="pass",
        ),
        smtp={
            "host": "smtp.demo.com", "port": 587, "starttls": True,
            "username": "u@demo.com", "password": "pass",
        },
        default_from="Demo <u@demo.com>",
    )
    assert account.id == "demo"
    assert account.smtp.host == "smtp.demo.com"

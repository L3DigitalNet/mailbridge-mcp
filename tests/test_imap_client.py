from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig
from mailbridge_mcp.imap_client import get_uidvalidity, imap_connection, run_imap


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="test",
        label="Test",
        imap=ImapConfig(
            host="imap.test.com", port=993, tls=True,
            username="user@test.com", password="pass",
        ),
        smtp=SmtpConfig(
            host="smtp.test.com", port=587, starttls=True,
            username="user@test.com", password="pass",
        ),
        default_from="Test <user@test.com>",
    )


def test_imap_connection_logs_in_and_out(account: AccountConfig):
    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        with imap_connection(account) as client:
            assert client is mock_client
            mock_client.login.assert_called_once_with("user@test.com", "pass")

        mock_client.logout.assert_called_once()


def test_imap_connection_logout_on_exception(account: AccountConfig):
    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        with pytest.raises(RuntimeError):
            with imap_connection(account) as _client:
                raise RuntimeError("boom")

        mock_client.logout.assert_called_once()


def test_imap_connection_swallows_logout_error(account: AccountConfig):
    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.logout.side_effect = OSError("already closed")
        mock_cls.return_value = mock_client

        with imap_connection(account) as _client:
            pass  # should not raise despite logout failure


async def test_run_imap_calls_operation(
    account: AccountConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def fake_op(client, folder):
        return f"listed {folder}"

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        result = await run_imap(account, fake_op, "INBOX")
        assert result == "listed INBOX"


async def test_run_imap_retries_on_connection_error(
    account: AccountConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")
    call_count = 0

    def flaky_op(client):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("dropped")
        return "success"

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        result = await run_imap(account, flaky_op)
        assert result == "success"
        assert call_count == 2


async def test_run_imap_does_not_retry_auth_error(
    account: AccountConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def auth_fail_op(client):
        raise Exception("LOGIN failed")

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        with pytest.raises(Exception, match="LOGIN failed"):
            await run_imap(account, auth_fail_op)


async def test_run_imap_timeout(
    account: AccountConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "1")

    def slow_op(client):
        import time
        time.sleep(5)

    with patch("mailbridge_mcp.imap_client.imapclient.IMAPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        with pytest.raises(asyncio.TimeoutError):
            await run_imap(account, slow_op)


def test_get_uidvalidity():
    mock_client = MagicMock()
    mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
    result = get_uidvalidity(mock_client, "INBOX")
    assert result == 12345
    mock_client.select_folder.assert_called_once_with("INBOX")

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig
from mailbridge_mcp.smtp_client import RateLimiter, send_email, validate_addresses


@pytest.fixture
def account() -> AccountConfig:
    return AccountConfig(
        id="test",
        label="Test",
        imap=ImapConfig(
            host="imap.test.com", port=993, tls=True,
            username="u", password="p",
        ),
        smtp=SmtpConfig(
            host="smtp.test.com", port=587, starttls=True,
            username="u", password="p",
        ),
        default_from="Test <test@test.com>",
    )


class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(max_per_minute=3)
        assert rl.check() is True
        assert rl.check() is True
        assert rl.check() is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_per_minute=2)
        assert rl.check() is True
        assert rl.check() is True
        assert rl.check() is False

    def test_unlimited_when_zero(self):
        rl = RateLimiter(max_per_minute=0)
        for _ in range(100):
            assert rl.check() is True

    def test_window_expires(self):
        rl = RateLimiter(max_per_minute=1)
        assert rl.check() is True
        assert rl.check() is False
        # Manually expire the oldest entry
        rl._timestamps[0] = time.monotonic() - 61
        assert rl.check() is True


class TestValidateAddresses:
    def test_valid_addresses(self):
        validate_addresses(["user@example.com", "other@test.org"])

    def test_invalid_address_raises(self):
        with pytest.raises(ValueError, match="not-an-email"):
            validate_addresses(["not-an-email"])

    def test_empty_list_ok(self):
        validate_addresses([])


async def test_send_email_builds_mime_and_sends(account: AccountConfig):
    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send") as mock_send:
        mock_send.return_value = ({}, "OK")
        result = await send_email(
            account=account,
            to=["recipient@test.com"],
            subject="Test Subject",
            body="Hello world",
            timeout=5,
        )
        assert result["status"] == "sent"
        assert "message_id" in result
        assert "@mailbridge-mcp>" in result["message_id"]
        mock_send.assert_called_once()


async def test_send_email_with_cc_bcc(account: AccountConfig):
    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send") as mock_send:
        mock_send.return_value = ({}, "OK")
        result = await send_email(
            account=account,
            to=["to@test.com"],
            subject="Test",
            body="Body",
            cc=["cc@test.com"],
            bcc=["bcc@test.com"],
            timeout=5,
        )
        assert result["status"] == "sent"
        # Verify all recipients were included
        call_kwargs = mock_send.call_args
        recipients = call_kwargs.kwargs.get("recipients", [])
        assert "to@test.com" in recipients
        assert "cc@test.com" in recipients
        assert "bcc@test.com" in recipients


async def test_send_email_with_reply_headers(account: AccountConfig):
    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send") as mock_send:
        mock_send.return_value = ({}, "OK")
        result = await send_email(
            account=account,
            to=["to@test.com"],
            subject="Re: Original",
            body="My reply",
            in_reply_to="<orig-123@example.com>",
            references="<orig-123@example.com>",
            timeout=5,
        )
        assert result["status"] == "sent"
        # Verify the MIME message was passed with headers
        call_args = mock_send.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("message")
        assert msg["In-Reply-To"] == "<orig-123@example.com>"
        assert msg["References"] == "<orig-123@example.com>"

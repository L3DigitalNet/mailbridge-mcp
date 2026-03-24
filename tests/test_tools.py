"""Tool-level integration tests — test tool functions with mocked IMAP/SMTP."""

from __future__ import annotations

import json
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig
from mailbridge_mcp.tools_read import (
    get_message,
    list_accounts,
    list_folders,
    list_messages,
    search_messages,
)
from mailbridge_mcp.tools_write import (
    delete_message,
    move_message,
    send_email_tool,
    set_flags,
)

# imapclient ENVELOPE is a namedtuple-like object
Envelope = namedtuple(
    "Envelope",
    ["date", "subject", "from_", "sender", "reply_to",
     "to", "cc", "bcc", "in_reply_to", "message_id"],
)


@pytest.fixture
def accounts() -> dict[str, AccountConfig]:
    acct = AccountConfig(
        id="test",
        label="Test Account",
        imap=ImapConfig(
            host="imap.test.com", port=993, tls=True,
            username="u@test.com", password="p",
        ),
        smtp=SmtpConfig(
            host="smtp.test.com", port=587, starttls=True,
            username="u@test.com", password="p",
        ),
        default_from="Test <u@test.com>",
    )
    return {"test": acct}


def _make_envelope(
    subject: str = "Test Subject",
    from_addr: str = "sender@test.com",
    date: str = "2026-03-24",
) -> Envelope:
    """Build a mock ENVELOPE for IMAP FETCH responses."""
    from_tuple = ((None, None, from_addr.split("@")[0], from_addr.split("@")[1]),)
    to_tuple = ((None, None, b"user", b"test.com"),)
    return Envelope(
        date=date,
        subject=subject.encode(),
        from_=from_tuple,
        sender=from_tuple,
        reply_to=None,
        to=to_tuple,
        cc=None,
        bcc=None,
        in_reply_to=None,
        message_id=b"<msg-123@test.com>",
    )


# --- Read tool tests ---


async def test_list_accounts_returns_configured(accounts: dict[str, AccountConfig]):
    result = json.loads(await list_accounts(accounts))
    assert len(result) == 1
    assert result[0]["id"] == "test"
    assert result[0]["label"] == "Test Account"
    assert result[0]["default_from"] == "Test <u@test.com>"


async def test_list_folders_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await list_folders(accounts, "nonexistent"))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_list_folders_returns_data(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def mock_op(client):
        # Simulate list_folders returning folder data
        return [
            {"name": "INBOX", "flags": ["\\HasNoChildren"], "delimiter": "/",
             "message_count": 10, "unread_count": 3},
        ]

    with patch("mailbridge_mcp.tools_read.run_imap", return_value=mock_op(None)):
        result = json.loads(await list_folders(accounts, "test"))
        assert isinstance(result, list)
        assert result[0]["name"] == "INBOX"


async def test_list_messages_pagination(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def mock_imap_op(account, op, *args, **kwargs):
        # Simulate the inner _op function's return value
        return {
            "summaries": [
                {"uid": 1, "subject": "Test", "from": "sender@test.com",
                 "to": "user@test.com", "date": "2026-03-24",
                 "size_kb": 1.5, "has_attachments": False,
                 "is_read": True, "is_flagged": False, "uidvalidity": 1},
            ],
            "total": 5,
            "uidvalidity": 1,
        }

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_imap_op):
        result = json.loads(await list_messages(
            accounts, "test", "INBOX", limit=1, offset=0,
            unread_only=False, sort_by="date_desc", response_format="json",
        ))
        assert result["total"] == 5
        assert result["has_more"] is True
        assert len(result["items"]) == 1


async def test_get_message_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await get_message(
        accounts, "nonexistent", "INBOX", 1, True, False, "json"
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_search_messages_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await search_messages(
        accounts, "nonexistent", "INBOX", "", None, None, None, None, None,
        None, None, 20, 0, "json",
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


# --- Write tool tests ---


async def test_send_email_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await send_email_tool(
        accounts, "nonexistent", ["to@test.com"], "Subj", "Body",
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_send_email_invalid_address(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SMTP_RATE_LIMIT", "100")
    # Reset the rate limiter
    from mailbridge_mcp import tools_write
    tools_write._rate_limiter._timestamps.clear()

    result = json.loads(await send_email_tool(
        accounts, "test", ["not-an-email"], "Subj", "Body",
    ))
    assert result["error"] == "INVALID_EMAIL_ADDRESS"


async def test_send_email_rate_limited(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SMTP_RATE_LIMIT", "1")
    from mailbridge_mcp import tools_write
    tools_write._rate_limiter = tools_write.RateLimiter(max_per_minute=1)

    # First send should work (mock the SMTP)
    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send", return_value=({}, "OK")):
        result1 = json.loads(await send_email_tool(
            accounts, "test", ["to@test.com"], "S1", "B1",
        ))
        assert result1["status"] == "sent"

    # Second send should be rate limited
    result2 = json.loads(await send_email_tool(
        accounts, "test", ["to@test.com"], "S2", "B2",
    ))
    assert result2["error"] == "SMTP_RATE_LIMITED"

    # Reset for other tests
    tools_write._rate_limiter = tools_write.RateLimiter(max_per_minute=10)


async def test_send_email_success(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    from mailbridge_mcp import tools_write
    tools_write._rate_limiter._timestamps.clear()

    with patch("mailbridge_mcp.smtp_client.aiosmtplib.send", return_value=({}, "OK")):
        result = json.loads(await send_email_tool(
            accounts, "test", ["to@test.com"], "Subject", "Body",
        ))
        assert result["status"] == "sent"
        assert "message_id" in result


async def test_move_message_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await move_message(
        accounts, "nonexistent", "INBOX", 1, "Archive",
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_delete_message_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await delete_message(
        accounts, "nonexistent", "INBOX", 1,
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_set_flags_unknown_account(accounts: dict[str, AccountConfig]):
    result = json.loads(await set_flags(
        accounts, "nonexistent", "INBOX", [1], True, None,
    ))
    assert result["error"] == "ACCOUNT_NOT_FOUND"


async def test_delete_message_moves_to_trash(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def mock_imap_op(account, op, *args, **kwargs):
        mock_client = MagicMock()
        mock_client.select_folder.return_value = {b"UIDVALIDITY": 1}
        mock_client.list_folders.return_value = [
            ([b"\\Trash"], "/", "Trash"),
            ([b"\\HasNoChildren"], "/", "INBOX"),
        ]
        return op(mock_client, *args, **kwargs)

    with patch("mailbridge_mcp.tools_write.run_imap", side_effect=mock_imap_op):
        result = json.loads(await delete_message(accounts, "test", "INBOX", 42))
        assert result["status"] == "trashed"
        assert result["uid"] == 42


async def test_set_flags_marks_read(
    accounts: dict[str, AccountConfig], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IMAP_TIMEOUT", "5")

    def mock_imap_op(account, op, *args, **kwargs):
        mock_client = MagicMock()
        return op(mock_client, *args, **kwargs)

    with patch("mailbridge_mcp.tools_write.run_imap", side_effect=mock_imap_op):
        result = json.loads(await set_flags(
            accounts, "test", "INBOX", [1, 2], mark_read=True, mark_flagged=None,
        ))
        assert result["status"] == "updated"
        assert result["uids"] == [1, 2]
        assert "\\Seen" in result["flags_set"]

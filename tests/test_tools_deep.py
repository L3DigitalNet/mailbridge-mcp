"""Deep tool tests — happy-path tests for tools_read and tools_write with full IMAP mock chains.

These tests exercise the inner _op() closures that do actual IMAP work,
using mock clients that simulate real imapclient return values.
"""

from __future__ import annotations

import json
from collections import namedtuple
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from mailbridge_mcp.config import AccountConfig, ImapConfig, SmtpConfig
from mailbridge_mcp.tools_read import get_message, get_thread, list_messages, search_messages
from mailbridge_mcp.tools_write import move_message, reply_tool, set_flags

Envelope = namedtuple(
    "Envelope",
    ["date", "subject", "from_", "sender", "reply_to",
     "to", "cc", "bcc", "in_reply_to", "message_id"],
)


@pytest.fixture
def accounts() -> dict[str, AccountConfig]:
    acct = AccountConfig(
        id="test",
        label="Test",
        imap=ImapConfig(host="imap.test.com", port=993, tls=True, username="u", password="p"),
        smtp=SmtpConfig(host="smtp.test.com", port=587, starttls=True, username="u", password="p"),
        default_from="Test <u@test.com>",
    )
    return {"test": acct}


def _make_mime_bytes(body_text: str = "Hello world", html: str | None = None) -> bytes:
    """Build raw email bytes suitable for IMAP FETCH BODY[] response."""
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(html, "html"))
    else:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body_text, "plain"))
    msg["From"] = "sender@test.com"
    msg["To"] = "recipient@test.com"
    msg["Subject"] = "Test Subject"
    msg["Message-ID"] = "<msg-001@test.com>"
    return msg.as_bytes()


def _make_envelope(**overrides) -> Envelope:
    defaults = {
        "date": "2026-03-24",
        "subject": b"Test Subject",
        "from_": ((None, None, b"sender", b"test.com"),),
        "sender": ((None, None, b"sender", b"test.com"),),
        "reply_to": None,
        "to": ((None, None, b"user", b"test.com"),),
        "cc": None,
        "bcc": None,
        "in_reply_to": None,
        "message_id": b"<msg-001@test.com>",
    }
    defaults.update(overrides)
    return Envelope(**defaults)


def _mock_imap_client(
    folder_data: dict | None = None,
    search_uids: list[int] | None = None,
) -> MagicMock:
    """Create a mock imapclient that returns realistic data."""
    client = MagicMock()
    client.select_folder.return_value = {b"UIDVALIDITY": 42}
    if search_uids is not None:
        client.search.return_value = search_uids
    if folder_data is not None:
        client.fetch.return_value = folder_data
    return client


# --- get_message tests ---


async def test_get_message_plain_text(accounts):
    env = _make_envelope()
    raw_body = _make_mime_bytes("Hello from the test")
    fetch_data = {
        101: {
            b"ENVELOPE": env,
            b"BODY[]": raw_body,
            b"FLAGS": (b"\\Seen",),
            b"RFC822.SIZE": len(raw_body),
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(folder_data=fetch_data)
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(
            await get_message(accounts, "test", "INBOX", 101, True, False, "json")
        )
    assert result["uid"] == 101
    assert "Hello from the test" in result["body"]
    assert result["body_truncated"] is False
    assert result["is_read"] is True
    assert result["uidvalidity"] == 42
    assert result["attachments"] == []


async def test_get_message_strips_html(accounts):
    env = _make_envelope()
    raw_body = _make_mime_bytes(
        body_text="Plain version",
        html="<p>HTML <b>version</b></p>",
    )
    fetch_data = {
        102: {
            b"ENVELOPE": env,
            b"BODY[]": raw_body,
            b"FLAGS": (),
            b"RFC822.SIZE": len(raw_body),
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(folder_data=fetch_data)
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(
            await get_message(accounts, "test", "INBOX", 102, True, False, "json")
        )
    # With prefer_plain=True, should get the plain text part (found first in walk)
    assert "Plain version" in result["body"]


async def test_get_message_not_found(accounts):
    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(folder_data={})  # UID 999 not in fetch data
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(
            await get_message(accounts, "test", "INBOX", 999, True, False, "json")
        )
    assert result["error"] == "IMAP_MESSAGE_NOT_FOUND"


async def test_get_message_includes_headers(accounts):
    env = _make_envelope()
    raw_body = _make_mime_bytes("Body text")
    fetch_data = {
        103: {
            b"ENVELOPE": env,
            b"BODY[]": raw_body,
            b"FLAGS": (),
            b"RFC822.SIZE": len(raw_body),
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(folder_data=fetch_data)
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(
            await get_message(accounts, "test", "INBOX", 103, True, True, "json")
        )
    assert "headers" in result
    assert "Message-ID" in result["headers"]


# --- search_messages tests ---


async def test_search_messages_with_query(accounts):
    env = _make_envelope()
    fetch_data = {
        201: {
            b"ENVELOPE": env,
            b"FLAGS": (b"\\Seen",),
            b"RFC822.SIZE": 1024,
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(search_uids=[201], folder_data=fetch_data)
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(await search_messages(
            accounts, "test", "INBOX", "hello", None, None, None,
            None, None, None, None, 20, 0, "json",
        ))
    assert result["total"] == 1
    assert result["items"][0]["uid"] == 201

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        md = await search_messages(
            accounts, "test", "INBOX", "hello", None, None, None,
            None, None, None, None, 20, 0, "markdown",
        )
    assert "201" in md  # UID appears in markdown table


async def test_search_messages_with_filters(accounts):
    """Verify IMAP criteria are built from filter parameters."""

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(search_uids=[], folder_data={})
        result = op(client)
        # Verify the search criteria were passed correctly
        criteria = client.search.call_args[0][0]
        assert "FROM" in criteria
        assert "alice@test.com" in criteria
        assert "UNSEEN" in criteria
        return result

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        await search_messages(
            accounts, "test", "INBOX", "",
            from_address="alice@test.com",
            to_address=None,
            subject_filter=None,
            since_date=None,
            before_date=None,
            is_unread=True,
            is_flagged=None,
            limit=20,
            offset=0,
            response_format="json",
        )


# --- list_messages tests ---


async def test_list_messages_markdown_format(accounts):
    env = _make_envelope()
    fetch_data = {
        301: {
            b"ENVELOPE": env,
            b"FLAGS": (),
            b"RFC822.SIZE": 2048,
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(search_uids=[301], folder_data=fetch_data)
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        md = await list_messages(
            accounts, "test", "INBOX", 20, 0, False, "date_desc", "markdown",
        )
    assert "| 301 |" in md
    assert "Showing" in md


# --- get_thread tests ---


async def test_get_thread_finds_related_messages(accounts):
    header_bytes = b"Message-ID: <thread-1@test.com>\r\nReferences: <parent@test.com>\r\n"
    env1 = _make_envelope(message_id=b"<thread-1@test.com>")
    env2 = _make_envelope(message_id=b"<parent@test.com>")

    async def mock_run_imap(account, op, *args, **kwargs):
        client = MagicMock()
        client.select_folder.return_value = {b"UIDVALIDITY": 42}
        # First fetch: get threading headers for target UID
        # Subsequent fetches: get envelopes for thread UIDs
        fetch_call_count = [0]

        def mock_fetch(uids, fields):
            fetch_call_count[0] += 1
            if fetch_call_count[0] == 1:
                # Threading headers fetch
                return {401: {b"BODY[HEADER.FIELDS (MESSAGE-ID REFERENCES)]": header_bytes}}
            # Envelope fetch for thread messages
            return {
                401: {b"ENVELOPE": env1, b"FLAGS": (), b"RFC822.SIZE": 1024},
                402: {b"ENVELOPE": env2, b"FLAGS": (), b"RFC822.SIZE": 1024},
            }

        client.fetch.side_effect = mock_fetch
        # Search for messages with matching Message-ID or References
        client.search.return_value = [401, 402]
        return op(client)

    with patch("mailbridge_mcp.tools_read.run_imap", side_effect=mock_run_imap):
        result = json.loads(await get_thread(
            accounts, "test", "INBOX", 401, 20, 0, "json",
        ))
    assert result["total"] >= 1
    assert any(item["uid"] in (401, 402) for item in result["items"])


# --- move_message tests ---


async def test_move_message_copies_and_flags(accounts):
    async def mock_run_imap(account, op, *args, **kwargs):
        client = MagicMock()
        client.select_folder.return_value = {b"UIDVALIDITY": 42}
        result = op(client)
        # Verify COPY was called followed by DELETED flag
        client.copy.assert_called_once_with([501], "Archive")
        client.add_flags.assert_called_once()
        return result

    with patch("mailbridge_mcp.tools_write.run_imap", side_effect=mock_run_imap):
        result = json.loads(await move_message(
            accounts, "test", "INBOX", 501, "Archive",
        ))
    assert result["status"] == "moved"
    assert result["uid"] == 501
    assert result["destination"] == "Archive"


# --- reply_tool tests ---


async def test_reply_tool_sends_with_threading_headers(accounts):
    env = _make_envelope(
        subject=b"Original Subject",
        message_id=b"<orig-42@test.com>",
    )
    raw_body = _make_mime_bytes("Original body text")
    fetch_data = {
        601: {
            b"ENVELOPE": env,
            b"BODY[]": raw_body,
        }
    }

    async def mock_run_imap(account, op, *args, **kwargs):
        client = _mock_imap_client(folder_data=fetch_data)
        return op(client)

    with (
        patch("mailbridge_mcp.tools_write.run_imap", side_effect=mock_run_imap),
        patch("mailbridge_mcp.tools_write.send_email") as mock_send,
    ):
        mock_send.return_value = {"status": "sent", "message_id": "<reply@test.com>"}
        # Reset rate limiter for this test
        from mailbridge_mcp import tools_write
        tools_write._rate_limiter._timestamps.clear()

        result = json.loads(await reply_tool(
            accounts, "test", "INBOX", 601, "My reply",
            reply_all=False, include_original=True,
        ))

    assert result["status"] == "sent"
    # Verify send_email was called with threading headers
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["in_reply_to"] == "<orig-42@test.com>"
    assert "<orig-42@test.com>" in call_kwargs["references"]
    assert call_kwargs["subject"] == "Re: Original Subject"
    # Original body should be quoted in reply
    assert "> Original body text" in call_kwargs["body"]


# --- set_flags edge cases ---


async def test_set_flags_unflag_and_unread(accounts):
    """Test removing flags (mark_read=False, mark_flagged=False)."""

    async def mock_run_imap(account, op, *args, **kwargs):
        client = MagicMock()
        result = op(client)
        # Verify remove_flags was called for both
        import imapclient
        client.remove_flags.assert_any_call([701], [imapclient.SEEN])
        client.remove_flags.assert_any_call([701], [imapclient.FLAGGED])
        return result

    with patch("mailbridge_mcp.tools_write.run_imap", side_effect=mock_run_imap):
        result = json.loads(await set_flags(
            accounts, "test", "INBOX", [701],
            mark_read=False, mark_flagged=False,
        ))
    assert result["status"] == "updated"

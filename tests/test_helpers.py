"""Tests for internal helper functions in tools_read and tools_write."""

from __future__ import annotations

from unittest.mock import MagicMock

from mailbridge_mcp.tools_read import _decode_header, _envelope_date, _parse_addresses
from mailbridge_mcp.tools_write import _find_trash_folder


class TestDecodeHeader:
    def test_plain_string(self):
        assert _decode_header("Hello World") == "Hello World"

    def test_bytes_input(self):
        assert _decode_header(b"Hello") == "Hello"

    def test_none_returns_empty(self):
        assert _decode_header(None) == ""

    def test_encoded_header(self):
        # RFC 2047 encoded header
        result = _decode_header("=?utf-8?b?SGVsbG8=?=")
        assert result == "Hello"


class TestParseAddresses:
    def test_single_address(self):
        # ENVELOPE address format: (name, route, mailbox, host)
        addr = [(None, None, b"user", b"test.com")]
        result = _parse_addresses(addr)
        assert "user@test.com" in result

    def test_address_with_name(self):
        addr = [(b"John Doe", None, b"john", b"test.com")]
        result = _parse_addresses(addr)
        assert "John Doe" in result
        assert "john@test.com" in result

    def test_none_returns_empty(self):
        assert _parse_addresses(None) == ""

    def test_empty_list_returns_empty(self):
        assert _parse_addresses([]) == ""

    def test_multiple_addresses(self):
        addrs = [
            (None, None, b"alice", b"test.com"),
            (None, None, b"bob", b"test.com"),
        ]
        result = _parse_addresses(addrs)
        assert "alice@test.com" in result
        assert "bob@test.com" in result


class TestEnvelopeDate:
    def test_none_envelope(self):
        assert _envelope_date(None) == ""

    def test_string_date(self):
        env = MagicMock()
        env.date = "2026-03-24"
        assert _envelope_date(env) == "2026-03-24"

    def test_datetime_date(self):
        from datetime import datetime

        env = MagicMock()
        env.date = datetime(2026, 3, 24, 12, 0, 0)
        result = _envelope_date(env)
        assert "2026-03-24" in result

    def test_no_date_attr(self):
        env = MagicMock()
        env.date = None
        assert _envelope_date(env) == ""


class TestFindTrashFolder:
    def test_finds_trash_flag(self):
        mock_client = MagicMock()
        mock_client.list_folders.return_value = [
            ([b"\\HasNoChildren"], "/", "INBOX"),
            ([b"\\Trash"], "/", "Deleted Items"),
        ]
        assert _find_trash_folder(mock_client) == "Deleted Items"

    def test_falls_back_to_common_name(self):
        mock_client = MagicMock()
        mock_client.list_folders.return_value = [
            ([b"\\HasNoChildren"], "/", "INBOX"),
            ([b"\\HasNoChildren"], "/", "Trash"),
        ]
        assert _find_trash_folder(mock_client) == "Trash"

    def test_falls_back_to_inbox_trash(self):
        mock_client = MagicMock()
        mock_client.list_folders.return_value = [
            ([b"\\HasNoChildren"], "/", "INBOX"),
            ([b"\\HasNoChildren"], "/", "INBOX.Trash"),
        ]
        assert _find_trash_folder(mock_client) == "INBOX.Trash"

    def test_no_match_returns_trash(self):
        mock_client = MagicMock()
        mock_client.list_folders.return_value = [
            ([b"\\HasNoChildren"], "/", "INBOX"),
            ([b"\\HasNoChildren"], "/", "Archive"),
        ]
        assert _find_trash_folder(mock_client) == "Trash"

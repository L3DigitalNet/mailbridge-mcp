"""Tests for formatters — HTML stripping, body truncation, markdown, pagination, errors."""

from __future__ import annotations

import json

from mailbridge_mcp.formatters import (
    BODY_TRUNCATION_LIMIT,
    error_response,
    format_json,
    format_message_summary_markdown,
    pagination_envelope,
    strip_html,
    truncate_body,
)


class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_plain_text_passthrough(self):
        assert strip_html("no tags here") == "no tags here"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_nested_tags(self):
        result = strip_html("<div><ul><li>item</li></ul></div>")
        assert "item" in result
        assert "<" not in result


class TestTruncateBody:
    def test_short_body_not_truncated(self):
        body, truncated = truncate_body("short")
        assert body == "short"
        assert truncated is False

    def test_exact_limit_not_truncated(self):
        body = "x" * BODY_TRUNCATION_LIMIT
        result, truncated = truncate_body(body)
        assert len(result) == BODY_TRUNCATION_LIMIT
        assert truncated is False

    def test_over_limit_truncated(self):
        body = "x" * (BODY_TRUNCATION_LIMIT + 100)
        result, truncated = truncate_body(body)
        assert len(result) == BODY_TRUNCATION_LIMIT
        assert truncated is True


class TestFormatJson:
    def test_serializes_dict(self):
        result = format_json({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_serializes_list(self):
        result = format_json([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_handles_non_serializable_with_default_str(self):
        from datetime import datetime

        result = format_json({"date": datetime(2026, 3, 24)})
        parsed = json.loads(result)
        assert "2026" in parsed["date"]


class TestMessageSummaryMarkdown:
    def test_empty_list(self):
        result = format_message_summary_markdown([])
        assert "No messages found" in result

    def test_single_message(self):
        messages = [
            {
                "uid": 1,
                "from": "sender@test.com",
                "subject": "Test Subject",
                "date": "2026-03-24",
                "is_read": True,
                "is_flagged": False,
            }
        ]
        result = format_message_summary_markdown(messages)
        assert "| 1 |" in result
        assert "sender@test.com" in result
        assert "Test Subject" in result
        assert "yes" in result  # is_read

    def test_long_subject_truncated(self):
        messages = [
            {
                "uid": 1,
                "from": "x@x.com",
                "subject": "A" * 100,
                "date": "",
                "is_read": False,
                "is_flagged": False,
            }
        ]
        result = format_message_summary_markdown(messages)
        # Subject truncated to 60 chars
        assert "A" * 60 in result
        assert "A" * 61 not in result

    def test_no_subject_shows_placeholder(self):
        messages = [
            {"uid": 1, "from": "", "subject": None, "date": "", "is_read": False, "is_flagged": False}
        ]
        result = format_message_summary_markdown(messages)
        assert "(no subject)" in result


class TestPaginationEnvelope:
    def test_has_more(self):
        result = pagination_envelope(items=["a", "b"], total=10, offset=0, limit=2)
        assert result["total"] == 10
        assert result["has_more"] is True
        assert result["next_offset"] == 2

    def test_no_more(self):
        result = pagination_envelope(items=["a", "b"], total=2, offset=0, limit=5)
        assert result["has_more"] is False
        assert result["next_offset"] is None

    def test_mid_page(self):
        result = pagination_envelope(items=["c"], total=10, offset=5, limit=1)
        assert result["offset"] == 5
        assert result["has_more"] is True
        assert result["next_offset"] == 6


class TestErrorResponse:
    def test_builds_json(self):
        result = error_response("IMAP_AUTH_FAILED", "Bad creds", "personal")
        parsed = json.loads(result)
        assert parsed["error"] == "IMAP_AUTH_FAILED"
        assert parsed["message"] == "Bad creds"
        assert parsed["account_id"] == "personal"

    def test_empty_account_id(self):
        result = error_response("UNKNOWN", "oops")
        parsed = json.loads(result)
        assert parsed["account_id"] == ""

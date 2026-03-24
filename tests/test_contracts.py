"""Contract tests — verify MCP tool schemas, annotations, and error response shapes."""

from __future__ import annotations

import asyncio
import json

import pytest

from mailbridge_mcp.server import mcp


@pytest.fixture
async def tools():
    """Get all registered MCP tools."""
    return await mcp.list_tools()


# Expected annotations per design spec section 8
EXPECTED_ANNOTATIONS = {
    "imap_list_accounts": {"readOnlyHint": True},
    "imap_list_folders": {"readOnlyHint": True},
    "imap_list_messages": {"readOnlyHint": True},
    "imap_get_message": {"readOnlyHint": True},
    "imap_search_messages": {"readOnlyHint": True},
    "imap_get_thread": {"readOnlyHint": True},
    "imap_send_email": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "imap_reply": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "imap_move_message": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    "imap_delete_message": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    "imap_set_flags": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
}


def test_all_11_tools_registered(tools):
    names = {t.name for t in tools}
    assert len(names) == 11, f"Expected 11 tools, got {len(names)}: {names}"
    for expected_name in EXPECTED_ANNOTATIONS:
        assert expected_name in names, f"Missing tool: {expected_name}"


@pytest.mark.parametrize("tool_name,expected", list(EXPECTED_ANNOTATIONS.items()))
def test_tool_annotations(tools, tool_name: str, expected: dict):
    tool = next((t for t in tools if t.name == tool_name), None)
    assert tool is not None, f"Tool {tool_name} not found"
    assert tool.annotations is not None, f"Tool {tool_name} has no annotations"
    for key, value in expected.items():
        actual = getattr(tool.annotations, key, None)
        assert actual == value, (
            f"Tool {tool_name}: {key} expected {value}, got {actual}"
        )


def test_read_tools_have_account_id_param(tools):
    """All tools except imap_list_accounts should require account_id."""
    for tool in tools:
        if tool.name == "imap_list_accounts":
            continue
        schema = tool.parameters
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        assert "account_id" in props, (
            f"Tool {tool.name} missing account_id parameter"
        )


def test_error_response_shape():
    """Verify error responses follow the structured {error, message, account_id} shape."""
    from mailbridge_mcp.formatters import error_response

    result = json.loads(error_response("TEST_ERROR", "test message", "acct1"))
    assert set(result.keys()) == {"error", "message", "account_id"}
    assert isinstance(result["error"], str)
    assert isinstance(result["message"], str)
    assert isinstance(result["account_id"], str)

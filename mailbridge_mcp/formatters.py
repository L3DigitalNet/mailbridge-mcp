from __future__ import annotations

import json
from typing import Any

import nh3

BODY_TRUNCATION_LIMIT = 50_000


def strip_html(html: str) -> str:
    """Strip all HTML tags, returning plain text."""
    return nh3.clean(html, tags=set())


def truncate_body(body: str) -> tuple[str, bool]:
    """Truncate body at BODY_TRUNCATION_LIMIT chars. Returns (body, was_truncated)."""
    if len(body) <= BODY_TRUNCATION_LIMIT:
        return body, False
    return body[:BODY_TRUNCATION_LIMIT], True


def format_json(data: Any) -> str:
    """Serialize data as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def format_message_summary_markdown(messages: list[dict[str, Any]]) -> str:
    """Format message summaries as a markdown table."""
    if not messages:
        return "*No messages found.*"
    lines = [
        "| UID | From | Subject | Date | Read | Flagged |",
        "|-----|------|---------|------|------|---------|",
    ]
    for m in messages:
        read = "yes" if m.get("is_read") else "no"
        flagged = "yes" if m.get("is_flagged") else "no"
        subj = (m.get("subject") or "(no subject)")[:60]
        from_addr = (m.get("from") or "")[:40]
        lines.append(
            f"| {m['uid']} | {from_addr} | {subj} | {m.get('date', '')} | {read} | {flagged} |"
        )
    return "\n".join(lines)


def pagination_envelope(
    items: list[Any], total: int, offset: int, limit: int
) -> dict[str, Any]:
    """Wrap a list of items with pagination metadata."""
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "has_more": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def error_response(code: str, message: str, account_id: str = "") -> str:
    """Build a structured error JSON string."""
    return json.dumps({
        "error": code,
        "message": message,
        "account_id": account_id,
    })

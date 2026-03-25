"""Read-only IMAP tool implementations.

All functions return JSON strings. They accept pre-resolved account maps
and raw parameter values (not Pydantic models) so they can be called
directly from the server tool wrappers.
"""

from __future__ import annotations

import email
import email.header
import email.utils
from datetime import datetime
from typing import Any

import structlog

from mailbridge_mcp.config import AccountConfig
from mailbridge_mcp.formatters import (
    error_response,
    format_json,
    format_message_summary_markdown,
    pagination_envelope,
    strip_html,
    truncate_body,
)
from mailbridge_mcp.imap_client import get_uidvalidity, run_imap

log = structlog.get_logger()


def _get_account(accounts: dict[str, AccountConfig], account_id: str) -> AccountConfig | str:
    """Return the account or an error JSON string."""
    if account_id not in accounts:
        return error_response("ACCOUNT_NOT_FOUND", f"Unknown account: {account_id}", account_id)
    return accounts[account_id]


def _decode_header(raw: Any) -> str:
    """Decode an email header value into a plain string."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    parts = email.header.decode_header(str(raw))
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def _parse_addresses(raw: Any) -> str:
    """Extract a displayable address string from an ENVELOPE address tuple."""
    if not raw:
        return ""
    addrs = []
    for addr in raw:
        # ENVELOPE address tuple: (name, route, mailbox, host)
        if isinstance(addr, (list, tuple)) and len(addr) >= 4:
            name = _decode_header(addr[0]) if addr[0] else ""
            raw_mbox = addr[2] or b""
            mailbox = (
                raw_mbox.decode("utf-8", errors="replace")
                if isinstance(raw_mbox, bytes)
                else str(raw_mbox)
            )
            raw_host = addr[3] or b""
            host = (
                raw_host.decode("utf-8", errors="replace")
                if isinstance(raw_host, bytes)
                else str(raw_host)
            )
            email_addr = f"{mailbox}@{host}" if mailbox and host else ""
            if name:
                addrs.append(f"{name} <{email_addr}>")
            else:
                addrs.append(email_addr)
        else:
            addrs.append(str(addr))
    return ", ".join(addrs)


def _envelope_date(env: Any) -> str:
    """Extract a date string from an ENVELOPE."""
    if not env or not env.date:
        return ""
    if isinstance(env.date, datetime):
        return env.date.isoformat()
    return str(env.date)


def _build_summary(uid: int, data: dict[str, Any], uidvalidity: int) -> dict[str, Any]:
    """Build a message summary dict from FETCH response data."""
    env = data.get(b"ENVELOPE")  # type: ignore[call-overload]  # imapclient uses bytes keys
    flags = data.get(b"FLAGS", ())  # type: ignore[call-overload]
    size = data.get(b"RFC822.SIZE", 0)  # type: ignore[call-overload]

    flag_strs = [
        f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f) for f in flags
    ]

    return {
        "uid": uid,
        "subject": _decode_header(env.subject) if env else "",
        "from": _parse_addresses(env.from_) if env else "",
        "to": _parse_addresses(env.to) if env else "",
        "date": _envelope_date(env),
        "size_kb": round(size / 1024, 1) if size else 0,
        "has_attachments": False,  # determined during full fetch, not summary
        "is_read": "\\Seen" in flag_strs,
        "is_flagged": "\\Flagged" in flag_strs,
        "uidvalidity": uidvalidity,
    }


# --- Tool implementations ---


async def list_accounts(accounts: dict[str, AccountConfig]) -> str:
    return format_json(
        [{"id": a.id, "label": a.label, "default_from": a.default_from} for a in accounts.values()]
    )


async def list_folders(accounts: dict[str, AccountConfig], account_id: str) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:

        def _op(client: Any) -> list[dict[str, Any]]:
            folders = client.list_folders()
            result = []
            for flags, delimiter, name in folders:
                flag_strs = [
                    f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f)
                    for f in flags
                ]
                # Skip non-selectable folders (e.g. dovecot metadata, \Noselect)
                if "\\Noselect" in flag_strs or "\\NonExistent" in flag_strs:
                    continue
                try:
                    status = client.folder_status(name, ["MESSAGES", "UNSEEN"])
                    msg_count = status.get(b"MESSAGES", 0)
                    unread_count = status.get(b"UNSEEN", 0)
                except Exception:
                    msg_count = 0
                    unread_count = 0
                result.append(
                    {
                        "name": name,
                        "flags": flag_strs,
                        "delimiter": delimiter,
                        "message_count": msg_count,
                        "unread_count": unread_count,
                    }
                )
            return result

        data = await run_imap(acct, _op)
        return format_json(data)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)


async def list_messages(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    limit: int,
    offset: int,
    unread_only: bool,
    sort_by: str,
    response_format: str,
) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:

        def _op(client: Any) -> dict[str, Any]:
            uidvalidity = get_uidvalidity(client, folder)
            criteria: list[Any] = ["UNSEEN"] if unread_only else ["ALL"]
            uids = client.search(criteria)

            # Sort
            if sort_by == "date_asc":
                uids = sorted(uids)
            else:
                uids = sorted(uids, reverse=True)  # date_desc is default

            total = len(uids)
            page_uids = uids[offset : offset + limit]

            if not page_uids:
                return {"summaries": [], "total": total, "uidvalidity": uidvalidity}

            fetch_data = client.fetch(page_uids, ["ENVELOPE", "FLAGS", "RFC822.SIZE"])
            summaries = [
                _build_summary(uid, fetch_data[uid], uidvalidity)
                for uid in page_uids
                if uid in fetch_data
            ]
            return {"summaries": summaries, "total": total, "uidvalidity": uidvalidity}

        result = await run_imap(acct, _op)
        envelope = pagination_envelope(result["summaries"], result["total"], offset, limit)

        if response_format == "markdown":
            md = format_message_summary_markdown(result["summaries"])
            shown = len(result["summaries"])
            md += f"\n\n*Showing {offset + 1}-{offset + shown} of {result['total']}*"
            return md
        return format_json(envelope)
    except Exception as e:
        if "folder" in str(e).lower() or "mailbox" in str(e).lower():
            return error_response("IMAP_FOLDER_NOT_FOUND", str(e), account_id)
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)


async def get_message(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uid: int,
    prefer_plain: bool,
    include_headers: bool,
    response_format: str,
) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:

        def _op(client: Any) -> dict[str, Any] | str:
            uidvalidity = get_uidvalidity(client, folder)
            fetch_data = client.fetch([uid], ["ENVELOPE", "BODY[]", "FLAGS", "RFC822.SIZE"])
            if uid not in fetch_data:
                return error_response(
                    "IMAP_MESSAGE_NOT_FOUND", f"UID {uid} not found in {folder}", account_id
                )

            data = fetch_data[uid]
            env = data.get(b"ENVELOPE")
            raw_body = data.get(b"BODY[]", b"")
            flags = data.get(b"FLAGS", ())
            size = data.get(b"RFC822.SIZE", 0)

            # Parse the email message
            msg = email.message_from_bytes(
                raw_body if isinstance(raw_body, bytes) else raw_body.encode()
            )

            # Extract body
            body = ""
            attachments: list[dict[str, Any]] = []
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in disposition:
                    attachments.append(
                        {
                            "filename": part.get_filename() or "unknown",
                            "content_type": content_type,
                            "size_kb": round(len(part.get_payload(decode=True) or b"") / 1024, 1),
                        }
                    )
                    continue

                if content_type == "text/plain" and not body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                elif content_type == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        body = strip_html(html) if prefer_plain else html

            body, body_truncated = truncate_body(body)
            flag_strs = [
                f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f)
                for f in flags
            ]

            result: dict[str, Any] = {
                "uid": uid,
                "subject": _decode_header(env.subject) if env else "",
                "from": _parse_addresses(env.from_) if env else "",
                "to": _parse_addresses(env.to) if env else "",
                "cc": _parse_addresses(env.cc) if env and env.cc else "",
                "bcc": _parse_addresses(env.bcc) if env and env.bcc else "",
                "date": _envelope_date(env),
                "body": body,
                "body_truncated": body_truncated,
                "attachments": attachments,
                "size_kb": round(size / 1024, 1) if size else 0,
                "is_read": "\\Seen" in flag_strs,
                "is_flagged": "\\Flagged" in flag_strs,
                "uidvalidity": uidvalidity,
            }
            if include_headers:
                result["headers"] = dict(msg.items())
            return result

        result = await run_imap(acct, _op)
        if isinstance(result, str):
            return result  # error response
        return format_json(result)
    except Exception as e:
        return error_response("BODY_FETCH_FAILED", str(e), account_id)


async def search_messages(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    query: str,
    from_address: str | None,
    to_address: str | None,
    subject_filter: str | None,
    since_date: str | None,
    before_date: str | None,
    is_unread: bool | None,
    is_flagged: bool | None,
    limit: int,
    offset: int,
    response_format: str,
) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:

        def _op(client: Any) -> dict[str, Any]:
            uidvalidity = get_uidvalidity(client, folder)

            # Build IMAP SEARCH criteria
            criteria: list[Any] = []
            if query:
                criteria.extend(["TEXT", query])
            if from_address:
                criteria.extend(["FROM", from_address])
            if to_address:
                criteria.extend(["TO", to_address])
            if subject_filter:
                criteria.extend(["SUBJECT", subject_filter])
            if since_date:
                criteria.extend(["SINCE", since_date])
            if before_date:
                criteria.extend(["BEFORE", before_date])
            if is_unread is True:
                criteria.append("UNSEEN")
            elif is_unread is False:
                criteria.append("SEEN")
            if is_flagged is True:
                criteria.append("FLAGGED")
            elif is_flagged is False:
                criteria.append("UNFLAGGED")
            if not criteria:
                criteria = ["ALL"]

            uids = client.search(criteria)
            uids = sorted(uids, reverse=True)  # newest first
            total = len(uids)
            page_uids = uids[offset : offset + limit]

            if not page_uids:
                return {"summaries": [], "total": total, "uidvalidity": uidvalidity}

            fetch_data = client.fetch(page_uids, ["ENVELOPE", "FLAGS", "RFC822.SIZE"])
            summaries = [
                _build_summary(uid, fetch_data[uid], uidvalidity)
                for uid in page_uids
                if uid in fetch_data
            ]
            return {"summaries": summaries, "total": total, "uidvalidity": uidvalidity}

        result = await run_imap(acct, _op)
        envelope = pagination_envelope(result["summaries"], result["total"], offset, limit)

        if response_format == "markdown":
            md = format_message_summary_markdown(result["summaries"])
            shown = len(result["summaries"])
            md += f"\n\n*Showing {offset + 1}-{offset + shown} of {result['total']}*"
            return md
        return format_json(envelope)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)


async def get_thread(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uid: int,
    limit: int,
    offset: int,
    response_format: str,
) -> str:
    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct
    try:

        def _op(client: Any) -> dict[str, Any]:
            uidvalidity = get_uidvalidity(client, folder)

            # Fetch the target message's threading headers
            target_data = client.fetch([uid], ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES)]"])
            if uid not in target_data:
                return {"error": "IMAP_MESSAGE_NOT_FOUND", "uid": uid}

            raw_headers = target_data[uid].get(b"BODY[HEADER.FIELDS (MESSAGE-ID REFERENCES)]", b"")
            if isinstance(raw_headers, bytes):
                raw_headers = raw_headers.decode("utf-8", errors="replace")

            # Parse Message-ID and References from headers
            msg = email.message_from_string(raw_headers)
            message_id = msg.get("Message-ID", "").strip()
            references = msg.get("References", "").strip()

            # Collect all message-ids from the thread
            thread_ids: set[str] = set()
            if message_id:
                thread_ids.add(message_id)
            if references:
                for ref in references.split():
                    ref = ref.strip()
                    if ref:
                        thread_ids.add(ref)

            if not thread_ids:
                # No threading info; return just this message
                fetch_data = client.fetch([uid], ["ENVELOPE", "FLAGS", "RFC822.SIZE"])
                return {
                    "summaries": [_build_summary(uid, fetch_data[uid], uidvalidity)]
                    if uid in fetch_data
                    else [],
                    "total": 1,
                    "uidvalidity": uidvalidity,
                }

            # Search for all messages in the thread (within this folder)
            all_thread_uids: set[int] = set()
            for tid in thread_ids:
                found = client.search(["HEADER", "Message-ID", tid])
                all_thread_uids.update(found)
                found = client.search(["HEADER", "References", tid])
                all_thread_uids.update(found)

            # Sort by UID (proxy for date order), apply pagination
            sorted_uids = sorted(all_thread_uids)
            total = len(sorted_uids)
            page_uids = sorted_uids[offset : offset + limit]

            if not page_uids:
                return {"summaries": [], "total": total, "uidvalidity": uidvalidity}

            fetch_data = client.fetch(page_uids, ["ENVELOPE", "FLAGS", "RFC822.SIZE"])
            summaries = [
                _build_summary(u, fetch_data[u], uidvalidity) for u in page_uids if u in fetch_data
            ]
            return {"summaries": summaries, "total": total, "uidvalidity": uidvalidity}

        result = await run_imap(acct, _op)
        if "error" in result:
            return error_response(result["error"], f"UID {uid} not found", account_id)

        envelope = pagination_envelope(result["summaries"], result["total"], offset, limit)

        if response_format == "markdown":
            md = format_message_summary_markdown(result["summaries"])
            shown = len(result["summaries"])
            md += f"\n\n*Thread: {offset + 1}-{offset + shown} of {result['total']}*"
            return md
        return format_json(envelope)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)

"""Write IMAP/SMTP tool implementations.

All functions return JSON strings. They accept pre-resolved account maps
and raw parameter values so they can be called directly from server tool wrappers.
"""

from __future__ import annotations

import email
import email.header
import os
from typing import Any

import imapclient
import structlog

from mailbridge_mcp.config import AccountConfig
from mailbridge_mcp.formatters import error_response, format_json
from mailbridge_mcp.imap_client import get_uidvalidity, run_imap
from mailbridge_mcp.smtp_client import RateLimiter, send_email, validate_addresses
from mailbridge_mcp.tools_read import _decode_header, _parse_addresses

log = structlog.get_logger()

# Shared rate limiter for all SMTP sends (send + reply)
_rate_limiter = RateLimiter(
    max_per_minute=int(os.getenv("SMTP_RATE_LIMIT", "10"))
)

TRASH_FOLDER_NAMES = ["Trash", "Deleted Items", "INBOX.Trash", "Deleted"]


def _find_trash_folder(client: Any) -> str:
    """Auto-detect Trash folder: check \\Trash flag first, then common names."""
    folders = client.list_folders()
    # Check for \Trash special-use flag
    for flags, _delimiter, name in folders:
        flag_strs = [
            f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f)
            for f in flags
        ]
        if "\\Trash" in flag_strs:
            return str(name)

    # Fall back to common names
    folder_names = [name for _, _, name in folders]
    for candidate in TRASH_FOLDER_NAMES:
        if candidate in folder_names:
            return candidate

    return "Trash"  # last resort


async def send_email_tool(
    accounts: dict[str, AccountConfig],
    account_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
) -> str:
    from mailbridge_mcp.tools_read import _get_account

    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct

    if not _rate_limiter.check():
        return error_response(
            "SMTP_RATE_LIMITED",
            "Send rate exceeded, retry after cooldown",
            account_id,
        )

    try:
        validate_addresses(to + (cc or []) + (bcc or []))
    except ValueError as e:
        return error_response("INVALID_EMAIL_ADDRESS", str(e), account_id)

    timeout = int(os.getenv("SMTP_TIMEOUT", "30"))
    try:
        result = await send_email(
            acct, to, subject, body, cc, bcc, reply_to, timeout=timeout
        )
        return format_json(result)
    except Exception as e:
        return error_response("SMTP_SEND_FAILED", str(e), account_id)


async def reply_tool(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uid: int,
    body: str,
    reply_all: bool = False,
    include_original: bool = True,
) -> str:
    from mailbridge_mcp.tools_read import _get_account

    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct

    if not _rate_limiter.check():
        return error_response(
            "SMTP_RATE_LIMITED",
            "Send rate exceeded, retry after cooldown",
            account_id,
        )

    try:
        # Fetch original message for threading headers
        def _fetch_original(client: Any) -> dict[str, Any] | None:
            get_uidvalidity(client, folder)
            fetch_data = client.fetch(
                [uid],
                ["ENVELOPE", "BODY[]"],
            )
            if uid not in fetch_data:
                return None

            data = fetch_data[uid]
            env = data.get(b"ENVELOPE")
            raw_body = data.get(b"BODY[]", b"")

            # Parse threading headers
            message_id = ""
            references = ""
            if env and hasattr(env, "message_id"):
                mid = env.message_id
                if isinstance(mid, bytes):
                    message_id = mid.decode("utf-8", errors="replace")
                elif mid:
                    message_id = str(mid)

            # Parse References from raw body
            msg = email.message_from_bytes(
                raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
            )
            references = msg.get("References", "")

            # Build new References: old References + original Message-ID
            new_references = references
            if message_id:
                new_references = f"{references} {message_id}".strip()

            # Build subject
            orig_subject = _decode_header(env.subject) if env else ""
            if not orig_subject.lower().startswith("re:"):
                orig_subject = f"Re: {orig_subject}"

            # Determine recipients
            to_addrs: list[str] = []
            if env and env.reply_to:
                to_addrs = [_parse_addresses(env.reply_to)]
            elif env and env.from_:
                to_addrs = [_parse_addresses(env.from_)]

            cc_addrs: list[str] = []
            if reply_all and env:
                if env.to:
                    cc_addrs.append(_parse_addresses(env.to))
                if env.cc:
                    cc_addrs.append(_parse_addresses(env.cc))

            # Build reply body
            original_text = ""
            if include_original:
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            charset = part.get_content_charset() or "utf-8"
                            original_text = payload.decode(charset, errors="replace")
                            break

            return {
                "to": to_addrs,
                "cc": cc_addrs if reply_all else [],
                "subject": orig_subject,
                "in_reply_to": message_id,
                "references": new_references,
                "original_text": original_text,
            }

        original = await run_imap(acct, _fetch_original)
        if original is None:
            return error_response(
                "IMAP_MESSAGE_NOT_FOUND",
                f"UID {uid} not found in {folder}",
                account_id,
            )

        # Compose reply body
        reply_body = body
        if include_original and original["original_text"]:
            quoted = "\n".join(
                f"> {line}" for line in original["original_text"].splitlines()
            )
            reply_body = f"{body}\n\n{quoted}"

        timeout = int(os.getenv("SMTP_TIMEOUT", "30"))
        result = await send_email(
            acct,
            to=original["to"],
            subject=original["subject"],
            body=reply_body,
            cc=original["cc"] if original["cc"] else None,
            in_reply_to=original["in_reply_to"],
            references=original["references"],
            timeout=timeout,
        )
        return format_json(result)

    except Exception as e:
        return error_response("SMTP_SEND_FAILED", str(e), account_id)


async def move_message(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uid: int,
    destination_folder: str,
) -> str:
    from mailbridge_mcp.tools_read import _get_account

    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct

    try:

        def _op(client: Any) -> dict[str, Any]:
            get_uidvalidity(client, folder)
            client.select_folder(folder)
            client.copy([uid], destination_folder)
            client.add_flags([uid], [imapclient.DELETED])
            return {
                "status": "moved",
                "uid": uid,
                "destination": destination_folder,
            }

        result = await run_imap(acct, _op)
        return format_json(result)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)


async def delete_message(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uid: int,
) -> str:
    """Move message to Trash. Never calls EXPUNGE."""
    from mailbridge_mcp.tools_read import _get_account

    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct

    try:

        def _op(client: Any) -> dict[str, Any]:
            get_uidvalidity(client, folder)
            trash = _find_trash_folder(client)
            client.select_folder(folder)
            client.copy([uid], trash)
            client.add_flags([uid], [imapclient.DELETED])
            return {"status": "trashed", "uid": uid}

        result = await run_imap(acct, _op)
        return format_json(result)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)


async def set_flags(
    accounts: dict[str, AccountConfig],
    account_id: str,
    folder: str,
    uids: list[int],
    mark_read: bool | None = None,
    mark_flagged: bool | None = None,
) -> str:
    from mailbridge_mcp.tools_read import _get_account

    acct = _get_account(accounts, account_id)
    if isinstance(acct, str):
        return acct

    try:

        def _op(client: Any) -> dict[str, Any]:
            client.select_folder(folder)
            flags_set: list[str] = []

            if mark_read is True:
                client.add_flags(uids, [imapclient.SEEN])
                flags_set.append("\\Seen")
            elif mark_read is False:
                client.remove_flags(uids, [imapclient.SEEN])

            if mark_flagged is True:
                client.add_flags(uids, [imapclient.FLAGGED])
                flags_set.append("\\Flagged")
            elif mark_flagged is False:
                client.remove_flags(uids, [imapclient.FLAGGED])

            return {"status": "updated", "uids": uids, "flags_set": flags_set}

        result = await run_imap(acct, _op)
        return format_json(result)
    except Exception as e:
        return error_response("IMAP_CONNECTION_ERROR", str(e), account_id)

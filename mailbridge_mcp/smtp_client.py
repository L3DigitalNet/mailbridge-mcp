from __future__ import annotations

import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
from email_validator import EmailNotValidError, validate_email

from mailbridge_mcp.config import AccountConfig


class RateLimiter:
    """Sliding window rate limiter. Not thread-safe (single event loop is fine)."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []

    def check(self) -> bool:
        if self.max_per_minute == 0:
            return True
        now = time.monotonic()
        cutoff = now - 60.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


def validate_addresses(addresses: list[str]) -> None:
    """Validate email addresses. Raises ValueError on invalid addresses."""
    for addr in addresses:
        try:
            validate_email(addr, check_deliverability=False)
        except EmailNotValidError as e:
            raise ValueError(f"Invalid email address '{addr}': {e}") from e


def _build_mime(
    from_addr: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4()}@mailbridge-mcp>"
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.attach(MIMEText(body, "plain"))
    return msg


async def send_email(
    account: AccountConfig,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Build and send an email via SMTP. Returns {status, message_id}."""
    all_addrs = list(to) + (cc or []) + (bcc or [])
    validate_addresses(all_addrs)

    msg = _build_mime(
        from_addr=account.default_from,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        in_reply_to=in_reply_to,
        references=references,
    )

    recipients = list(to) + (cc or []) + (bcc or [])
    await aiosmtplib.send(
        msg,
        hostname=account.smtp.host,
        port=account.smtp.port,
        username=account.smtp.username,
        password=account.smtp.password,
        start_tls=account.smtp.starttls,
        timeout=timeout,
        recipients=recipients,
    )
    return {"status": "sent", "message_id": msg["Message-ID"]}

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Base ---


class AccountIdInput(BaseModel):
    account_id: str = Field(description="Account ID from imap_list_accounts")


# --- Read tool models ---


class ListFoldersInput(AccountIdInput):
    pass


class ListMessagesInput(AccountIdInput):
    folder: str = Field(default="INBOX", description="IMAP folder name")
    limit: int = Field(default=20, ge=1, le=100, description="Max messages to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    unread_only: bool = Field(default=False, description="Only return unread messages")
    sort_by: Literal["date_desc", "date_asc", "from", "subject"] = Field(
        default="date_desc", description="Sort order"
    )
    response_format: Literal["json", "markdown"] = Field(
        default="markdown", description="Response format"
    )


class GetMessageInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(description="Message UID from imap_list_messages")
    prefer_plain: bool = Field(default=True, description="Strip HTML to plain text via nh3")
    include_headers: bool = Field(default=False, description="Include raw email headers")
    response_format: Literal["json", "markdown"] = Field(default="json")


class SearchMessagesInput(AccountIdInput):
    folder: str = Field(default="INBOX", description="IMAP folder name")
    query: str = Field(
        default="", description="Maps to IMAP TEXT criterion (full message search)"
    )
    from_address: str | None = Field(default=None, description="Filter by sender")
    to_address: str | None = Field(default=None, description="Filter by recipient")
    subject: str | None = Field(default=None, description="Filter by subject")
    since_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    before_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    is_unread: bool | None = Field(default=None, description="Filter unread only")
    is_flagged: bool | None = Field(default=None, description="Filter flagged only")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    response_format: Literal["json", "markdown"] = Field(default="markdown")


class GetThreadInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(description="Any UID in the thread")
    limit: int = Field(default=20, ge=1, le=50, description="Max thread messages")
    offset: int = Field(default=0, ge=0)
    response_format: Literal["json", "markdown"] = Field(default="markdown")


# --- Write tool models ---


class SendEmailInput(AccountIdInput):
    to: list[str] = Field(description="Recipient email addresses")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Plain text body")
    cc: list[str] = Field(default_factory=list, description="CC recipients")
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    reply_to: str | None = Field(default=None, description="Reply-To address")


class ReplyInput(AccountIdInput):
    folder: str = Field(description="IMAP folder containing the message")
    uid: int = Field(description="UID of message being replied to")
    body: str = Field(description="Plain text reply body")
    reply_all: bool = Field(default=False, description="Reply to all recipients")
    include_original: bool = Field(default=True, description="Include original message text")


class MoveMessageInput(AccountIdInput):
    folder: str = Field(description="Source folder")
    uid: int = Field(description="Message UID")
    destination_folder: str = Field(description="Destination folder")


class DeleteMessageInput(AccountIdInput):
    folder: str = Field(description="Folder containing the message")
    uid: int = Field(description="Message UID")


class SetFlagsInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uids: list[int] = Field(description="One or more message UIDs")
    mark_read: bool | None = Field(default=None, description="Set read/unread")
    mark_flagged: bool | None = Field(default=None, description="Set flagged/unflagged")

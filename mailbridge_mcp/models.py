from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Folder names: alphanumeric start, then alphanumeric/dots/slashes/spaces/hyphens/underscores.
# Rejects IMAP glob chars (*, %), newlines, and other injection vectors.
_SAFE_FOLDER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9./ _-]{0,254}$")

# Header-safe string: no CR/LF (prevents email header injection)
_NO_CRLF_RE = re.compile(r"[\r\n]")


def _validate_folder_name(v: str) -> str:
    if not _SAFE_FOLDER_RE.match(v):
        raise ValueError(
            "Invalid folder name: must start with alphanumeric, "
            "contain only alphanumeric/.//_/- /space, max 255 chars"
        )
    return v


def _validate_no_crlf(v: str) -> str:
    if _NO_CRLF_RE.search(v):
        raise ValueError("Value must not contain CR or LF characters")
    return v


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

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class GetMessageInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(ge=1, description="Message UID from imap_list_messages")
    prefer_plain: bool = Field(default=True, description="Strip HTML to plain text via nh3")
    include_headers: bool = Field(default=False, description="Include raw email headers")
    response_format: Literal["json", "markdown"] = Field(default="json")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class SearchMessagesInput(AccountIdInput):
    folder: str = Field(default="INBOX", description="IMAP folder name")
    query: str = Field(
        default="", max_length=500,
        description="Maps to IMAP TEXT criterion (full message search)",
    )
    from_address: str | None = Field(default=None, max_length=254, description="Filter by sender")
    to_address: str | None = Field(default=None, max_length=254, description="Filter by recipient")
    subject: str | None = Field(default=None, max_length=500, description="Filter by subject")
    since_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    before_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    is_unread: bool | None = Field(default=None, description="Filter unread only")
    is_flagged: bool | None = Field(default=None, description="Filter flagged only")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    response_format: Literal["json", "markdown"] = Field(default="markdown")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class GetThreadInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uid: int = Field(ge=1, description="Any UID in the thread")
    limit: int = Field(default=20, ge=1, le=50, description="Max thread messages")
    offset: int = Field(default=0, ge=0)
    response_format: Literal["json", "markdown"] = Field(default="markdown")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


# --- Write tool models ---


class SendEmailInput(AccountIdInput):
    to: list[str] = Field(description="Recipient email addresses")
    subject: str = Field(max_length=998, description="Email subject")
    body: str = Field(description="Plain text body")
    cc: list[str] = Field(default_factory=list, description="CC recipients")
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    reply_to: str | None = Field(default=None, description="Reply-To address")

    @field_validator("subject")
    @classmethod
    def check_subject(cls, v: str) -> str:
        return _validate_no_crlf(v)

    @field_validator("reply_to")
    @classmethod
    def check_reply_to(cls, v: str | None) -> str | None:
        if v is not None:
            _validate_no_crlf(v)
        return v


class ReplyInput(AccountIdInput):
    folder: str = Field(description="IMAP folder containing the message")
    uid: int = Field(ge=1, description="UID of message being replied to")
    body: str = Field(description="Plain text reply body")
    reply_all: bool = Field(default=False, description="Reply to all recipients")
    include_original: bool = Field(default=True, description="Include original message text")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class MoveMessageInput(AccountIdInput):
    folder: str = Field(description="Source folder")
    uid: int = Field(ge=1, description="Message UID")
    destination_folder: str = Field(description="Destination folder")

    @field_validator("folder", "destination_folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class DeleteMessageInput(AccountIdInput):
    folder: str = Field(description="Folder containing the message")
    uid: int = Field(ge=1, description="Message UID")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)


class SetFlagsInput(AccountIdInput):
    folder: str = Field(description="IMAP folder name")
    uids: list[int] = Field(description="One or more message UIDs", max_length=1000)
    mark_read: bool | None = Field(default=None, description="Set read/unread")
    mark_flagged: bool | None = Field(default=None, description="Set flagged/unflagged")

    @field_validator("folder")
    @classmethod
    def check_folder(cls, v: str) -> str:
        return _validate_folder_name(v)

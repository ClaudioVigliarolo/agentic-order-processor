"""Read-only Gmail API client and email body parsing helpers."""

from __future__ import annotations

import base64
import logging
import re
import base64
from email.message import EmailMessage as MimeEmailMessage
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Iterator

from bs4 import BeautifulSoup
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailClientError(Exception):
    """Base exception for Gmail client failures."""


class GmailAuthenticationError(GmailClientError):
    """OAuth credentials are missing, invalid, or could not be refreshed."""


class GmailAPIError(GmailClientError):
    """A Gmail API request failed."""


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    body: str
    labels: list[str]
    rfc_message_id: str = ""


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeError, LookupError, ValueError):
        return value


def _decode_part_data(data: str | None) -> str:
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        return raw.decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        logger.debug("Could not decode a Gmail MIME part")
        return ""


def _walk_parts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield leaf MIME parts, tolerating missing or malformed subparts."""
    parts = payload.get("parts")
    if isinstance(parts, list) and parts:
        for part in parts:
            if isinstance(part, dict):
                yield from _walk_parts(part)
        return
    yield payload


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "head", "title", "meta", "noscript"]):
        node.decompose()
    text = soup.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", unescape(line)).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in _walk_parts(payload):
        mime_type = str(part.get("mimeType", "")).lower()
        data = (part.get("body") or {}).get("data") if isinstance(part.get("body"), dict) else None
        decoded = _decode_part_data(data)
        if not decoded:
            continue
        if mime_type == "text/plain":
            plain.append(decoded)
        elif mime_type == "text/html":
            html.append(decoded)
    if plain:
        return "\n".join(plain).strip()
    if html:
        return "\n".join(filter(None, (_html_to_text(item) for item in html))).strip()
    return ""


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = payload.get("headers", [])
    result: dict[str, str] = {}
    if isinstance(headers, list):
        for header in headers:
            if isinstance(header, dict) and header.get("name"):
                result[str(header["name"]).lower()] = _decode_header(header.get("value"))
    return result


def _format_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        return value


class GmailClient:
    """Gmail client for reading inbox messages and sending reviewed replies."""

    def __init__(
        self,
        credentials_file: str | Path = "credentials.json",
        token_file: str | Path = "token.json",
        service: Resource | Any | None = None,
    ) -> None:
        self.credentials_file = Path(credentials_file).expanduser()
        self.token_file = Path(token_file).expanduser()
        self._service = service

    def _load_credentials(self) -> Credentials:
        credentials: Credentials | None = None
        try:
            if self.token_file.exists():
                # Load the scopes actually granted in token.json. Passing SCOPES
                # here overwrites that metadata and can make an old read-only
                # token look like it already has the send permission.
                credentials = Credentials.from_authorized_user_file(str(self.token_file))
            if credentials and credentials.valid and credentials.has_scopes(SCOPES):
                return credentials
            if (
                credentials
                and credentials.expired
                and credentials.refresh_token
                and credentials.has_scopes(SCOPES)
            ):
                credentials.refresh(Request())
            else:
                if not self.credentials_file.is_file():
                    raise GmailAuthenticationError(
                        f"OAuth client file not found: {self.credentials_file}. "
                        "Download a Desktop OAuth client JSON and place it there."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
                credentials = flow.run_local_server(port=0)
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(credentials.to_json(), encoding="utf-8")
            return credentials
        except GmailAuthenticationError:
            raise
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise GmailAuthenticationError(
                f"Gmail OAuth failed ({type(exc).__name__}): {exc}"
            ) from exc

    def _get_service(self) -> Resource | Any:
        if self._service is not None:
            return self._service
        try:
            credentials = self._load_credentials()
            self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            return self._service
        except GmailAuthenticationError:
            raise
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise GmailAuthenticationError("Could not initialize Gmail authentication.") from exc

    def iter_inbox_messages(
        self, page_size: int = 20, max_results: int | None = None
    ) -> Iterator[EmailMessage]:
        """Yield inbox messages newest-first, following nextPageToken as needed."""
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        service = self._get_service()
        page_token: str | None = None
        emitted = 0
        while max_results is None or emitted < max_results:
            request_size = page_size if max_results is None else min(page_size, max_results - emitted)
            try:
                response = service.users().messages().list(
                    userId="me", labelIds=["INBOX"], maxResults=request_size, pageToken=page_token
                ).execute()
                refs = response.get("messages", [])
                for ref in refs:
                    if not isinstance(ref, dict) or not ref.get("id"):
                        logger.warning("Skipping malformed Gmail message reference")
                        continue
                    try:
                        raw = service.users().messages().get(
                            userId="me", id=ref["id"], format="full"
                        ).execute()
                        message = self._parse_message(raw)
                    except HttpError as exc:
                        raise GmailAPIError("Gmail failed while retrieving a message.") from exc
                    except (AttributeError, TypeError, ValueError, KeyError) as exc:
                        logger.warning("Skipping malformed Gmail message: %s", ref.get("id"))
                        continue
                    yield message
                    emitted += 1
                    if max_results is not None and emitted >= max_results:
                        return
                page_token = response.get("nextPageToken")
                if not page_token or not refs:
                    return
            except HttpError as exc:
                raise GmailAPIError("Gmail failed while listing inbox messages.") from exc

    def send_message(
        self, recipient: str, subject: str, body: str, *,
        thread_id: str = "", in_reply_to: str = "",
    ) -> str:
        """Send a plain text message and return Gmail's sent message ID."""
        message = MimeEmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        request_body: dict[str, str] = {"raw": raw}
        if thread_id:
            request_body["threadId"] = thread_id
        try:
            response = self._get_service().users().messages().send(
                userId="me", body=request_body
            ).execute()
        except HttpError as exc:
            raise GmailAPIError("Gmail failed while sending the reply.") from exc
        except GmailAuthenticationError:
            raise
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise GmailAuthenticationError("Could not authenticate with Gmail to send the reply.") from exc
        if not isinstance(response, dict) or not response.get("id"):
            raise GmailAPIError("Gmail did not confirm that the reply was sent.")
        return str(response["id"])

    @staticmethod
    def _parse_message(raw: dict[str, Any]) -> EmailMessage:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ValueError("Message has no ID")
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Message has no valid payload")
        headers = _header_map(payload)
        labels = raw.get("labelIds", [])
        return EmailMessage(
            message_id=str(raw["id"]),
            thread_id=str(raw.get("threadId", "")),
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            date=_format_date(headers.get("date", "")),
            body=_extract_body(payload),
            labels=[str(item) for item in labels] if isinstance(labels, list) else [],
            rfc_message_id=headers.get("message-id", ""),
        )


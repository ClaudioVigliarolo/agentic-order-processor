"""Command-line entry point for printing a short inbox preview."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .gmail_client import GmailAPIError, GmailAuthenticationError, GmailClient


def _preview(body: str, limit: int = 180) -> str:
    compact = " ".join(body.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    credentials_file = Path(os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"))
    token_file = Path(os.getenv("GMAIL_TOKEN_FILE", "token.json"))
    try:
        limit = int(os.getenv("GMAIL_MAX_RESULTS", "20"))
        if limit < 1:
            raise ValueError
    except ValueError:
        print("GMAIL_MAX_RESULTS must be a positive integer.", file=sys.stderr)
        return 2

    try:
        messages = list(GmailClient(credentials_file, token_file).iter_inbox_messages(page_size=limit, max_results=limit))
    except GmailAuthenticationError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 1
    except GmailAPIError as exc:
        print(f"Gmail API error: {exc}", file=sys.stderr)
        return 1

    if not messages:
        print("No inbox messages found.")
        return 0
    for index, message in enumerate(messages, 1):
        print(f"{index}. {message.subject or '(no subject)'}")
        print(f"   From: {message.sender or '(unknown sender)'}")
        print(f"   Date: {message.date or '(unknown date)'}")
        print(f"   Preview: {_preview(message.body) or '(empty body)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


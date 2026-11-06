"""Run the complete review-only order workflow for Gmail or sample messages."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agents import EmailClassifier, OrderExtractor
from .classification_cli import _load_sample_file
from .gmail_client import GmailAPIError, GmailAuthenticationError, GmailClient
from .workflow import OrderWorkflow


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Classify emails, check complete new orders, and draft replies for review."
    )
    parser.add_argument("--limit", type=int, default=20, help="maximum inbox emails (default: 20)")
    parser.add_argument("--sample-file", type=Path, help="use sample JSON instead of Gmail")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be a positive integer")
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Add it to .env or export it in your shell.", file=sys.stderr)
        return 2

    if args.sample_file:
        try:
            messages, _ = _load_sample_file(args.sample_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"Could not load sample emails: {exc}", file=sys.stderr)
            return 2
        messages = messages[: args.limit]
    else:
        try:
            messages = list(
                GmailClient(
                    os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"),
                    os.getenv("GMAIL_TOKEN_FILE", "token.json"),
                ).iter_inbox_messages(page_size=min(args.limit, 100), max_results=args.limit)
            )
        except GmailAuthenticationError as exc:
            print(f"Gmail authentication error: {exc}", file=sys.stderr)
            return 1
        except GmailAPIError as exc:
            print(f"Gmail API error: {exc}", file=sys.stderr)
            return 1

    if not messages:
        print("No messages found.")
        return 0

    workflow = OrderWorkflow(classifier=EmailClassifier(), extractor=OrderExtractor())
    for message in messages:
        result = workflow.process(message)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

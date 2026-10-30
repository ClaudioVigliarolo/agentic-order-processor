"""CLI for classifying inbox email and measuring accuracy with human labels."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agents import (
    EmailCategory,
    EmailClassifier,
    calculate_accuracy,
)
from .gmail_client import GmailAPIError, GmailAuthenticationError, GmailClient
from .gmail_client import EmailMessage
from .agents import OrderExtractor
from .workflow import classify_and_extract


def _load_sample_file(path: Path) -> tuple[list[EmailMessage], list[EmailCategory | None]]:
    raw_samples = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_samples, list):
        raise ValueError("sample file must contain a JSON array")

    messages: list[EmailMessage] = []
    expected: list[EmailCategory | None] = []
    for index, sample in enumerate(raw_samples, 1):
        if not isinstance(sample, dict):
            raise ValueError(f"sample {index} must be a JSON object")
        category_value = sample.get("expected_category")
        try:
            category = EmailCategory(category_value) if category_value is not None else None
        except ValueError as exc:
            raise ValueError(f"sample {index} has an invalid expected_category") from exc
        messages.append(
            EmailMessage(
                message_id=str(sample.get("id", f"sample_{index:03d}")),
                thread_id=str(sample.get("thread_id", "")),
                sender=str(sample.get("from", "")),
                subject=str(sample.get("subject", "")),
                date=str(sample.get("date", "")),
                body=str(sample.get("body", "")),
                labels=[],
            )
        )
        expected.append(category)
    return messages, expected


def _parse_label(value: str) -> EmailCategory | None:
    try:
        return EmailCategory(value.strip().lower())
    except ValueError:
        return None


def _get_expected_label(index: int, total: int) -> EmailCategory:
    values = ", ".join(category.value for category in EmailCategory)
    while True:
        value = input(f"Gold label for email {index}/{total} [{values}]: ")
        label = _parse_label(value)
        if label is not None:
            return label
        print("Enter one of the listed labels.", file=sys.stderr)


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Classify recent Gmail inbox messages.")
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="ask for a human gold label for each of 20 emails and report accuracy",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="maximum messages to classify (default: 20)"
    )
    parser.add_argument(
        "--sample-file",
        type=Path,
        help="classify JSON email samples instead of reading Gmail",
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be a positive integer")
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Add it to .env or export it in your shell.", file=sys.stderr)
        return 2

    expected_labels: list[EmailCategory | None] = []
    if args.sample_file:
        try:
            messages, expected_labels = _load_sample_file(args.sample_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"Could not load sample emails: {exc}", file=sys.stderr)
            return 2
        if args.evaluate and any(label is None for label in expected_labels):
            print(
                "Evaluation needs an expected_category for every sample in the JSON file.",
                file=sys.stderr,
            )
            return 2
        messages = messages[: args.limit]
        expected_labels = expected_labels[: args.limit]
    else:
        credentials_file = Path(os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"))
        token_file = Path(os.getenv("GMAIL_TOKEN_FILE", "token.json"))
        try:
            messages = list(
                GmailClient(credentials_file, token_file).iter_inbox_messages(
                    page_size=min(args.limit, 100), max_results=args.limit
                )
            )
        except GmailAuthenticationError as exc:
            print(f"Gmail authentication error: {exc}", file=sys.stderr)
            return 1
        except GmailAPIError as exc:
            print(f"Gmail API error: {exc}", file=sys.stderr)
            return 1
    if not messages:
        print("No inbox messages found.")
        return 0

    classifier = EmailClassifier()
    extractor = OrderExtractor()
    predictions: list[EmailCategory] = []
    expected: list[EmailCategory] = []
    try:
        for index, message in enumerate(messages, 1):
            classification, record = classify_and_extract(message, classifier, extractor)
            predictions.append(classification.category)
            record["subject"] = message.subject
            record["sender"] = message.sender
            print(json.dumps(record, ensure_ascii=False))
            if args.evaluate:
                if args.sample_file:
                    expected_label = expected_labels[index - 1]
                    assert expected_label is not None
                else:
                    expected_label = _get_expected_label(index, len(messages))
                expected.append(expected_label)
                print(
                    f"  {'correct' if expected_label is classification.category else 'incorrect'}: "
                    f"predicted={classification.category.value}, expected={expected_label.value}"
                )
    except (KeyboardInterrupt, EOFError):
        print("\nEvaluation stopped before all emails were labeled.", file=sys.stderr)
        return 130
    except Exception:
        print(
            "Classification failed. Check the API key, model setting, network, and SDK installation.",
            file=sys.stderr,
        )
        return 1

    if args.evaluate:
        accuracy = calculate_accuracy(predictions, expected)
        print(f"Accuracy: {accuracy:.1%} ({sum(p is e for p, e in zip(predictions, expected))}/{len(expected)})")
        print("Per-class results:")
        for category in EmailCategory:
            total = sum(label is category for label in expected)
            correct = sum(p is e and e is category for p, e in zip(predictions, expected))
            if total:
                print(f"  {category.value}: {correct}/{total} correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

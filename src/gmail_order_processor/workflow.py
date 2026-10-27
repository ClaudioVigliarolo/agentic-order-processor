"""Connect classification to order extraction for new-order messages."""

from __future__ import annotations

from typing import Any

from .classification import (
    EmailCategory,
    EmailClassification,
    EmailClassifier,
    classification_record,
)
from .gmail_client import EmailMessage
from .order_extraction import OrderExtractor


def classify_and_extract(
    message: EmailMessage,
    classifier: EmailClassifier,
    extractor: OrderExtractor,
) -> tuple[EmailClassification, dict[str, Any]]:
    """Classify an email and extract order fields only for new orders."""
    classification = classifier.classify(message)
    record = classification_record(message, classification)
    if classification.category is EmailCategory.NEW_ORDER:
        record["order_extraction"] = extractor.extract(message).model_dump(mode="json")
    return classification, record

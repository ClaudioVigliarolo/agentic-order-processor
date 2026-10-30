"""Compatibility exports for classification code now grouped in ``agents``."""

from .agents.classifier import (
    EmailCategory,
    EmailClassification,
    EmailClassifier,
    calculate_accuracy,
    classification_record,
    workflow_route,
)

__all__ = [
    "EmailCategory",
    "EmailClassification",
    "EmailClassifier",
    "calculate_accuracy",
    "classification_record",
    "workflow_route",
]

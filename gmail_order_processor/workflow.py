"""Safe orchestration for classifying, extracting, and checking an order email."""

from __future__ import annotations

from enum import Enum
from email.utils import parseaddr
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from .agents import (
    CustomerReply,
    EmailCategory,
    EmailClassification,
    EmailClassifier,
    InventoryAgent,
    InventoryCheck,
    InventoryItemStatus,
    OrderExtraction,
    OrderExtractor,
    ResponseAgent,
)
from .agents.classifier import workflow_route
from .gmail_client import EmailMessage
from .orders import (
    DuplicateOrderError,
    InsufficientStockError,
    Order,
    OrderItemRequest,
    OrderRepository,
    OrderRepositoryError,
    ReviewCase,
)


class WorkflowStatus(str, Enum):
    IGNORED = "ignored"
    HUMAN_REVIEW = "human_review"
    HUMAN_REVIEW_ERROR = "human_review_error"
    NEEDS_CUSTOMER_INFORMATION = "needs_customer_information"
    DRAFT_CREATED = "draft_created"
    DUPLICATE_ORDER = "duplicate_order"


class WorkflowResult(BaseModel):
    message_id: str
    category: EmailCategory | None = None
    confidence: float | None = None
    status: WorkflowStatus
    rationale: str | None = None
    order_extraction: OrderExtraction | None = None
    inventory: InventoryCheck | None = None
    order: Order | None = None
    draft_reply: CustomerReply | None = None
    error: str | None = None


class OrderWorkflow:
    """Run one message through the safe, sequential order-processing workflow."""

    def __init__(
        self,
        classifier: EmailClassifier | Any | None = None,
        extractor: OrderExtractor | Any | None = None,
        inventory_agent: InventoryAgent | Any | None = None,
        response_agent: ResponseAgent | Any | None = None,
        order_repository: OrderRepository | Any | None = None,
    ) -> None:
        self.classifier = classifier or EmailClassifier()
        self.extractor = extractor or OrderExtractor()
        self.inventory_agent = inventory_agent or InventoryAgent()
        self.response_agent = response_agent or ResponseAgent()
        self.order_repository = order_repository or OrderRepository()

    def process(self, message: EmailMessage) -> WorkflowResult:
        result = self._process(message)
        if result.status in {
            WorkflowStatus.HUMAN_REVIEW,
            WorkflowStatus.HUMAN_REVIEW_ERROR,
            WorkflowStatus.NEEDS_CUSTOMER_INFORMATION,
        }:
            try:
                self.order_repository.save_review_case(ReviewCase(
                    message_id=message.message_id,
                    category=result.category.value if result.category else None,
                    status=result.status.value,
                    confidence=result.confidence,
                    rationale=result.rationale,
                    sender=message.sender,
                    subject=message.subject,
                    email_date=message.date,
                    email_body=message.body,
                    extraction=result.order_extraction.model_dump(mode="json") if result.order_extraction else None,
                    inventory=result.inventory.model_dump(mode="json").get("items") if result.inventory else None,
                    reply_subject=result.draft_reply.subject if result.draft_reply else None,
                    reply_body=result.draft_reply.body if result.draft_reply else None,
                    error=result.error,
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
            except Exception:
                # A processing result remains available even if review persistence fails.
                pass
        return result

    def _process(self, message: EmailMessage) -> WorkflowResult:
        # Fast idempotency check: an already-seen Gmail message never re-enters
        # the AI or inventory steps. The DB unique constraint covers races too.
        try:
            existing_order = self.order_repository.get_order_by_message_id(
                message.message_id
            )
        except Exception:
            return WorkflowResult(
                message_id=message.message_id,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Order storage could not be read; processing was stopped.",
            )
        if existing_order is not None:
            return WorkflowResult(
                message_id=message.message_id,
                category=EmailCategory.NEW_ORDER,
                status=WorkflowStatus.DUPLICATE_ORDER,
                order=existing_order,
            )

        try:
            classification = self.classifier.classify(message)
        except Exception:
            return WorkflowResult(
                message_id=message.message_id,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Classification failed; later workflow steps were skipped.",
            )

        base = {
            "message_id": message.message_id,
            "category": classification.category,
            "confidence": classification.confidence,
            "rationale": classification.rationale,
        }
        if classification.category is EmailCategory.UNRELATED:
            return WorkflowResult(**base, status=WorkflowStatus.IGNORED)
        if classification.category is EmailCategory.UNCERTAIN:
            return WorkflowResult(**base, status=WorkflowStatus.HUMAN_REVIEW)

        # Product inquiries and cancellations are not new orders. Draft only a
        # neutral acknowledgement and route it for human review.
        if classification.category in (EmailCategory.PRODUCT_INQUIRY, EmailCategory.CANCELLATION):
            return self._draft_reply(
                base,
                status=WorkflowStatus.HUMAN_REVIEW,
                context={
                    "category": classification.category.value,
                    "subject": message.subject,
                    "sender": message.sender,
                    "email_body": message.body,
                    "review_required": True,
                    "missing_information": [],
                    "inventory": [],
                },
            )

        # Keep extraction explicitly limited to new-order messages. If another
        # category is added later, it will stop here instead of entering ordering.
        if classification.category is not EmailCategory.NEW_ORDER:
            return WorkflowResult(**base, status=WorkflowStatus.HUMAN_REVIEW)

        try:
            extraction = self.extractor.extract(message)
        except Exception:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Order extraction failed; inventory and reply drafting were skipped.",
            )

        base["order_extraction"] = extraction
        missing = list(extraction.missing_information)
        if not extraction.products:
            missing.append("requested products")
        if not extraction.delivery_address:
            missing.append("delivery address")
        customer_email = parseaddr(message.sender)[1].strip()
        if not customer_email:
            missing.append("customer email address")
        if any(product.quantity is None for product in extraction.products):
            missing.append("quantity for each requested product")
        missing = list(dict.fromkeys(missing))
        if missing:
            return self._draft_reply(
                base,
                status=WorkflowStatus.NEEDS_CUSTOMER_INFORMATION,
                context={
                    "category": classification.category.value,
                    "subject": message.subject,
                    "sender": message.sender,
                    "order": extraction.model_dump(mode="json"),
                    "missing_information": missing,
                    "inventory": [],
                },
            )

        try:
            inventory = self.inventory_agent.check(extraction)
        except Exception:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Inventory lookup failed; a reply was not drafted.",
            )
        base["inventory"] = inventory
        inventory_data = inventory.model_dump(mode="json")["items"]
        if any(item.status is not InventoryItemStatus.AVAILABLE for item in inventory.items):
            return self._draft_reply(
                base,
                status=WorkflowStatus.HUMAN_REVIEW,
                context={
                    "category": classification.category.value,
                    "subject": message.subject,
                    "sender": message.sender,
                    "order": extraction.model_dump(mode="json"),
                    "missing_information": [],
                    "inventory": inventory_data,
                    "review_required": True,
                },
            )

        try:
            order = self.order_repository.create_draft_order(
                gmail_message_id=message.message_id,
                customer_email=customer_email,
                delivery_address=extraction.delivery_address or "",
                products=[
                    OrderItemRequest(sku=item.sku or "", quantity=item.quantity)
                    for item in inventory.items
                ],
            )
        except DuplicateOrderError:
            existing_order = self.order_repository.get_order_by_message_id(
                message.message_id
            )
            return WorkflowResult(
                **base,
                status=WorkflowStatus.DUPLICATE_ORDER,
                order=existing_order,
            )
        except InsufficientStockError:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW,
                error="Stock changed after the inventory check; no draft order was created.",
            )
        except OrderRepositoryError:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Draft order could not be saved; no reply was drafted.",
            )
        except Exception:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Draft order could not be saved; no reply was drafted.",
            )

        base["order"] = order
        return self._draft_reply(
            base,
            status=WorkflowStatus.DRAFT_CREATED,
            context={
                "category": classification.category.value,
                "subject": message.subject,
                "sender": message.sender,
                "order": extraction.model_dump(mode="json"),
                "missing_information": [],
                "inventory": inventory_data,
                "review_required": True,
                "draft_order_id": order.id,
                "order_status": order.status.value,
            },
        )

    def _draft_reply(
        self, base: dict[str, Any], *, status: WorkflowStatus, context: dict[str, Any]
    ) -> WorkflowResult:
        try:
            reply = self.response_agent.draft(context)
        except Exception:
            return WorkflowResult(
                **base,
                status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                error="Reply drafting failed; nothing was sent.",
            )
        if "order" in base:
            try:
                base["order"] = self.order_repository.save_draft_response(
                    base["order"].id,
                    subject=reply.subject,
                    body=reply.body,
                )
            except Exception:
                return WorkflowResult(
                    **base,
                    status=WorkflowStatus.HUMAN_REVIEW_ERROR,
                    error="The draft order was saved, but its reply draft could not be stored.",
                )
        return WorkflowResult(**base, status=status, draft_reply=reply)


def classify_and_extract(
    message: EmailMessage,
    classifier: EmailClassifier,
    extractor: OrderExtractor,
) -> tuple[EmailClassification, dict[str, Any]]:
    """Backward-compatible phase-2 helper retained for classification evaluation."""
    classification = classifier.classify(message)
    record: dict[str, Any] = {
        "message_id": message.message_id,
        "category": classification.category.value,
        "confidence": classification.confidence,
        "rationale": classification.rationale,
        "route": workflow_route(classification.category),
    }
    if classification.category is EmailCategory.NEW_ORDER:
        record["order_extraction"] = extractor.extract(message).model_dump(mode="json")
    return classification, record

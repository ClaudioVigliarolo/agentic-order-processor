from gmail_order_processor.agents import (
    CustomerReply,
    EmailCategory,
    EmailClassification,
    OrderExtraction,
    RequestedProduct,
)
from gmail_order_processor.agents.inventory_agent import InventoryAgent
from gmail_order_processor.gmail_client import EmailMessage
from gmail_order_processor.inventory import ProductRepository
from gmail_order_processor.orders import OrderRepository, OrderStatus
from gmail_order_processor.workflow import OrderWorkflow, WorkflowStatus


def test_workflow_creates_one_draft_for_a_new_order_and_skips_duplicate(tmp_path):
    database = tmp_path / "workflow.sqlite3"
    products = ProductRepository(database)
    products.seed_demo_products()

    class FixedClassifier:
        calls = 0

        def classify(self, _message):
            self.calls += 1
            return EmailClassification(
                category=EmailCategory.NEW_ORDER,
                confidence=0.99,
                rationale="The customer requests a product.",
            )

    class FixedExtractor:
        calls = 0

        def extract(self, _message):
            self.calls += 1
            return OrderExtraction(
                products=[RequestedProduct(name="Casa Round Dining Table", quantity=2)],
                delivery_address="Via Roma 10, Rome",
            )

    class FixedResponseAgent:
        def draft(self, _context):
            return CustomerReply(
                subject="We received your request",
                body="Your request is ready for review.",
            )

    classifier = FixedClassifier()
    extractor = FixedExtractor()
    order_repository = OrderRepository(database)
    workflow = OrderWorkflow(
        classifier=classifier,
        extractor=extractor,
        inventory_agent=InventoryAgent(repository=products),
        response_agent=FixedResponseAgent(),
        order_repository=order_repository,
    )
    message = EmailMessage(
        message_id="gmail-message-123",
        thread_id="thread-123",
        sender="Ada Lovelace <ada@example.com>",
        subject="Two dining tables",
        date="2026-10-31T10:00:00+00:00",
        body="Please order two Casa Round Dining Tables to Via Roma 10, Rome.",
        labels=["INBOX"],
    )

    result = workflow.process(message)

    assert result.status is WorkflowStatus.DRAFT_CREATED
    assert result.order is not None
    assert result.order.status is OrderStatus.DRAFT
    assert result.order.customer_email == "ada@example.com"
    assert result.order.items[0].quantity == 2
    assert products.get_by_sku("FUR-TBL-001").stock_quantity == 7

    duplicate = workflow.process(message)

    assert duplicate.status is WorkflowStatus.DUPLICATE_ORDER
    assert duplicate.order is not None
    assert duplicate.order.id == result.order.id
    assert len(order_repository.list_orders()) == 1
    assert classifier.calls == 1
    assert extractor.calls == 1

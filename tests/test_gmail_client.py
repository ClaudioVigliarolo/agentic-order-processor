import base64
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from gmail_order_processor.gmail_client import (
    GmailAPIError,
    GmailClient,
    _extract_body,
)
from gmail_order_processor.classification import (
    EmailCategory,
    EmailClassification,
    EmailClassifier,
    calculate_accuracy,
    classification_record,
)
from gmail_order_processor.classification_cli import _load_sample_file
from gmail_order_processor.order_extraction import (
    OrderExtraction,
    OrderExtractor,
    ProductVariants,
    RequestedProduct,
)
from gmail_order_processor.workflow import classify_and_extract
from gmail_order_processor.inventory import ProductRepository


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_prefers_plain_text_and_decodes_unicode():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": encoded("<p>HTML</p>")}},
            {"mimeType": "text/plain", "body": {"data": encoded("Ciao 🌱")}},
        ],
    }
    assert _extract_body(payload) == "Ciao 🌱"


def test_converts_html_when_plain_text_missing():
    payload = {"mimeType": "text/html", "body": {"data": encoded("<p>Hello <b>world</b></p><script>x</script>")}}
    assert _extract_body(payload) == "Hello\nworld"


def test_empty_or_malformed_body_is_safe():
    assert _extract_body({"mimeType": "text/plain", "body": {}}) == ""
    assert _extract_body({"mimeType": "multipart/mixed", "parts": [None, {"body": None}]}) == ""


def test_parses_structured_message():
    raw = {
        "id": "abc",
        "threadId": "thread-1",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Ada <ada@example.com>"},
                {"name": "Subject", "value": "=?utf-8?b?UHJvZHVjdOKckw==?="},
                {"name": "Date", "value": "Mon, 01 Jan 2024 12:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": encoded("Hi")},
        },
    }
    message = GmailClient._parse_message(raw)
    assert (message.message_id, message.thread_id, message.sender) == ("abc", "thread-1", "Ada <ada@example.com>")
    assert message.subject == "Product✓"
    assert message.body == "Hi"
    assert message.labels == ["INBOX", "UNREAD"]
    assert message.date.startswith("2024-01-01T12:00:00")


def _response(value):
    request = Mock()
    request.execute.return_value = value
    return request


def test_fetches_latest_page_and_reads_full_messages():
    service = Mock()
    service.users.return_value.messages.return_value.list.return_value = _response(
        {"messages": [{"id": "m1"}]}
    )
    service.users.return_value.messages.return_value.get.return_value = _response(
        {"id": "m1", "threadId": "t1", "payload": {"mimeType": "text/plain", "body": {"data": encoded("Body")}}}
    )
    result = list(GmailClient(service=service).iter_inbox_messages(max_results=20))
    assert len(result) == 1
    service.users.return_value.messages.return_value.list.assert_called_once_with(
        userId="me", labelIds=["INBOX"], maxResults=20, pageToken=None
    )
    service.users.return_value.messages.return_value.get.assert_called_once_with(
        userId="me", id="m1", format="full"
    )


def test_follows_pagination_tokens():
    service = Mock()
    listing = service.users.return_value.messages.return_value.list
    listing.side_effect = [_response({"messages": [{"id": "a"}], "nextPageToken": "next"}), _response({"messages": [{"id": "b"}]})]
    getter = service.users.return_value.messages.return_value.get
    getter.side_effect = [
        _response({"id": "a", "payload": {}}),
        _response({"id": "b", "payload": {}}),
    ]
    result = list(GmailClient(service=service).iter_inbox_messages(page_size=1, max_results=2))
    assert [message.message_id for message in result] == ["a", "b"]
    assert listing.call_args_list[1].kwargs["pageToken"] == "next"


def test_gmail_api_errors_are_sanitized():
    service = Mock()
    error = HttpError(Response({"status": "403"}), b"sensitive body")
    service.users.return_value.messages.return_value.list.return_value.execute.side_effect = error
    with pytest.raises(GmailAPIError, match="listing inbox"):
        list(GmailClient(service=service).iter_inbox_messages())




def test_classification_uses_structured_output_and_routes_unrelated():
    message = GmailClient._parse_message(
        {"id": "m-1", "payload": {"headers": [{"name": "Subject", "value": "Hello"}]}}
    )
    output = EmailClassification(
        category=EmailCategory.UNRELATED, confidence=0.98, rationale="A personal greeting."
    )
    calls = []

    def fake_runner(agent, prompt):
        calls.append((agent, prompt))
        return SimpleNamespace(final_output=output)

    classification = EmailClassifier(runner=fake_runner).classify(message)
    assert classification is output
    assert '"subject": "Hello"' in calls[0][1]
    assert classification_record(message, classification)["route"] == "ignored"


@pytest.mark.parametrize(
    ("category", "route"),
    [
        (EmailCategory.NEW_ORDER, "order_workflow"),
        (EmailCategory.PRODUCT_INQUIRY, "order_workflow"),
        (EmailCategory.CANCELLATION, "order_workflow"),
        (EmailCategory.UNRELATED, "ignored"),
        (EmailCategory.UNCERTAIN, "human_review"),
    ],
)
def test_all_categories_have_expected_routes(category, route):
    from gmail_order_processor.classification import workflow_route

    assert workflow_route(category) == route


def test_accuracy_calculation_requires_gold_labels():
    assert calculate_accuracy(
        [EmailCategory.NEW_ORDER, EmailCategory.UNRELATED],
        [EmailCategory.NEW_ORDER, EmailCategory.UNCERTAIN],
    ) == 0.5
    with pytest.raises(ValueError, match="equal lengths"):
        calculate_accuracy([EmailCategory.NEW_ORDER], [])


def test_sample_email_fixture_has_20_gold_labels():
    fixture = Path(__file__).parents[1] / "sample_emails.json"
    messages, expected = _load_sample_file(fixture)
    assert len(messages) == 20
    assert all(label is not None for label in expected)
    assert messages[0].message_id == "email_001"
    assert expected[0] is EmailCategory.NEW_ORDER
    assert expected[-1] is EmailCategory.UNCERTAIN


def test_order_extractor_returns_structured_product_details():
    message = GmailClient._parse_message(
        {
            "id": "order-1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Chairs"}],
                "mimeType": "text/plain",
                "body": {"data": encoded("Please order 10 blue chairs to Rome.")},
            },
        }
    )
    expected = OrderExtraction(
        products=[
            RequestedProduct(
                name="chairs",
                quantity=10,
                variants=ProductVariants(color="blue"),
            )
        ],
        delivery_address="Rome",
        missing_information=[],
    )
    calls = []

    def fake_runner(agent, prompt):
        calls.append((agent, prompt))
        return SimpleNamespace(final_output=expected)

    extracted = OrderExtractor(runner=fake_runner).extract(message)
    assert extracted is expected
    assert "Please order 10 blue chairs to Rome." in calls[0][1]
    assert calls[0][0].name == "Order Extraction Agent"


def test_workflow_extracts_details_only_for_new_orders():
    message = GmailClient._parse_message({"id": "m-2", "payload": {"headers": []}})
    extraction = OrderExtraction(
        products=[RequestedProduct(name="desk", quantity=3)],
        delivery_address=None,
        missing_information=["delivery address"],
    )

    class FakeClassifier:
        category = EmailCategory.UNRELATED

        def classify(self, _message):
            return EmailClassification(
                category=self.category, confidence=0.9, rationale="test result"
            )

    class FakeExtractor:
        calls = 0

        def extract(self, _message):
            self.calls += 1
            return extraction

    classifier = FakeClassifier()
    extractor = FakeExtractor()
    _, unrelated_record = classify_and_extract(message, classifier, extractor)
    assert "order_extraction" not in unrelated_record
    assert extractor.calls == 0

    classifier.category = EmailCategory.NEW_ORDER
    _, order_record = classify_and_extract(message, classifier, extractor)
    assert order_record["order_extraction"]["products"][0]["quantity"] == 3
    assert order_record["order_extraction"]["missing_information"] == ["delivery address"]
    assert extractor.calls == 1


def test_product_database_seeds_fictional_catalog_idempotently(tmp_path):
    repository = ProductRepository(tmp_path / "catalog" / "products.sqlite3")
    assert repository.seed_demo_products() == 15
    products = repository.list_products()
    assert len(products) == 15
    assert repository.seed_demo_products() == 0
    chair = repository.get_by_sku("FUR-CHR-001")
    assert chair is not None
    assert chair.name == "Nordic Oak Dining Chair"
    assert chair.price_euros == 129
    assert chair.stock_quantity == 24


def test_product_database_migrates_cents_to_whole_euros(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT NOT NULL UNIQUE,
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0)
            )
            """
        )
        connection.executemany(
            "INSERT INTO products (name, sku, price_cents, stock_quantity) VALUES (?, ?, ?, ?)",
            [("Chair", "OLD-001", 12349, 2), ("Desk", "OLD-002", 12350, 3)],
        )

    products = ProductRepository(database).list_products()
    assert [product.price_euros for product in products] == [123, 124]

